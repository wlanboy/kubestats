"""Tests for the per-namespace counting in kubectl.py, against fake API classes."""
from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from kubernetes.client.exceptions import ApiException

from kubestats import kubectl
from kubestats.models import CliTool

_FULL_LIMITS = {"cpu": "500m", "memory": "256Mi"}


def _api_error(status: int, reason: str = "Error") -> ApiException:
    return ApiException(status=status, reason=reason)


def _page(items: list[Any]) -> Any:
    return SimpleNamespace(items=list(items), metadata=SimpleNamespace(_continue=None))


class _FakeApi:
    """Stands in for a kubernetes API class.

    `on(method, result)` configures a list method. `result` is a list of
    items, an exception, or a callable that gets the call's keyword arguments
    and returns or raises one of those. Unconfigured methods return no items.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._methods: dict[str, Any] = {}

    def on(self, method: str, result: Any) -> None:
        self._methods[method] = result

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)

        def method(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            result: Any = self._methods.get(name, [])
            if callable(result):
                result = result(**kwargs)
            if isinstance(result, Exception):
                raise result
            return _page(result)
        return method


_API_CLASSES = ("CoreV1Api", "AppsV1Api", "AutoscalingV2Api", "NetworkingV1Api",
                "PolicyV1Api", "CustomObjectsApi", "ApiextensionsV1Api")


@pytest.fixture()
def apis(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeApi]:
    fakes = {name: _FakeApi() for name in _API_CLASSES}
    for name, fake in fakes.items():
        monkeypatch.setattr(kubectl.client, name, lambda fake=fake: fake)
    monkeypatch.setattr(kubectl, "_backend", CliTool.kubectl)
    return fakes


def _meta(namespace: str, name: str = "x") -> Any:
    return SimpleNamespace(namespace=namespace, name=name, annotations=None)


def _obj(namespace: str, name: str = "x") -> dict[str, Any]:
    return {"metadata": {"namespace": namespace, "name": name}}


def _typed(namespace: str, name: str = "x", **spec: Any) -> Any:
    return SimpleNamespace(metadata=_meta(namespace, name), spec=SimpleNamespace(**spec))


def _container(name: str = "app", *, probes: bool = True, liveness: bool | None = None,
               limits: dict[str, str] | None = None) -> Any:
    return SimpleNamespace(
        name=name,
        liveness_probe=object() if (probes if liveness is None else liveness) else None,
        readiness_probe=object() if probes else None,
        resources=SimpleNamespace(limits=limits),
    )


def _pod(namespace: str, *containers: Any) -> Any:
    return _typed(namespace, containers=list(containers), init_containers=None)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

class TestHasLimits:
    def test_cpu_and_memory_on_every_container(self) -> None:
        pod = _pod("a", _container(limits=_FULL_LIMITS),
                   _container("istio-proxy", limits=_FULL_LIMITS))
        assert kubectl._has_limits(pod)

    def test_missing_memory_limit(self) -> None:
        assert not kubectl._has_limits(_pod("a", _container(limits={"cpu": "1"})))

    def test_one_container_without_resources(self) -> None:
        bare = SimpleNamespace(name="sidecar", resources=None)
        assert not kubectl._has_limits(_pod("a", _container(limits=_FULL_LIMITS), bare))

    def test_requests_only(self) -> None:
        assert not kubectl._has_limits(_pod("a", _container(limits=None)))


# ---------------------------------------------------------------------------
# Adoption
# ---------------------------------------------------------------------------

def _custom_by_plural(**by_plural: Any) -> Callable[..., Any]:
    """Handler for CustomObjectsApi list calls, keyed by plural (default: no items)."""
    def handler(*, plural: str, **kwargs: Any) -> Any:
        result = by_plural.get(plural, [])
        return result(**kwargs) if callable(result) else result
    return handler


class TestGetAdoptionStats:
    @pytest.fixture()
    def pods(self, apis: dict[str, _FakeApi]) -> None:
        apis["CoreV1Api"].on("list_pod_for_all_namespaces", [
            # limits and probes
            _pod("a", _container(limits=_FULL_LIMITS)),
            # probes, but no memory limit
            _pod("a", _container(limits={"cpu": "1"})),
            # limits, app without liveness probe
            _pod("a", _container(liveness=False, limits=_FULL_LIMITS),
                 _container("istio-proxy", liveness=False, limits=_FULL_LIMITS)),
            # limits and probes; istio-proxy has no liveness probe
            _pod("a", _container(limits=_FULL_LIMITS),
                 _container("istio-proxy", liveness=False, limits=_FULL_LIMITS)),
            _pod("other", _container(limits=_FULL_LIMITS)),
        ])

    @pytest.mark.usefixtures("pods")
    def test_counts_pods_with_limits_and_probes(self) -> None:
        a, b = kubectl.get_adoption_stats(["a", "b"])
        assert (a.namespace, a.pod_count, a.pods_with_limits, a.pods_with_probes) == ("a", 4, 3, 3)
        assert (b.namespace, b.pod_count, b.pods_with_limits, b.pods_with_probes) == ("b", 0, 0, 0)

    @pytest.mark.usefixtures("pods")
    def test_probe_ignore_empty_checks_sidecars(self) -> None:
        a, _ = kubectl.get_adoption_stats(["a", "b"], probe_ignore=())
        assert a.pods_with_probes == 2

    def test_hpa_counts_only_targets_of_existing_deployments(
            self, apis: dict[str, _FakeApi]) -> None:
        apis["AppsV1Api"].on("list_deployment_for_all_namespaces", [
            _typed("a", "web"), _typed("a", "api"), _typed("b", "api"),
        ])
        def hpa(ns: str, target: str) -> Any:
            return _typed(ns, scale_target_ref=SimpleNamespace(name=target))
        apis["AutoscalingV2Api"].on("list_horizontal_pod_autoscaler_for_all_namespaces", [
            hpa("a", "web"),
            hpa("a", "web"),      # second HPA on the same target counts once
            hpa("a", "deleted"),  # target deployment no longer exists
            hpa("c", "api"),      # other namespace
        ])

        a, b = kubectl.get_adoption_stats(["a", "b"])
        assert (a.deployment_count, a.hpa_count) == (2, 1)
        assert (b.deployment_count, b.hpa_count) == (1, 0)

    def test_core_failure_skips_only_that_namespace(self, apis: dict[str, _FakeApi]) -> None:
        def per_namespace(*, namespace: str, **_: Any) -> Any:
            return _api_error(403, "Forbidden") if namespace == "locked" else []
        apis["NetworkingV1Api"].on("list_network_policy_for_all_namespaces",
                                   _api_error(403, "Forbidden"))
        apis["NetworkingV1Api"].on("list_namespaced_network_policy", per_namespace)

        stats = kubectl.get_adoption_stats(["a", "locked"])
        assert [s.namespace for s in stats] == ["a"]

    def test_optional_metric_failure_is_unknown(self, apis: dict[str, _FakeApi]) -> None:
        apis["PolicyV1Api"].on("list_pod_disruption_budget_for_all_namespaces",
                               _api_error(500, "Internal"))
        a = kubectl.get_adoption_stats(["a", "b"])[0]
        assert a.pdb_count is None
        assert a.fetch_errors == {"pdb_count": "500 Internal"}
        assert a.vpa_count == 0

    def test_flux_sums_both_kinds_with_version_fallback(
            self, apis: dict[str, _FakeApi]) -> None:
        def helmreleases(*, version: str, **_: Any) -> Any:
            if version == "v2":
                return _api_error(404)
            return [_obj("a"), _obj("b")]
        apis["CustomObjectsApi"].on("list_cluster_custom_object", _custom_by_plural(
            helmreleases=helmreleases,
            kustomizations=[_obj("a")],
            applications=[_obj("b"), _obj("b")],
        ))

        a, b = kubectl.get_adoption_stats(["a", "b"])
        assert (a.flux_resources, a.argocd_resources) == (2, 0)
        assert (b.flux_resources, b.argocd_resources) == (1, 2)
        versions = [kw["version"] for name, kw in apis["CustomObjectsApi"].calls
                    if kw.get("plural") == "helmreleases"]
        assert versions == ["v2", "v2beta2"]

    def test_crd_not_installed_counts_as_zero(self, apis: dict[str, _FakeApi]) -> None:
        apis["CustomObjectsApi"].on("list_cluster_custom_object", _api_error(404))
        a, _ = kubectl.get_adoption_stats(["a", "b"])
        assert (a.vpa_count, a.flux_resources, a.argocd_resources) == (0, 0, 0)
        assert a.fetch_errors == {}

    def test_openshift_resources_only_in_oc_mode(
            self, apis: dict[str, _FakeApi], monkeypatch: pytest.MonkeyPatch) -> None:
        apis["CustomObjectsApi"].on("list_cluster_custom_object", _custom_by_plural(
            routes=[_obj("a"), _obj("a")],
            deploymentconfigs=_api_error(403, "Forbidden"),
        ))
        apis["CustomObjectsApi"].on("list_namespaced_custom_object", _custom_by_plural(
            deploymentconfigs=_api_error(403, "Forbidden"),
        ))

        a, _ = kubectl.get_adoption_stats(["a", "b"])
        assert (a.route_count, a.deploymentconfig_count) == (0, 0)
        assert not any(kw.get("plural") == "routes"
                       for _, kw in apis["CustomObjectsApi"].calls)

        monkeypatch.setattr(kubectl, "_backend", CliTool.oc)
        a, _ = kubectl.get_adoption_stats(["a", "b"])
        assert a.route_count == 2
        assert a.deploymentconfig_count is None
        assert a.fetch_errors == {
            "deploymentconfig_count": "deploymentconfigs.apps.openshift.io: 403 Forbidden",
        }

    def test_passed_pod_listing_is_reused(self, apis: dict[str, _FakeApi]) -> None:
        pods = kubectl.NamespacedItems(items={"a": [_pod("a", _container(limits=_FULL_LIMITS))]})
        a, = kubectl.get_adoption_stats(["a"], pods=pods)
        assert (a.pod_count, a.pods_with_limits) == (1, 1)
        assert not any(name.endswith("_pod") or name == "list_pod_for_all_namespaces"
                       for name, _ in apis["CoreV1Api"].calls)


# ---------------------------------------------------------------------------
# PeerAuthentication selection
# ---------------------------------------------------------------------------

def _peer_auth(mode: str | None, *, selector: bool = False) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    if mode:
        spec["mtls"] = {"mode": mode}
    if selector:
        spec["selector"] = {"matchLabels": {"app": "x"}}
    return {"spec": spec}


class TestNamespaceWideMtls:
    def test_skips_workload_policies(self) -> None:
        items = [_peer_auth("DISABLE", selector=True), _peer_auth("STRICT")]
        assert kubectl._namespace_wide_mtls(items) == "STRICT"

    def test_only_workload_policies(self) -> None:
        assert kubectl._namespace_wide_mtls([_peer_auth("STRICT", selector=True)]) is None

    def test_policy_without_mtls_block(self) -> None:
        assert kubectl._namespace_wide_mtls([_peer_auth(None)]) is None

    def test_empty_spec(self) -> None:
        assert kubectl._namespace_wide_mtls([{}]) is None


# ---------------------------------------------------------------------------
# CRD statistics
# ---------------------------------------------------------------------------

def _version(name: str, storage: bool = False) -> Any:
    return SimpleNamespace(name=name, storage=storage)


def _crd(group: str, kind: str, plural: str, *, scope: str = "Namespaced",
         versions: list[Any] | None = None) -> Any:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=f"{plural}.{group}"),
        spec=SimpleNamespace(
            group=group,
            names=SimpleNamespace(kind=kind, plural=plural),
            scope=scope,
            versions=[_version("v1", storage=True)] if versions is None else versions,
        ),
    )


class TestStorageVersion:
    def test_storage_flag_wins(self) -> None:
        crd = _crd("x.io", "X", "xs", versions=[_version("v2"), _version("v1beta1", True)])
        assert kubectl._storage_version(crd) == "v1beta1"

    def test_first_version_without_storage_flag(self) -> None:
        crd = _crd("x.io", "X", "xs", versions=[_version("v2"), _version("v1")])
        assert kubectl._storage_version(crd) == "v2"

    def test_no_versions(self) -> None:
        assert kubectl._storage_version(_crd("x.io", "X", "xs", versions=[])) == "v1"


class TestGetCrdStatsFiltering:
    @pytest.fixture()
    def crds(self, apis: dict[str, _FakeApi]) -> _FakeApi:
        apis["ApiextensionsV1Api"].on("list_custom_resource_definition", [
            _crd("cert-manager.io", "Certificate", "certificates",
                 versions=[_version("v1alpha2"), _version("v1", storage=True)]),
            _crd("cert-manager.io", "ClusterIssuer", "clusterissuers", scope="Cluster"),
            _crd("example.io", "Widget", "widgets"),
        ])
        custom = apis["CustomObjectsApi"]
        custom.on("list_cluster_custom_object", _custom_by_plural(
            certificates=[_obj("a"), _obj("a"), _obj("b"), _obj("unlisted")],
            clusterissuers=[{"metadata": {"name": "letsencrypt"}}],
        ))
        return custom

    def test_counts_and_sorting(self, crds: _FakeApi) -> None:
        stats = kubectl.get_crd_stats(["a", "b"])
        assert [(s.kind, s.instances_by_namespace) for s in stats] == [
            ("Certificate", {"a": 2, "b": 1}),
            ("ClusterIssuer", {"(cluster)": 1}),
        ]
        cert_versions = {kw["version"] for _, kw in crds.calls
                         if kw.get("plural") == "certificates"}
        assert cert_versions == {"v1"}

    @pytest.mark.usefixtures("crds")
    def test_include_empty(self) -> None:
        stats = kubectl.get_crd_stats(["a", "b"], include_empty=True)
        assert [s.kind for s in stats][-1] == "Widget"
        assert stats[-1].total_instances == 0

    @pytest.mark.usefixtures("crds")
    @pytest.mark.parametrize(("kwargs", "expected"), [
        ({"group_filter": "CERT-MANAGER"}, ["Certificate", "ClusterIssuer"]),
        ({"search": "issuer"}, ["ClusterIssuer"]),
        ({"search": "widget", "include_empty": True}, ["Widget"]),
        ({"search": "widget", "group_filter": "cert-manager"}, []),
    ])
    def test_filters(self, kwargs: dict[str, Any], expected: list[str]) -> None:
        assert [s.kind for s in kubectl.get_crd_stats(["a", "b"], **kwargs)] == expected

    def test_cluster_scoped_error_recorded(self, crds: _FakeApi) -> None:
        crds.on("list_cluster_custom_object", _custom_by_plural(
            clusterissuers=_api_error(403, "Forbidden")))
        stats = kubectl.get_crd_stats(["a", "b"], search="issuer")
        assert [(s.kind, s.fetch_errors) for s in stats] == [
            ("ClusterIssuer", {"(cluster)": "403 Forbidden"})]


# ---------------------------------------------------------------------------
# Service entries
# ---------------------------------------------------------------------------

class TestGetServiceEntries:
    def test_ports_and_defaults(self, apis: dict[str, _FakeApi]) -> None:
        apis["CustomObjectsApi"].on("list_cluster_custom_object", [
            {"metadata": {"namespace": "a", "name": "api"},
             "spec": {"hosts": ["api.example.com"], "resolution": "DNS",
                      "ports": [{"number": 443, "protocol": "HTTPS"}, {"number": 5432}]}},
            {"metadata": {"namespace": "b", "name": "bare"}, "spec": {}},
        ])
        a, b = kubectl.get_service_entries(["a", "b"])
        assert (a.namespace, a.name, a.hosts, a.resolution, a.ports) == (
            "a", "api", ["api.example.com"], "DNS", ["443/HTTPS", "5432/TCP"])
        assert (b.namespace, b.hosts, b.resolution, b.ports) == ("b", [], "NONE", [])

    def test_unreadable_namespace_is_skipped(self, apis: dict[str, _FakeApi]) -> None:
        def per_namespace(*, namespace: str, **_: Any) -> Any:
            if namespace == "locked":
                return _api_error(403, "Forbidden")
            return [{"metadata": {"namespace": namespace, "name": "x"}, "spec": {}}]
        apis["CustomObjectsApi"].on("list_cluster_custom_object", _api_error(403, "Forbidden"))
        apis["CustomObjectsApi"].on("list_namespaced_custom_object", per_namespace)

        assert [e.namespace for e in kubectl.get_service_entries(["a", "locked"])] == ["a"]


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

class TestGetNamespaces:
    @pytest.fixture()
    def core(self, apis: dict[str, _FakeApi]) -> _FakeApi:
        apis["CoreV1Api"].on("list_namespace", [
            SimpleNamespace(metadata=SimpleNamespace(name="ns", labels=None)),
        ])
        return apis["CoreV1Api"]

    def test_kubectl_lists_namespaces(self, core: _FakeApi, apis: dict[str, _FakeApi]) -> None:
        [ns] = kubectl.get_namespaces()
        assert (ns.name, ns.labels) == ("ns", {})
        assert apis["CustomObjectsApi"].calls == []

    @pytest.mark.usefixtures("core")
    def test_oc_prefers_projects(self, apis: dict[str, _FakeApi],
                                 monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(kubectl, "_backend", CliTool.oc)
        apis["CustomObjectsApi"].on("list_cluster_custom_object", [
            {"metadata": {"name": "proj", "labels": {"team": "x"}}},
        ])
        [ns] = kubectl.get_namespaces()
        assert (ns.name, ns.labels) == ("proj", {"team": "x"})

    @pytest.mark.usefixtures("core")
    @pytest.mark.parametrize("projects", [[], _api_error(404)])
    def test_oc_falls_back_to_namespaces(self, apis: dict[str, _FakeApi],
                                         monkeypatch: pytest.MonkeyPatch,
                                         projects: Any) -> None:
        monkeypatch.setattr(kubectl, "_backend", CliTool.oc)
        apis["CustomObjectsApi"].on("list_cluster_custom_object", projects)
        assert [ns.name for ns in kubectl.get_namespaces()] == ["ns"]

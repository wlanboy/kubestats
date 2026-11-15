"""Tests for the collection logic in kubectl.py, with the Kubernetes client mocked."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import urllib3
from kubernetes.client.exceptions import ApiException

from kubestats import kubectl
from kubestats.models import GVR, NamespaceInfo


def _api_error(status: int, reason: str = "Error") -> ApiException:
    return ApiException(status=status, reason=reason)


def _container(name: str, *, liveness: bool = True, readiness: bool = True) -> Any:
    return SimpleNamespace(
        name=name,
        liveness_probe=object() if liveness else None,
        readiness_probe=object() if readiness else None,
    )


def _pod(containers: list[Any], init_containers: list[Any] | None = None,
         annotations: dict[str, str] | None = None) -> Any:
    return SimpleNamespace(
        spec=SimpleNamespace(containers=containers, init_containers=init_containers),
        metadata=SimpleNamespace(annotations=annotations),
    )


_ISTIO_PROXY = _container("istio-proxy", liveness=False)   # readiness only, like the injector


# ---------------------------------------------------------------------------
# Probe check (sidecars ignored)
# ---------------------------------------------------------------------------

class TestHasProbes:
    def test_app_with_probes_plus_istio_proxy_counts(self) -> None:
        assert kubectl._has_probes(_pod([_container("app"), _ISTIO_PROXY]))

    def test_istio_proxy_checked_when_not_ignored(self) -> None:
        pod = _pod([_container("app"), _ISTIO_PROXY])
        assert not kubectl._has_probes(pod, ignore=())

    def test_app_without_liveness_fails(self) -> None:
        pod = _pod([_container("app", liveness=False), _ISTIO_PROXY])
        assert not kubectl._has_probes(pod)

    def test_custom_ignore_list(self) -> None:
        pod = _pod([_container("app"), _container("log-shipper", liveness=False)])
        assert kubectl._has_probes(pod, ignore={"log-shipper"})

    def test_pod_of_only_ignored_containers_has_no_probes(self) -> None:
        assert not kubectl._has_probes(_pod([_ISTIO_PROXY]))


# ---------------------------------------------------------------------------
# Version fallback and error handling
# ---------------------------------------------------------------------------

_GVR = GVR("example.io", ("v2", "v1"), "widgets")


class TestListCustom:
    def test_falls_back_to_next_version_on_404(self) -> None:
        custom = MagicMock()
        custom.list_namespaced_custom_object.side_effect = [
            _api_error(404), {"items": [{}, {}]},
        ]
        assert len(kubectl._list_custom(custom, _GVR, "ns") or []) == 2
        assert custom.list_namespaced_custom_object.call_args.kwargs["version"] == "v1"

    def test_all_404_means_not_installed(self) -> None:
        custom = MagicMock()
        custom.list_namespaced_custom_object.side_effect = _api_error(404)
        assert kubectl._list_custom(custom, _GVR, "ns") is None

    def test_forbidden_raises(self) -> None:
        custom = MagicMock()
        custom.list_namespaced_custom_object.side_effect = _api_error(403, "Forbidden")
        with pytest.raises(kubectl._FetchError, match="widgets.example.io: 403 Forbidden"):
            kubectl._list_custom(custom, _GVR, "ns")

    def test_collect_records_error_and_returns_none(self) -> None:
        custom = MagicMock()
        custom.list_namespaced_custom_object.side_effect = _api_error(500, "Internal")
        errors: dict[str, str] = {}
        result = kubectl._collect(errors, "flux_resources", kubectl._list_custom,
                                  custom, _GVR, "ns")
        assert result is None
        assert errors == {"flux_resources": "widgets.example.io: 500 Internal"}

    def test_collect_passes_value_through(self) -> None:
        errors: dict[str, str] = {}
        assert kubectl._collect(errors, "x", lambda: 0) == 0
        assert errors == {}


# ---------------------------------------------------------------------------
# CRD statistics
# ---------------------------------------------------------------------------

def _crd(name: str = "widgets.example.io") -> Any:
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(
            group="example.io",
            names=SimpleNamespace(kind="Widget", plural="widgets"),
            scope="Namespaced",
            versions=[SimpleNamespace(name="v1", storage=True)],
        ),
    )


class TestGetCrdStats:
    @pytest.fixture()
    def custom(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        ext = MagicMock()
        ext.list_custom_resource_definition.return_value = SimpleNamespace(
            items=[_crd()], metadata=SimpleNamespace(_continue=None))
        custom = MagicMock()
        # RBAC limited to namespaces: the cluster-wide list is forbidden.
        custom.list_cluster_custom_object.side_effect = _api_error(403, "Forbidden")
        monkeypatch.setattr(kubectl.client, "ApiextensionsV1Api", lambda: ext)
        monkeypatch.setattr(kubectl.client, "CustomObjectsApi", lambda: custom)
        return custom

    def test_forbidden_namespace_recorded_not_zero(self, custom: MagicMock) -> None:
        def list_objects(*, namespace: str, **_: Any) -> dict[str, Any]:
            if namespace == "locked":
                raise _api_error(403, "Forbidden")
            return {"items": [{}]}
        custom.list_namespaced_custom_object.side_effect = list_objects

        [stat] = kubectl.get_crd_stats(["open", "locked"])
        assert stat.instances_by_namespace == {"open": 1}
        assert stat.fetch_errors == {"locked": "403 Forbidden"}

    def test_crd_with_only_errors_stays_visible(self, custom: MagicMock) -> None:
        custom.list_namespaced_custom_object.side_effect = _api_error(403, "Forbidden")
        [stat] = kubectl.get_crd_stats(["a"])
        assert stat.total_instances == 0
        assert stat.fetch_errors == {"a": "403 Forbidden"}

    def test_404_counts_as_zero(self, custom: MagicMock) -> None:
        custom.list_namespaced_custom_object.side_effect = _api_error(404)
        assert kubectl.get_crd_stats(["a"]) == []


# ---------------------------------------------------------------------------
# Istio
# ---------------------------------------------------------------------------

class TestInjectionMode:
    @pytest.mark.parametrize(("labels", "expected"), [
        ({"istio-injection": "enabled"}, "sidecar"),
        ({"istio-injection": "disabled", "istio.io/rev": "1-24"}, "disabled"),
        ({"istio.io/rev": "1-24"}, "rev=1-24"),
        ({"istio.io/dataplane-mode": "ambient"}, "ambient"),
        ({"istio-injection": "enabled", "istio.io/rev": "1-24"}, "sidecar"),
        ({}, "none"),
    ])
    def test_labels(self, labels: dict[str, str], expected: str) -> None:
        assert kubectl._injection_mode(labels) == expected


class TestSidecarDetection:
    def test_regular_sidecar(self) -> None:
        assert kubectl._has_sidecar(_pod([_container("app"), _ISTIO_PROXY]))

    def test_native_sidecar_in_init_containers(self) -> None:
        assert kubectl._has_sidecar(_pod([_container("app")], init_containers=[_ISTIO_PROXY]))

    def test_no_sidecar(self) -> None:
        assert not kubectl._has_sidecar(_pod([_container("app")]))

    def test_ambient_annotation(self) -> None:
        pod = _pod([_container("app")], annotations={"ambient.istio.io/redirection": "enabled"})
        assert kubectl._is_ambient(pod)


def _peer_auth(mode: str | None, selector: dict[str, Any] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    if mode:
        spec["mtls"] = {"mode": mode}
    if selector:
        spec["selector"] = selector
    return {"spec": spec}


class TestResolveMtls:
    def test_namespace_policy_wins(self) -> None:
        assert kubectl._resolve_mtls([_peer_auth("PERMISSIVE")], "STRICT") == "PERMISSIVE"

    def test_mesh_fallback_is_marked(self) -> None:
        assert kubectl._resolve_mtls([], "STRICT") == "STRICT (mesh)"

    def test_unset_namespace_policy_inherits_mesh(self) -> None:
        assert kubectl._resolve_mtls([_peer_auth("UNSET")], "STRICT") == "STRICT (mesh)"

    def test_workload_policy_is_not_namespace_mode(self) -> None:
        items = [_peer_auth("DISABLE", selector={"matchLabels": {"app": "x"}})]
        assert kubectl._resolve_mtls(items, None) == "none"

    def test_no_policy_anywhere(self) -> None:
        assert kubectl._resolve_mtls([], None) == "none"


class TestGetIstioStats:
    @pytest.fixture()
    def apis(self, monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
        core = MagicMock()
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[
            _pod([_container("app"), _ISTIO_PROXY]),
            _pod([_container("app")], init_containers=[_ISTIO_PROXY]),
            _pod([_container("app")]),
        ], metadata=SimpleNamespace(_continue=None))
        custom = MagicMock()
        monkeypatch.setattr(kubectl.client, "CoreV1Api", lambda: core)
        monkeypatch.setattr(kubectl.client, "CustomObjectsApi", lambda: custom)
        return core, custom

    def test_mesh_wide_mtls_and_native_sidecars(self, apis: tuple[MagicMock, MagicMock]) -> None:
        _, custom = apis

        def list_objects(*, namespace: str, plural: str, **_: Any) -> dict[str, Any]:
            if plural == "peerauthentications" and namespace == "istio-system":
                return {"items": [_peer_auth("STRICT")]}
            return {"items": []}
        custom.list_namespaced_custom_object.side_effect = list_objects

        [stat] = kubectl.get_istio_stats(
            [NamespaceInfo("team", {"istio.io/rev": "stable"})])
        assert stat.injection_mode == "rev=stable"
        assert stat.sidecar_count == 2
        assert stat.coverage_pct == 66
        assert stat.mtls_mode == "STRICT (mesh)"
        assert stat.fetch_errors == {}

    def test_forbidden_counts_are_unknown(self, apis: tuple[MagicMock, MagicMock]) -> None:
        _, custom = apis

        def list_objects(*, plural: str, **_: Any) -> dict[str, Any]:
            if plural == "authorizationpolicies":
                raise _api_error(403, "Forbidden")
            return {"items": []}
        custom.list_namespaced_custom_object.side_effect = list_objects

        [stat] = kubectl.get_istio_stats([NamespaceInfo("team", {})])
        assert stat.authorization_policies is None
        assert stat.virtual_services == 0
        assert stat.fetch_errors == {
            "authorization_policies": "authorizationpolicies.security.istio.io: 403 Forbidden",
        }

    def test_unreadable_mesh_policy_makes_mtls_unknown(
            self, apis: tuple[MagicMock, MagicMock]) -> None:
        _, custom = apis

        def list_objects(*, namespace: str, plural: str, **_: Any) -> dict[str, Any]:
            if plural == "peerauthentications" and namespace == "istio-system":
                raise _api_error(403, "Forbidden")
            return {"items": []}
        custom.list_namespaced_custom_object.side_effect = list_objects

        [stat] = kubectl.get_istio_stats([NamespaceInfo("team", {})])
        assert stat.mtls_mode is None
        assert "istio-system" in stat.fetch_errors["mtls_mode"]


# ---------------------------------------------------------------------------
# Cluster-wide listing, pagination, timeout
# ---------------------------------------------------------------------------

def _obj(namespace: str) -> dict[str, Any]:
    return {"metadata": {"namespace": namespace}}


class TestListPages:
    def test_follows_continue_token_and_sets_timeout(self) -> None:
        fn = MagicMock(side_effect=[
            {"items": [_obj("a")], "metadata": {"continue": "t1"}},
            {"items": [_obj("b")], "metadata": {}},
        ])
        assert kubectl._list_pages(fn, plural="x") == [_obj("a"), _obj("b")]
        first, second = fn.call_args_list
        assert first.kwargs["_continue"] is None
        assert second.kwargs["_continue"] == "t1"
        assert first.kwargs["_request_timeout"] == kubectl.REQUEST_TIMEOUT_SECONDS
        assert first.kwargs["limit"] == kubectl._PAGE_SIZE

    def test_typed_response(self) -> None:
        page = SimpleNamespace(items=[1, 2], metadata=SimpleNamespace(_continue=None))
        assert kubectl._list_pages(MagicMock(return_value=page)) == [1, 2]

    def test_timeout_becomes_api_exception(self) -> None:
        fn = MagicMock(side_effect=urllib3.exceptions.ReadTimeoutError(None, "/", "read timed out"))
        with pytest.raises(ApiException) as exc:
            kubectl._list_pages(fn)
        assert exc.value.status == 0
        assert "ReadTimeoutError" in kubectl._reason(exc.value)


class TestListByNamespace:
    def test_one_cluster_wide_request_grouped(self) -> None:
        list_one = MagicMock()
        result = kubectl._list_by_namespace(
            ["a", "b", "c"],
            lambda: [_obj("a"), _obj("a"), _obj("b"), _obj("other")],
            list_one,
        )
        assert {ns: len(items) for ns, items in result.items.items()} == {"a": 2, "b": 1}
        assert result.get("c") == []
        assert result.errors == {}
        list_one.assert_not_called()

    def test_forbidden_falls_back_per_namespace(self) -> None:
        def list_all() -> list[Any]:
            raise _api_error(403, "Forbidden")

        def list_one(ns: str) -> list[Any]:
            if ns == "locked":
                raise _api_error(403, "Forbidden")
            return [_obj(ns)]

        result = kubectl._list_by_namespace(["a", "locked"], list_all, list_one, label="w.x")
        assert result.get("a") == [_obj("a")]
        assert result.errors == {"locked": "w.x: 403 Forbidden"}
        with pytest.raises(kubectl._FetchError, match="w.x: 403 Forbidden"):
            result.get("locked")

    def test_other_error_marks_every_namespace(self) -> None:
        def list_all() -> list[Any]:
            raise _api_error(500, "Internal")

        list_one = MagicMock()
        result = kubectl._list_by_namespace(["a", "b"], list_all, list_one)
        assert result.errors == {"a": "500 Internal", "b": "500 Internal"}
        list_one.assert_not_called()

    def test_single_namespace_lists_namespaced(self) -> None:
        list_all = MagicMock()
        result = kubectl._list_by_namespace(["a"], list_all, lambda ns: [_obj(ns)])
        assert result.get("a") == [_obj("a")]
        list_all.assert_not_called()


class TestSharedPods:
    def test_istio_reuses_passed_pod_listing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        core = MagicMock()
        custom = MagicMock()
        custom.list_cluster_custom_object.return_value = {"items": []}
        custom.list_namespaced_custom_object.return_value = {"items": []}
        monkeypatch.setattr(kubectl.client, "CoreV1Api", lambda: core)
        monkeypatch.setattr(kubectl.client, "CustomObjectsApi", lambda: custom)

        pods = kubectl.NamespacedItems(items={"a": [_pod([_container("app"), _ISTIO_PROXY])]})
        stats = kubectl.get_istio_stats(
            [NamespaceInfo("a", {}), NamespaceInfo("b", {})], pods=pods)

        assert [(s.namespace, s.pod_count, s.sidecar_count) for s in stats] == [
            ("a", 1, 1), ("b", 0, 0)]
        core.list_pod_for_all_namespaces.assert_not_called()
        core.list_namespaced_pod.assert_not_called()
        # One cluster-wide request per Istio resource type, none per namespace.
        custom.list_namespaced_custom_object.assert_called_once()  # mesh root policy
        assert custom.list_cluster_custom_object.call_count == len(kubectl._ISTIO_COUNTED) + 1

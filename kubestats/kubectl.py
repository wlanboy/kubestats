"""Kubernetes / OpenShift data collection via the official Python client."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import urllib3
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from kubestats.models import (
    GVR,
    AdoptionStat,
    CliTool,
    CRDStat,
    IstioNamespaceStat,
    NamespaceInfo,
    ServiceEntryStat,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
#
# A 404 means "resource type not installed" and counts as zero. Any other API
# error (403, 429, 5xx, ...) means the value is unknown: the metric becomes
# None and the reason is recorded in the stat's fetch_errors.

class _FetchError(Exception):
    """A non-404 API error, carrying a short human-readable reason."""


def _reason(e: ApiException) -> str:
    if not e.status:  # transport error (timeout, connection refused, TLS)
        return str(e.reason or "request failed").strip()
    return f"{e.status} {e.reason}".strip()


def _error_text(e: Exception) -> str:
    return _reason(e) if isinstance(e, ApiException) else str(e)


def _collect[**P, T](errors: dict[str, str], metric: str, fn: Callable[P, T],
                     *args: P.args, **kwargs: P.kwargs) -> T | None:
    """Call fn; on an API error record the reason under `metric` and return None."""
    try:
        return fn(*args, **kwargs)
    except _FetchError as e:
        errors[metric] = str(e)
    except ApiException as e:
        errors[metric] = _reason(e)
    return None

# ---------------------------------------------------------------------------
# Requests: timeout, pagination, cluster-wide listing
# ---------------------------------------------------------------------------
#
# Every list call goes through _list_pages, which sets a timeout and fetches
# page by page. Resources are listed once cluster-wide and grouped by
# namespace; only if that is forbidden (RBAC limited to some namespaces) does
# the code fall back to one request per namespace.

REQUEST_TIMEOUT_SECONDS = 30.0
_PAGE_SIZE = 500


def _call(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Call an API method with the request timeout.

    Transport errors (timeout, connection refused) become an ApiException with
    status 0, so callers handle them like any other non-404 API error.
    """
    try:
        return fn(_request_timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
    except urllib3.exceptions.HTTPError as e:
        raise ApiException(status=0, reason=f"{type(e).__name__}: {e}") from e


def _list_pages(fn: Callable[..., Any], **kwargs: Any) -> list[Any]:
    """All items of a list call, fetched in pages of _PAGE_SIZE.

    Works for typed responses (V1PodList, ...) and for the plain dicts the
    CustomObjectsApi returns.
    """
    items: list[Any] = []
    token: str | None = None
    while True:
        page = _call(fn, limit=_PAGE_SIZE, _continue=token, **kwargs)
        if isinstance(page, dict):
            items.extend(page.get("items") or [])
            token = (page.get("metadata") or {}).get("continue")
        else:
            items.extend(page.items or [])
            token = page.metadata._continue if page.metadata else None
        if not token:
            return items


def _namespace_of(item: Any) -> str | None:
    if isinstance(item, dict):
        return (item.get("metadata") or {}).get("namespace")
    return item.metadata.namespace


@dataclass
class NamespacedItems:
    """Items of one resource type, grouped by namespace.

    A namespace in `errors` could not be listed; its items are unknown.
    """
    items: dict[str, list[Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def get(self, namespace: str) -> list[Any]:
        """Items in `namespace`. Raises _FetchError if it could not be listed."""
        if namespace in self.errors:
            raise _FetchError(self.errors[namespace])
        return self.items.get(namespace, [])


def _list_by_namespace(
    namespaces: list[str],
    list_all: Callable[[], list[Any]],
    list_one: Callable[[str], list[Any]],
    *,
    label: str | None = None,
) -> NamespacedItems:
    """List a resource for `namespaces`: one cluster-wide request if possible.

    Falls back to one request per namespace if the cluster-wide list is
    forbidden (403) or only a single namespace is wanted. Both callables raise
    ApiException; `label` prefixes recorded error reasons.
    """
    def fmt(e: Exception) -> str:
        return f"{label}: {_error_text(e)}" if label else _error_text(e)

    result = NamespacedItems()
    if len(namespaces) > 1:
        try:
            items = list_all()
        except ApiException as e:
            if e.status != 403:
                result.errors = dict.fromkeys(namespaces, fmt(e))
                return result
            logger.debug("Cluster-wide list forbidden%s, listing per namespace",
                         f" for {label}" if label else "")
        else:
            wanted = set(namespaces)
            for item in items:
                ns = _namespace_of(item)
                if ns in wanted:
                    result.items.setdefault(ns, []).append(item)
            return result

    for ns in namespaces:
        try:
            result.items[ns] = list_one(ns)
        except ApiException as e:
            result.errors[ns] = fmt(e)
    return result


def _list_core(namespaces: list[str], list_all_fn: Callable[..., Any],
               list_namespaced_fn: Callable[..., Any]) -> NamespacedItems:
    """Group a built-in resource by namespace, e.g. pods via
    list_pod_for_all_namespaces / list_namespaced_pod."""
    return _list_by_namespace(
        namespaces,
        lambda: _list_pages(list_all_fn),
        lambda ns: _list_pages(list_namespaced_fn, namespace=ns),
    )


def list_pods(namespace_names: list[str]) -> NamespacedItems:
    """Pods per namespace. Pass the result to get_adoption_stats and
    get_istio_stats to list pods only once."""
    v1 = client.CoreV1Api()
    return _list_core(namespace_names, v1.list_pod_for_all_namespaces,
                      v1.list_namespaced_pod)


# ---------------------------------------------------------------------------
# Backend selection (kubectl vs oc / OpenShift)
# ---------------------------------------------------------------------------

_backend: CliTool = CliTool.kubectl


def set_backend(backend: CliTool) -> None:
    """Set the active CLI backend. Call after load_config()."""
    global _backend
    _backend = backend


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def load_config(kubeconfig: str | None = None, context: str | None = None) -> None:
    """Load kubeconfig (in-cluster first, then local ~/.kube/config).

    Args:
        kubeconfig: Path to a kubeconfig file. Defaults to ~/.kube/config.
        context:    Kubeconfig context to activate. Defaults to the current context.
    """
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(config_file=kubeconfig, context=context)


# ---------------------------------------------------------------------------
# Namespace listing
# ---------------------------------------------------------------------------

def get_namespaces() -> list[NamespaceInfo]:
    """List namespaces. In oc mode, tries OpenShift Projects first."""
    if _backend == CliTool.oc:
        custom = client.CustomObjectsApi()
        try:
            items: list[Any] = _list_pages(
                custom.list_cluster_custom_object,
                group="project.openshift.io", version="v1", plural="projects",
            )
            if items:
                return [
                    NamespaceInfo(
                        name=item["metadata"]["name"],
                        labels=item["metadata"].get("labels") or {},
                    )
                    for item in items
                ]
        except ApiException:
            logger.debug("OpenShift Projects API unavailable, falling back to namespaces")

    v1 = client.CoreV1Api()
    return [
        NamespaceInfo(name=ns.metadata.name, labels=ns.metadata.labels or {})
        for ns in _list_pages(v1.list_namespace)
    ]


# ---------------------------------------------------------------------------
# CRD statistics
# ---------------------------------------------------------------------------

def _storage_version(crd) -> str:
    for v in crd.spec.versions:
        if getattr(v, "storage", False):
            return v.name
    return crd.spec.versions[0].name if crd.spec.versions else "v1"


def _list_pages_or_empty(fn: Callable[..., Any], **kwargs: Any) -> list[Any]:
    """Like _list_pages; a 404 (resource type not installed) counts as empty."""
    try:
        return _list_pages(fn, **kwargs)
    except ApiException as e:
        if e.status == 404:
            return []
        raise


def get_crd_stats(
    namespace_names: list[str],
    *,
    include_empty: bool = False,
    search: str | None = None,
    group_filter: str | None = None,
) -> list[CRDStat]:
    ext = client.ApiextensionsV1Api()
    custom = client.CustomObjectsApi()

    stats: list[CRDStat] = []
    search_lower = search.lower() if search else None
    group_lower = group_filter.lower() if group_filter else None

    for crd in _list_pages(ext.list_custom_resource_definition):
        spec = crd.spec

        if group_lower and group_lower not in spec.group.lower():
            continue
        if search_lower and not any(
            search_lower in s.lower()
            for s in [crd.metadata.name, spec.names.kind, spec.group]
        ):
            continue

        version = _storage_version(crd)
        stat = CRDStat(
            name=crd.metadata.name,
            group=spec.group,
            kind=spec.names.kind,
            plural=spec.names.plural,
            namespaced=spec.scope == "Namespaced",
        )

        gvp = {"group": spec.group, "version": version, "plural": spec.names.plural}
        if stat.namespaced:
            listing = _list_by_namespace(
                namespace_names,
                lambda gvp=gvp: _list_pages_or_empty(
                    custom.list_cluster_custom_object, **gvp),
                lambda ns, gvp=gvp: _list_pages_or_empty(
                    custom.list_namespaced_custom_object, namespace=ns, **gvp),
            )
            stat.fetch_errors.update(listing.errors)
            for ns, items in listing.items.items():
                if items:
                    stat.instances_by_namespace[ns] = len(items)
        else:
            try:
                count = len(_list_pages_or_empty(
                    custom.list_cluster_custom_object, **gvp))
            except ApiException as e:
                stat.fetch_errors["(cluster)"] = _reason(e)
            else:
                if count:
                    stat.instances_by_namespace["(cluster)"] = count

        # A CRD whose count is unknown somewhere is not "empty": keep it visible.
        if include_empty or stat.total_instances > 0 or stat.fetch_errors:
            stats.append(stat)

    return sorted(stats, key=lambda s: s.total_instances, reverse=True)


# ---------------------------------------------------------------------------
# Adoption metrics
# ---------------------------------------------------------------------------

_VPA = GVR("autoscaling.k8s.io", ("v1",), "verticalpodautoscalers")
_HELM_RELEASES = GVR("helm.toolkit.fluxcd.io", ("v2", "v2beta2", "v2beta1"), "helmreleases")
_KUSTOMIZATIONS = GVR("kustomize.toolkit.fluxcd.io", ("v1", "v1beta2", "v1beta1"), "kustomizations")
_ARGOCD_APPLICATIONS = GVR("argoproj.io", ("v1alpha1",), "applications")
_OC_ROUTES = GVR("route.openshift.io", ("v1",), "routes")
_OC_DEPLOYMENTCONFIGS = GVR("apps.openshift.io", ("v1",), "deploymentconfigs")


def _list_custom_raw(custom: client.CustomObjectsApi, gvr: GVR,
                     namespace: str | None = None) -> list[dict[str, Any]] | None:
    """Items of the first served version, or None if no version is installed (all 404).

    Lists cluster-wide when `namespace` is None. Raises ApiException on any
    other API error.
    """
    for version in gvr.versions:
        kwargs: dict[str, Any] = {"group": gvr.group, "version": version, "plural": gvr.plural}
        try:
            if namespace is None:
                return _list_pages(custom.list_cluster_custom_object, **kwargs)
            return _list_pages(custom.list_namespaced_custom_object,
                               namespace=namespace, **kwargs)
        except ApiException as e:
            if e.status != 404:
                raise
    return None


def _list_custom(custom: client.CustomObjectsApi, gvr: GVR,
                 namespace: str) -> list[dict[str, Any]] | None:
    """Like _list_custom_raw for one namespace, but raises _FetchError."""
    try:
        return _list_custom_raw(custom, gvr, namespace)
    except ApiException as e:
        raise _FetchError(f"{gvr.plural}.{gvr.group}: {_reason(e)}") from e


def _list_custom_by_namespace(custom: client.CustomObjectsApi, gvr: GVR,
                              namespaces: list[str]) -> NamespacedItems:
    """Custom resources per namespace; empty if the type is not installed."""
    return _list_by_namespace(
        namespaces,
        lambda: _list_custom_raw(custom, gvr) or [],
        lambda ns: _list_custom_raw(custom, gvr, ns) or [],
        label=f"{gvr.plural}.{gvr.group}",
    )


def _count(namespace: str, *listings: NamespacedItems) -> int:
    """Summed item count in `namespace`. Raises _FetchError if any listing failed there."""
    return sum(len(listing.get(namespace)) for listing in listings)


_DEFAULT_PROBE_IGNORE: frozenset[str] = frozenset({"istio-proxy"})


def _has_probes(pod: Any, ignore: Iterable[str] = _DEFAULT_PROBE_IGNORE) -> bool:
    """True if every container except injected sidecars has liveness and readiness probes.

    Injected sidecars such as istio-proxy ship with a readiness probe only, so
    checking them would mark every meshed pod as "without probes". A pod made
    up only of ignored containers counts as without probes.
    """
    skip = set(ignore)
    containers = [c for c in (pod.spec.containers or []) if c.name not in skip]
    return bool(containers) and all(
        c.liveness_probe and c.readiness_probe for c in containers
    )


def _has_limits(pod: Any) -> bool:
    """True if every container sets CPU and memory limits."""
    return all(
        c.resources and c.resources.limits
        and "cpu" in c.resources.limits
        and "memory" in c.resources.limits
        for c in pod.spec.containers
    )


def get_adoption_stats(
    namespace_names: list[str],
    *,
    probe_ignore: Iterable[str] = _DEFAULT_PROBE_IGNORE,
    pods: NamespacedItems | None = None,
) -> list[AdoptionStat]:
    """Adoption metrics per namespace.

    Each resource type is listed once for all namespaces. Pass `pods` from
    list_pods() to reuse a pod listing.
    """
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    autoscaling = client.AutoscalingV2Api()
    networking = client.NetworkingV1Api()
    policy = client.PolicyV1Api()
    custom = client.CustomObjectsApi()
    names = namespace_names

    if pods is None:
        pods = list_pods(names)
    # A failure on any of these skips the namespace.
    net_policies = _list_core(names, networking.list_network_policy_for_all_namespaces,
                              networking.list_namespaced_network_policy)
    quotas = _list_core(names, v1.list_resource_quota_for_all_namespaces,
                        v1.list_namespaced_resource_quota)
    limit_ranges = _list_core(names, v1.list_limit_range_for_all_namespaces,
                              v1.list_namespaced_limit_range)
    deployments = _list_core(names, apps_v1.list_deployment_for_all_namespaces,
                             apps_v1.list_namespaced_deployment)
    statefulsets = _list_core(names, apps_v1.list_stateful_set_for_all_namespaces,
                              apps_v1.list_namespaced_stateful_set)
    ingresses = _list_core(names, networking.list_ingress_for_all_namespaces,
                           networking.list_namespaced_ingress)
    pvcs = _list_core(names, v1.list_persistent_volume_claim_for_all_namespaces,
                      v1.list_namespaced_persistent_volume_claim)
    service_accounts = _list_core(names, v1.list_service_account_for_all_namespaces,
                                  v1.list_namespaced_service_account)
    hpas = _list_core(names, autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces,
                      autoscaling.list_namespaced_horizontal_pod_autoscaler)
    # A failure on any of these makes only the metric unknown.
    pdbs = _list_core(names, policy.list_pod_disruption_budget_for_all_namespaces,
                      policy.list_namespaced_pod_disruption_budget)
    vpas = _list_custom_by_namespace(custom, _VPA, names)
    helm_releases = _list_custom_by_namespace(custom, _HELM_RELEASES, names)
    kustomizations = _list_custom_by_namespace(custom, _KUSTOMIZATIONS, names)
    argocd_apps = _list_custom_by_namespace(custom, _ARGOCD_APPLICATIONS, names)
    routes = dcs = None
    if _backend == CliTool.oc:
        routes = _list_custom_by_namespace(custom, _OC_ROUTES, names)
        dcs = _list_custom_by_namespace(custom, _OC_DEPLOYMENTCONFIGS, names)

    stats: list[AdoptionStat] = []

    for ns in names:
        try:
            ns_pods = pods.get(ns)
            ns_deployments = deployments.get(ns)
            deployment_names = {d.metadata.name for d in ns_deployments}
            hpa_targets = {h.spec.scale_target_ref.name for h in hpas.get(ns)}
            core = {
                "has_network_policy": bool(net_policies.get(ns)),
                "has_resource_quota": bool(quotas.get(ns)),
                "has_limit_range": bool(limit_ranges.get(ns)),
                "statefulset_count": len(statefulsets.get(ns)),
                "ingress_count": len(ingresses.get(ns)),
                "pvc_count": len(pvcs.get(ns)),
                "serviceaccount_count": len(service_accounts.get(ns)),
            }
        except _FetchError as e:
            logger.warning("Skipping namespace %s: %s", ns, e)
            continue

        errors: dict[str, str] = {}
        pdb_count = _collect(errors, "pdb_count", _count, ns, pdbs)
        vpa_count = _collect(errors, "vpa_count", _count, ns, vpas)
        flux_count = _collect(errors, "flux_resources", _count,
                              ns, helm_releases, kustomizations)
        argocd_count = _collect(errors, "argocd_resources", _count, ns, argocd_apps)
        route_count: int | None = 0
        dc_count: int | None = 0
        if routes is not None and dcs is not None:
            route_count = _collect(errors, "route_count", _count, ns, routes)
            dc_count = _collect(errors, "deploymentconfig_count", _count, ns, dcs)

        stats.append(AdoptionStat(
            namespace=ns,
            pod_count=len(ns_pods),
            pods_with_limits=sum(1 for pod in ns_pods if _has_limits(pod)),
            pods_with_probes=sum(1 for pod in ns_pods if _has_probes(pod, probe_ignore)),
            deployment_count=len(ns_deployments),
            pdb_count=pdb_count,
            hpa_count=len(hpa_targets & deployment_names),
            vpa_count=vpa_count,
            flux_resources=flux_count,
            argocd_resources=argocd_count,
            route_count=route_count,
            deploymentconfig_count=dc_count,
            fetch_errors=errors,
            **core,
        ))

    return stats


# ---------------------------------------------------------------------------
# Istio statistics
# ---------------------------------------------------------------------------

_ISTIO_API_VERSIONS = ("v1", "v1beta1", "v1alpha3")
_PEER_AUTHENTICATIONS = GVR("security.istio.io", ("v1", "v1beta1"), "peerauthentications")
_SERVICE_ENTRIES = GVR("networking.istio.io", _ISTIO_API_VERSIONS, "serviceentries")
DEFAULT_MESH_ROOT_NAMESPACE = "istio-system"

# IstioNamespaceStat field -> resource counted for it
_ISTIO_COUNTED: dict[str, GVR] = {
    "virtual_services": GVR("networking.istio.io", _ISTIO_API_VERSIONS, "virtualservices"),
    "destination_rules": GVR("networking.istio.io", _ISTIO_API_VERSIONS, "destinationrules"),
    "gateways": GVR("networking.istio.io", _ISTIO_API_VERSIONS, "gateways"),
    "service_entries": _SERVICE_ENTRIES,
    "workload_entries": GVR("networking.istio.io", _ISTIO_API_VERSIONS, "workloadentries"),
    "authorization_policies": GVR("security.istio.io", _ISTIO_API_VERSIONS, "authorizationpolicies"),
}


def _injection_mode(labels: dict[str, str]) -> str:
    """How the namespace joins the mesh, following Istio's label precedence."""
    injection = labels.get("istio-injection")
    if injection == "disabled":
        return "disabled"
    if injection == "enabled":
        return "sidecar"
    if revision := labels.get("istio.io/rev"):
        return f"rev={revision}"
    if labels.get("istio.io/dataplane-mode") == "ambient":
        return "ambient"
    return "none"


def _has_sidecar(pod: Any) -> bool:
    """istio-proxy in containers, or in initContainers (native sidecars)."""
    containers = (pod.spec.containers or []) + (pod.spec.init_containers or [])
    return any(c.name == "istio-proxy" for c in containers)


def _is_ambient(pod: Any) -> bool:
    annotations = pod.metadata.annotations or {}
    return annotations.get("ambient.istio.io/redirection") == "enabled"


def _namespace_wide_mtls(items: list[dict[str, Any]]) -> str | None:
    """Mode of the PeerAuthentication without workload selector; None if absent or UNSET."""
    for item in items:
        spec = item.get("spec") or {}
        if not spec.get("selector"):
            mode = (spec.get("mtls") or {}).get("mode")
            return mode if mode and mode != "UNSET" else None
    return None


def _resolve_mtls(items: list[dict[str, Any]], mesh_mode: str | None) -> str:
    """The namespace's own policy wins; otherwise the mesh-wide one, marked "(mesh)"."""
    if own := _namespace_wide_mtls(items):
        return own
    if mesh_mode:
        return f"{mesh_mode} (mesh)"
    return "none"


def get_istio_stats(
    namespace_infos: list[NamespaceInfo],
    *,
    mesh_root_namespace: str = DEFAULT_MESH_ROOT_NAMESPACE,
    pods: NamespacedItems | None = None,
) -> list[IstioNamespaceStat]:
    """Istio usage per namespace. Pass `pods` from list_pods() to reuse a pod listing."""
    custom = client.CustomObjectsApi()
    names = [ns.name for ns in namespace_infos]
    stats: list[IstioNamespaceStat] = []

    # The PeerAuthentication without selector in the root namespace applies mesh-wide.
    mesh_mode: str | None = None
    mesh_error: str | None = None
    try:
        mesh_mode = _namespace_wide_mtls(
            _list_custom(custom, _PEER_AUTHENTICATIONS, mesh_root_namespace) or [])
    except _FetchError as e:
        mesh_error = f"mesh-wide policy in {mesh_root_namespace}: {e}"

    if pods is None:
        pods = list_pods(names)
    counted = {
        metric: _list_custom_by_namespace(custom, gvr, names)
        for metric, gvr in _ISTIO_COUNTED.items()
    }
    peer_auth_listing = _list_custom_by_namespace(custom, _PEER_AUTHENTICATIONS, names)

    for ns_info in namespace_infos:
        ns = ns_info.name

        try:
            ns_pods = pods.get(ns)
        except _FetchError as e:
            logger.warning("Skipping namespace %s: %s", ns, e)
            continue

        sidecar_count = sum(1 for pod in ns_pods if _has_sidecar(pod))
        ambient_count = sum(1 for pod in ns_pods if _is_ambient(pod) and not _has_sidecar(pod))

        errors: dict[str, str] = {}

        counts = {
            metric: _collect(errors, metric, _count, ns, listing)
            for metric, listing in counted.items()
        }

        peer_auths = _collect(errors, "peer_authentications", peer_auth_listing.get, ns)
        mtls_mode: str | None = None
        if peer_auths is not None:
            if _namespace_wide_mtls(peer_auths) is None and mesh_error:
                errors["mtls_mode"] = mesh_error
            else:
                mtls_mode = _resolve_mtls(peer_auths, mesh_mode)

        stats.append(IstioNamespaceStat(
            namespace=ns,
            injection_mode=_injection_mode(ns_info.labels),
            pod_count=len(ns_pods),
            sidecar_count=sidecar_count,
            ambient_count=ambient_count,
            virtual_services=counts["virtual_services"],
            destination_rules=counts["destination_rules"],
            gateways=counts["gateways"],
            service_entries=counts["service_entries"],
            workload_entries=counts["workload_entries"],
            peer_authentications=None if peer_auths is None else len(peer_auths),
            authorization_policies=counts["authorization_policies"],
            mtls_mode=mtls_mode,
            fetch_errors=errors,
        ))

    return stats


# ---------------------------------------------------------------------------
# Service entries (external services)
# ---------------------------------------------------------------------------

def get_service_entries(namespace_names: list[str]) -> list[ServiceEntryStat]:
    custom = client.CustomObjectsApi()
    listing = _list_custom_by_namespace(custom, _SERVICE_ENTRIES, namespace_names)
    entries: list[ServiceEntryStat] = []

    for ns in namespace_names:
        try:
            items = listing.get(ns)
        except _FetchError as e:
            logger.warning("Cannot list ServiceEntries in namespace %s: %s", ns, e)
            continue
        for item in items:
            spec = item.get("spec", {})
            ports = [
                f"{p.get('number')}/{p.get('protocol', 'TCP')}"
                for p in spec.get("ports", [])
            ]
            entries.append(ServiceEntryStat(
                namespace=ns,
                name=item["metadata"]["name"],
                hosts=spec.get("hosts", []),
                resolution=spec.get("resolution", "NONE"),
                ports=ports,
            ))

    return entries

"""Kubernetes / OpenShift data collection via the official Python client."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any, cast

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
    return f"{e.status} {e.reason}".strip()


def _collect[T](errors: dict[str, str], metric: str, fn: Callable[[], T]) -> T | None:
    """Run fn; on an API error record the reason under `metric` and return None."""
    try:
        return fn()
    except _FetchError as e:
        errors[metric] = str(e)
    except ApiException as e:
        errors[metric] = _reason(e)
    return None

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
            result = cast(dict[str, Any], custom.list_cluster_custom_object(
                group="project.openshift.io", version="v1", plural="projects"
            ))
            items: list[Any] = result.get("items", [])
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
        for ns in cast(Any, v1.list_namespace()).items
    ]


# ---------------------------------------------------------------------------
# CRD statistics
# ---------------------------------------------------------------------------

def _storage_version(crd) -> str:
    for v in crd.spec.versions:
        if getattr(v, "storage", False):
            return v.name
    return crd.spec.versions[0].name if crd.spec.versions else "v1"


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

    for crd in cast(Any, ext.list_custom_resource_definition()).items:
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

        def record(key: str, list_fn: Callable[[], Any]) -> None:
            try:
                count = len(cast(dict[str, Any], list_fn()).get("items", []))
            except ApiException as e:
                if e.status != 404:
                    stat.fetch_errors[key] = _reason(e)
                return
            if count:
                stat.instances_by_namespace[key] = count

        if stat.namespaced:
            for ns in namespace_names:
                record(ns, lambda ns=ns: custom.list_namespaced_custom_object(
                    group=spec.group, version=version,
                    namespace=ns, plural=spec.names.plural,
                ))
        else:
            record("(cluster)", lambda: custom.list_cluster_custom_object(
                group=spec.group, version=version, plural=spec.names.plural,
            ))

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


def _list_custom(custom: client.CustomObjectsApi, gvr: GVR,
                 namespace: str) -> list[dict[str, Any]] | None:
    """Items of the first served version, or None if no version is installed (all 404).

    Raises _FetchError on any other API error.
    """
    for version in gvr.versions:
        try:
            result = cast(dict[str, Any], custom.list_namespaced_custom_object(
                group=gvr.group, version=version, namespace=namespace, plural=gvr.plural,
            ))
        except ApiException as e:
            if e.status == 404:
                continue
            raise _FetchError(f"{gvr.plural}.{gvr.group}: {_reason(e)}") from e
        return result.get("items", [])
    return None


def _count_custom(custom: client.CustomObjectsApi, gvr: GVR, namespace: str) -> int:
    """Instance count; 0 if the resource type is not installed. Raises _FetchError."""
    return len(_list_custom(custom, gvr, namespace) or [])


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


def get_adoption_stats(
    namespace_names: list[str],
    *,
    probe_ignore: Iterable[str] = _DEFAULT_PROBE_IGNORE,
) -> list[AdoptionStat]:
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    autoscaling = client.AutoscalingV2Api()
    networking = client.NetworkingV1Api()
    custom = client.CustomObjectsApi()

    policy = client.PolicyV1Api()

    stats: list[AdoptionStat] = []

    for ns in namespace_names:
        try:
            pods: list[Any] = cast(Any, v1.list_namespaced_pod(namespace=ns)).items
            pod_count = len(pods)
            pods_with_limits = sum(
                1 for pod in pods
                if all(
                    c.resources and c.resources.limits
                    and "cpu" in (c.resources.limits or {})
                    and "memory" in (c.resources.limits or {})
                    for c in pod.spec.containers
                )
            )
            pods_with_probes = sum(1 for pod in pods if _has_probes(pod, probe_ignore))

            net_policies: list[Any] = cast(Any, networking.list_namespaced_network_policy(namespace=ns)).items
            has_network_policy = bool(net_policies)

            quotas: list[Any] = cast(Any, v1.list_namespaced_resource_quota(namespace=ns)).items
            has_resource_quota = bool(quotas)

            limit_ranges: list[Any] = cast(Any, v1.list_namespaced_limit_range(namespace=ns)).items
            has_limit_range = bool(limit_ranges)

            deployments: list[Any] = cast(Any, apps_v1.list_namespaced_deployment(namespace=ns)).items
            deployment_count = len(deployments)
            deployment_names = {d.metadata.name for d in deployments}

            statefulsets: list[Any] = cast(Any, apps_v1.list_namespaced_stateful_set(namespace=ns)).items
            statefulset_count = len(statefulsets)

            ingresses: list[Any] = cast(Any, networking.list_namespaced_ingress(namespace=ns)).items
            ingress_count = len(ingresses)

            pvcs: list[Any] = cast(Any, v1.list_namespaced_persistent_volume_claim(namespace=ns)).items
            pvc_count = len(pvcs)

            service_accounts: list[Any] = cast(Any, v1.list_namespaced_service_account(namespace=ns)).items
            serviceaccount_count = len(service_accounts)

            errors: dict[str, str] = {}

            pdb_count = _collect(errors, "pdb_count", lambda: len(cast(Any,
                policy.list_namespaced_pod_disruption_budget(namespace=ns)
            ).items))

            hpas: list[Any] = cast(Any, autoscaling.list_namespaced_horizontal_pod_autoscaler(namespace=ns)).items
            hpa_targets = {h.spec.scale_target_ref.name for h in hpas}
            hpa_count = len(hpa_targets & deployment_names)

            def count(metric: str, *gvrs: GVR) -> int | None:
                return _collect(errors, metric, lambda: sum(
                    _count_custom(custom, gvr, ns) for gvr in gvrs  # noqa: B023 - called immediately
                ))

            vpa_count = count("vpa_count", _VPA)
            flux_count = count("flux_resources", _HELM_RELEASES, _KUSTOMIZATIONS)
            argocd_count = count("argocd_resources", _ARGOCD_APPLICATIONS)

            route_count: int | None = 0
            dc_count: int | None = 0
            if _backend == CliTool.oc:
                route_count = count("route_count", _OC_ROUTES)
                dc_count = count("deploymentconfig_count", _OC_DEPLOYMENTCONFIGS)

            stats.append(AdoptionStat(
                namespace=ns,
                pod_count=pod_count,
                pods_with_limits=pods_with_limits,
                pods_with_probes=pods_with_probes,
                has_network_policy=has_network_policy,
                has_resource_quota=has_resource_quota,
                has_limit_range=has_limit_range,
                deployment_count=deployment_count,
                statefulset_count=statefulset_count,
                ingress_count=ingress_count,
                pvc_count=pvc_count,
                serviceaccount_count=serviceaccount_count,
                pdb_count=pdb_count,
                hpa_count=hpa_count,
                vpa_count=vpa_count,
                flux_resources=flux_count,
                argocd_resources=argocd_count,
                route_count=route_count,
                deploymentconfig_count=dc_count,
                fetch_errors=errors,
            ))

        except ApiException as e:
            logger.warning("Skipping namespace %s: %s", ns, e)

    return stats


# ---------------------------------------------------------------------------
# Istio statistics
# ---------------------------------------------------------------------------

_ISTIO_API_VERSIONS = ("v1", "v1beta1", "v1alpha3")
_PEER_AUTHENTICATIONS = GVR("security.istio.io", ("v1", "v1beta1"), "peerauthentications")
_SERVICE_ENTRIES = GVR("networking.istio.io", _ISTIO_API_VERSIONS, "serviceentries")
DEFAULT_MESH_ROOT_NAMESPACE = "istio-system"


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
) -> list[IstioNamespaceStat]:
    v1 = client.CoreV1Api()
    custom = client.CustomObjectsApi()
    stats: list[IstioNamespaceStat] = []

    # The PeerAuthentication without selector in the root namespace applies mesh-wide.
    mesh_mode: str | None = None
    mesh_error: str | None = None
    try:
        mesh_mode = _namespace_wide_mtls(
            _list_custom(custom, _PEER_AUTHENTICATIONS, mesh_root_namespace) or [])
    except _FetchError as e:
        mesh_error = f"mesh-wide policy in {mesh_root_namespace}: {e}"

    for ns_info in namespace_infos:
        ns = ns_info.name

        try:
            pods: list[Any] = cast(Any, v1.list_namespaced_pod(namespace=ns)).items
        except ApiException as e:
            logger.warning("Skipping namespace %s: %s", ns, _reason(e))
            continue

        sidecar_count = sum(1 for pod in pods if _has_sidecar(pod))
        ambient_count = sum(1 for pod in pods if _is_ambient(pod) and not _has_sidecar(pod))

        errors: dict[str, str] = {}

        def count(metric: str, group: str, plural: str) -> int | None:
            gvr = GVR(group, _ISTIO_API_VERSIONS, plural)
            return _collect(errors, metric, lambda: _count_custom(custom, gvr, ns))  # noqa: B023

        peer_auths = _collect(errors, "peer_authentications",
                              lambda: _list_custom(custom, _PEER_AUTHENTICATIONS, ns) or [])  # noqa: B023
        mtls_mode: str | None = None
        if peer_auths is not None:
            if _namespace_wide_mtls(peer_auths) is None and mesh_error:
                errors["mtls_mode"] = mesh_error
            else:
                mtls_mode = _resolve_mtls(peer_auths, mesh_mode)

        stats.append(IstioNamespaceStat(
            namespace=ns,
            injection_mode=_injection_mode(ns_info.labels),
            pod_count=len(pods),
            sidecar_count=sidecar_count,
            ambient_count=ambient_count,
            virtual_services=count(
                "virtual_services", "networking.istio.io", "virtualservices"),
            destination_rules=count(
                "destination_rules", "networking.istio.io", "destinationrules"),
            gateways=count(
                "gateways", "networking.istio.io", "gateways"),
            service_entries=count(
                "service_entries", "networking.istio.io", "serviceentries"),
            workload_entries=count(
                "workload_entries", "networking.istio.io", "workloadentries"),
            peer_authentications=None if peer_auths is None else len(peer_auths),
            authorization_policies=count(
                "authorization_policies", "security.istio.io", "authorizationpolicies"),
            mtls_mode=mtls_mode,
            fetch_errors=errors,
        ))

    return stats


# ---------------------------------------------------------------------------
# Service entries (external services)
# ---------------------------------------------------------------------------

def get_service_entries(namespace_names: list[str]) -> list[ServiceEntryStat]:
    custom = client.CustomObjectsApi()
    entries: list[ServiceEntryStat] = []

    for ns in namespace_names:
        try:
            items = _list_custom(custom, _SERVICE_ENTRIES, ns) or []
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

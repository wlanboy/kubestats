"""Kubernetes / OpenShift data collection via the official Python client."""
from __future__ import annotations

import logging
from typing import Any, cast

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from kubestats.models import AdoptionStat, CRDStat, IstioNamespaceStat, NamespaceInfo, ServiceEntryStat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend selection (kubectl vs oc / OpenShift)
# ---------------------------------------------------------------------------

_backend: str = "kubectl"


def set_backend(backend: str) -> None:
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
    if _backend == "oc":
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

        if stat.namespaced:
            for ns in namespace_names:
                try:
                    result = cast(dict[str, Any], custom.list_namespaced_custom_object(
                        group=spec.group, version=version,
                        namespace=ns, plural=spec.names.plural,
                    ))
                    count = len(result.get("items", []))
                    if count:
                        stat.instances_by_namespace[ns] = count
                except ApiException:
                    pass
        else:
            try:
                result = cast(dict[str, Any], custom.list_cluster_custom_object(
                    group=spec.group, version=version, plural=spec.names.plural,
                ))
                count = len(result.get("items", []))
                if count:
                    stat.instances_by_namespace["(cluster)"] = count
            except ApiException:
                pass

        if include_empty or stat.total_instances > 0:
            stats.append(stat)

    return sorted(stats, key=lambda s: s.total_instances, reverse=True)


# ---------------------------------------------------------------------------
# Adoption metrics
# ---------------------------------------------------------------------------

def _count_custom(custom: client.CustomObjectsApi, group: str, versions: list[str],
                  namespace: str, plural: str) -> int:
    for version in versions:
        try:
            result = cast(dict[str, Any], custom.list_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural,
            ))
            return len(result.get("items", []))
        except ApiException as e:
            if e.status == 404:
                continue
    return 0


def get_adoption_stats(namespace_names: list[str]) -> list[AdoptionStat]:
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
            pods_with_probes = sum(
                1 for pod in pods
                if all(
                    c.liveness_probe and c.readiness_probe
                    for c in pod.spec.containers
                )
            )

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

            pdb_count = 0
            try:
                pdb_count = len(cast(Any,
                    policy.list_namespaced_pod_disruption_budget(namespace=ns)
                ).items)
            except ApiException:
                pass

            hpas: list[Any] = cast(Any, autoscaling.list_namespaced_horizontal_pod_autoscaler(namespace=ns)).items
            hpa_targets = {h.spec.scale_target_ref.name for h in hpas}
            hpa_count = len(hpa_targets & deployment_names)

            vpa_count = _count_custom(
                custom, "autoscaling.k8s.io", ["v1"], ns, "verticalpodautoscalers"
            )

            flux_count = (
                _count_custom(custom, "helm.toolkit.fluxcd.io",
                              ["v2", "v2beta2", "v2beta1"], ns, "helmreleases")
                + _count_custom(custom, "kustomize.toolkit.fluxcd.io",
                                ["v1", "v1beta2", "v1beta1"], ns, "kustomizations")
            )

            argocd_count = _count_custom(
                custom, "argoproj.io", ["v1alpha1"], ns, "applications"
            )

            route_count = 0
            dc_count = 0
            if _backend == "oc":
                route_count = _count_custom(
                    custom, "route.openshift.io", ["v1"], ns, "routes"
                )
                dc_count = _count_custom(
                    custom, "apps.openshift.io", ["v1"], ns, "deploymentconfigs"
                )

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
            ))

        except ApiException as e:
            logger.warning("Skipping namespace %s: %s", ns, e)

    return stats


# ---------------------------------------------------------------------------
# Istio statistics
# ---------------------------------------------------------------------------

def _istio_count(custom: client.CustomObjectsApi, group: str,
                 namespace: str, plural: str) -> int:
    return _count_custom(custom, group, ["v1", "v1beta1", "v1alpha3"], namespace, plural)


def _mtls_mode(custom: client.CustomObjectsApi, namespace: str) -> str:
    for version in ["v1", "v1beta1"]:
        try:
            result = cast(dict[str, Any], custom.list_namespaced_custom_object(
                group="security.istio.io", version=version,
                namespace=namespace, plural="peerauthentications",
            ))
            mtls_items: list[dict[str, Any]] = result.get("items", [])
            if not mtls_items:
                return "none"
            # Prefer the namespace-wide policy (no workload selector)
            for item in mtls_items:
                if not item.get("spec", {}).get("selector"):
                    return item.get("spec", {}).get("mtls", {}).get("mode", "none")
            return mtls_items[0].get("spec", {}).get("mtls", {}).get("mode", "none")
        except ApiException as e:
            if e.status == 404:
                continue
    return "none"


def get_istio_stats(namespace_infos: list[NamespaceInfo]) -> list[IstioNamespaceStat]:
    v1 = client.CoreV1Api()
    custom = client.CustomObjectsApi()
    stats: list[IstioNamespaceStat] = []

    for ns_info in namespace_infos:
        ns = ns_info.name
        injection_enabled = ns_info.labels.get("istio-injection") == "enabled"

        try:
            pods: list[Any] = cast(Any, v1.list_namespaced_pod(namespace=ns)).items
        except ApiException:
            continue

        pod_count = len(pods)
        sidecar_count = sum(
            1 for pod in pods
            if any(c.name == "istio-proxy" for c in (pod.spec.containers or []))
        )

        stats.append(IstioNamespaceStat(
            namespace=ns,
            injection_enabled=injection_enabled,
            pod_count=pod_count,
            sidecar_count=sidecar_count,
            virtual_services=_istio_count(
                custom, "networking.istio.io", ns, "virtualservices"),
            destination_rules=_istio_count(
                custom, "networking.istio.io", ns, "destinationrules"),
            gateways=_istio_count(
                custom, "networking.istio.io", ns, "gateways"),
            service_entries=_istio_count(
                custom, "networking.istio.io", ns, "serviceentries"),
            workload_entries=_istio_count(
                custom, "networking.istio.io", ns, "workloadentries"),
            peer_authentications=_istio_count(
                custom, "security.istio.io", ns, "peerauthentications"),
            authorization_policies=_istio_count(
                custom, "security.istio.io", ns, "authorizationpolicies"),
            mtls_mode=_mtls_mode(custom, ns),
        ))

    return stats


# ---------------------------------------------------------------------------
# Service entries (external services)
# ---------------------------------------------------------------------------

def get_service_entries(namespace_names: list[str]) -> list[ServiceEntryStat]:
    custom = client.CustomObjectsApi()
    entries: list[ServiceEntryStat] = []

    for ns in namespace_names:
        for version in ["v1", "v1beta1", "v1alpha3"]:
            try:
                result = cast(dict[str, Any], custom.list_namespaced_custom_object(
                    group="networking.istio.io", version=version,
                    namespace=ns, plural="serviceentries",
                ))
                for item in result.get("items", []):
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
                break   # found a working version
            except ApiException as e:
                if e.status == 404:
                    continue

    return entries

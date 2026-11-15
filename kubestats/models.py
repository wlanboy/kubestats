"""Data models for Kubernetes / OpenShift statistics."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CliTool(str, Enum):
    """Which CLI backend a cluster is being queried through."""
    kubectl = "kubectl"
    oc = "oc"


@dataclass(frozen=True)
class GVR:
    """A Kubernetes API group/plural, with the versions to try in order.

    Some CRDs (VPA, Flux, Argo CD, OpenShift routes, Istio, ...) serve the
    same resource under different versions depending on cluster age, so
    callers probe `versions` in order and use the first that responds.
    """
    group: str
    versions: tuple[str, ...]
    plural: str


@dataclass(frozen=True)
class FetchError:
    """A metric that could not be fetched, so its value is unknown rather than zero."""
    namespace: str
    metric: str
    reason: str


@dataclass
class NamespaceInfo:
    name: str
    labels: dict[str, str]


@dataclass
class CRDStat:
    name: str           # e.g. certificates.cert-manager.io
    group: str
    kind: str
    plural: str
    namespaced: bool
    instances_by_namespace: dict[str, int] = field(default_factory=dict)
    # namespace -> reason, for namespaces whose instance count is unknown
    fetch_errors: dict[str, str] = field(default_factory=dict)

    @property
    def total_instances(self) -> int:
        return sum(self.instances_by_namespace.values())

    @property
    def namespace_count(self) -> int:
        return sum(1 for v in self.instances_by_namespace.values() if v > 0)

    def adoption_pct(self, total_namespaces: int) -> float:
        if total_namespaces == 0:
            return 0.0
        return round(self.namespace_count * 100 / total_namespaces, 1)

    def error_rows(self) -> list[FetchError]:
        return [FetchError(ns, self.name, reason) for ns, reason in self.fetch_errors.items()]


@dataclass
class AdoptionStat:
    namespace: str
    pod_count: int
    pods_with_limits: int
    pods_with_probes: int
    has_network_policy: bool
    has_resource_quota: bool
    has_limit_range: bool
    deployment_count: int
    statefulset_count: int
    ingress_count: int
    pvc_count: int
    serviceaccount_count: int
    # Counts that are None could not be fetched; see fetch_errors.
    pdb_count: int | None
    hpa_count: int
    vpa_count: int | None
    flux_resources: int | None    # HelmReleases + Kustomizations
    argocd_resources: int | None  # Applications
    route_count: int | None = 0             # OpenShift Routes
    deploymentconfig_count: int | None = 0  # OpenShift DeploymentConfigs
    # metric (field name) -> reason
    fetch_errors: dict[str, str] = field(default_factory=dict)

    def error_rows(self) -> list[FetchError]:
        return [FetchError(self.namespace, metric, reason)
                for metric, reason in self.fetch_errors.items()]


@dataclass
class IstioNamespaceStat:
    namespace: str
    injection_mode: str     # sidecar | rev=<revision> | ambient | disabled | none
    pod_count: int
    sidecar_count: int
    # Counts and mtls_mode that are None could not be fetched; see fetch_errors.
    virtual_services: int | None
    destination_rules: int | None
    gateways: int | None
    service_entries: int | None
    workload_entries: int | None
    peer_authentications: int | None
    authorization_policies: int | None
    # STRICT | PERMISSIVE | DISABLE | none, with " (mesh)" suffix when the
    # mode comes from the mesh-wide policy in the root namespace
    mtls_mode: str | None
    ambient_count: int = 0  # pods redirected by the ambient data plane
    # metric (field name) -> reason
    fetch_errors: dict[str, str] = field(default_factory=dict)

    @property
    def injection_enabled(self) -> bool:
        return self.injection_mode not in ("none", "disabled")

    @property
    def coverage_pct(self) -> int | None:
        """Mesh coverage ((sidecars + ambient pods) / pods). None when the namespace has no pods."""
        if self.pod_count == 0:
            return None
        return (self.sidecar_count + self.ambient_count) * 100 // self.pod_count

    def error_rows(self) -> list[FetchError]:
        return [FetchError(self.namespace, metric, reason)
                for metric, reason in self.fetch_errors.items()]


@dataclass
class ServiceEntryStat:
    namespace: str
    name: str
    hosts: list[str]
    resolution: str
    ports: list[str]

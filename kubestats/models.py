"""Data models for Kubernetes / OpenShift statistics."""
from __future__ import annotations

from dataclasses import dataclass, field


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

    @property
    def total_instances(self) -> int:
        return sum(self.instances_by_namespace.values())

    @property
    def namespace_count(self) -> int:
        return sum(1 for v in self.instances_by_namespace.values() if v > 0)


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
    pdb_count: int
    hpa_count: int
    vpa_count: int
    flux_resources: int     # HelmReleases + Kustomizations
    argocd_resources: int   # Applications
    route_count: int = 0              # OpenShift Routes
    deploymentconfig_count: int = 0   # OpenShift DeploymentConfigs


@dataclass
class IstioNamespaceStat:
    namespace: str
    injection_enabled: bool
    pod_count: int
    sidecar_count: int
    virtual_services: int
    destination_rules: int
    gateways: int
    service_entries: int
    workload_entries: int
    peer_authentications: int
    authorization_policies: int
    mtls_mode: str          # STRICT | PERMISSIVE | DISABLE | none


@dataclass
class ServiceEntryStat:
    namespace: str
    name: str
    hosts: list[str]
    resolution: str
    ports: list[str]

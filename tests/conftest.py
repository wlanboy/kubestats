"""Shared pytest fixtures for kubestats tests."""
from __future__ import annotations

import pytest

from kubectl import (
    AdoptionStat,
    CRDStat,
    IstioNamespaceStat,
    NamespaceInfo,
    ServiceEntryStat,
)


@pytest.fixture()
def crd_stat() -> CRDStat:
    return CRDStat(
        name="certificates.cert-manager.io",
        group="cert-manager.io",
        kind="Certificate",
        plural="certificates",
        namespaced=True,
        instances_by_namespace={"ns-a": 3, "ns-b": 2, "ns-c": 0},
    )


@pytest.fixture()
def crd_stat_cluster() -> CRDStat:
    return CRDStat(
        name="clusterissuers.cert-manager.io",
        group="cert-manager.io",
        kind="ClusterIssuer",
        plural="clusterissuers",
        namespaced=False,
        instances_by_namespace={"(cluster)": 5},
    )


@pytest.fixture()
def adoption_stat() -> AdoptionStat:
    return AdoptionStat(
        namespace="default",
        pod_count=10,
        pods_with_limits=7,
        has_network_policy=True,
        deployment_count=3,
        pdb_count=1,
        hpa_count=2,
        flux_resources=4,
        argocd_resources=1,
    )


@pytest.fixture()
def adoption_stat_oc() -> AdoptionStat:
    return AdoptionStat(
        namespace="openshift-app",
        pod_count=5,
        pods_with_limits=5,
        has_network_policy=False,
        deployment_count=2,
        pdb_count=0,
        hpa_count=0,
        flux_resources=0,
        argocd_resources=0,
        route_count=3,
        deploymentconfig_count=2,
    )


@pytest.fixture()
def istio_stat() -> IstioNamespaceStat:
    return IstioNamespaceStat(
        namespace="prod",
        injection_enabled=True,
        pod_count=8,
        sidecar_count=6,
        virtual_services=2,
        destination_rules=1,
        gateways=1,
        service_entries=3,
        workload_entries=0,
        peer_authentications=1,
        authorization_policies=4,
        mtls_mode="STRICT",
    )


@pytest.fixture()
def istio_stat_no_injection() -> IstioNamespaceStat:
    return IstioNamespaceStat(
        namespace="dev",
        injection_enabled=False,
        pod_count=4,
        sidecar_count=0,
        virtual_services=0,
        destination_rules=0,
        gateways=0,
        service_entries=0,
        workload_entries=0,
        peer_authentications=0,
        authorization_policies=0,
        mtls_mode="none",
    )


@pytest.fixture()
def service_entry() -> ServiceEntryStat:
    return ServiceEntryStat(
        namespace="prod",
        name="external-api",
        hosts=["api.example.com", "api2.example.com"],
        resolution="DNS",
        ports=["443/HTTPS", "80/HTTP"],
    )


@pytest.fixture()
def namespace_info() -> NamespaceInfo:
    return NamespaceInfo(name="prod", labels={"istio-injection": "enabled"})

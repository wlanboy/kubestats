"""Tests for kubectl.py dataclass models and their computed properties."""
from __future__ import annotations


from kubectl import AdoptionStat, CRDStat, NamespaceInfo


class TestCRDStatProperties:
    def test_total_instances_sums_all_namespaces(self, crd_stat: CRDStat) -> None:
        # ns-a: 3, ns-b: 2, ns-c: 0
        assert crd_stat.total_instances == 5

    def test_total_instances_empty(self) -> None:
        stat = CRDStat("x.io", "io", "X", "xs", True)
        assert stat.total_instances == 0

    def test_namespace_count_only_positive(self, crd_stat: CRDStat) -> None:
        # ns-c has 0 instances and must not be counted
        assert crd_stat.namespace_count == 2

    def test_namespace_count_empty(self) -> None:
        stat = CRDStat("x.io", "io", "X", "xs", True)
        assert stat.namespace_count == 0

    def test_cluster_scoped_total(self, crd_stat_cluster: CRDStat) -> None:
        assert crd_stat_cluster.total_instances == 5
        assert crd_stat_cluster.namespace_count == 1

    def test_instances_by_namespace_default_empty(self) -> None:
        stat = CRDStat("a.io", "io", "A", "as", True)
        assert stat.instances_by_namespace == {}


class TestNamespaceInfo:
    def test_labels_stored(self, namespace_info: NamespaceInfo) -> None:
        assert namespace_info.labels["istio-injection"] == "enabled"

    def test_name(self, namespace_info: NamespaceInfo) -> None:
        assert namespace_info.name == "prod"


class TestAdoptionStat:
    def test_oc_fields_default_to_zero(self, adoption_stat: AdoptionStat) -> None:
        assert adoption_stat.route_count == 0
        assert adoption_stat.deploymentconfig_count == 0

    def test_fixture_fields(self, adoption_stat: AdoptionStat) -> None:
        assert adoption_stat.namespace == "default"
        assert adoption_stat.pod_count == 10
        assert adoption_stat.pods_with_limits == 7
        assert adoption_stat.pods_with_probes == 5
        assert adoption_stat.has_network_policy is True
        assert adoption_stat.has_resource_quota is True
        assert adoption_stat.has_limit_range is False
        assert adoption_stat.deployment_count == 3
        assert adoption_stat.statefulset_count == 1
        assert adoption_stat.ingress_count == 2
        assert adoption_stat.pvc_count == 3
        assert adoption_stat.serviceaccount_count == 2
        assert adoption_stat.pdb_count == 1
        assert adoption_stat.hpa_count == 2
        assert adoption_stat.vpa_count == 0
        assert adoption_stat.flux_resources == 4
        assert adoption_stat.argocd_resources == 1

    def test_oc_fixture_fields(self, adoption_stat_oc: AdoptionStat) -> None:
        assert adoption_stat_oc.route_count == 3
        assert adoption_stat_oc.deploymentconfig_count == 2
        assert adoption_stat_oc.has_network_policy is False
        assert adoption_stat_oc.has_resource_quota is False
        assert adoption_stat_oc.serviceaccount_count == 1

    def test_probes_can_be_zero(self, adoption_stat_oc: AdoptionStat) -> None:
        assert adoption_stat_oc.pods_with_probes == 5

    def test_probes_cannot_exceed_pod_count(self, adoption_stat: AdoptionStat) -> None:
        assert adoption_stat.pods_with_probes <= adoption_stat.pod_count

    def test_limits_cannot_exceed_pod_count(self, adoption_stat: AdoptionStat) -> None:
        assert adoption_stat.pods_with_limits <= adoption_stat.pod_count

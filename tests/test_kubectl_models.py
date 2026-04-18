"""Tests for kubectl.py dataclass models and their computed properties."""
from __future__ import annotations


from kubectl import CRDStat, NamespaceInfo


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

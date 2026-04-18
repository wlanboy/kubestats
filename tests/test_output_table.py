"""Tests for output_table.py Rich table rendering."""
from __future__ import annotations

from rich.table import Table

import output_table
from kubectl import AdoptionStat, CRDStat, IstioNamespaceStat, ServiceEntryStat


class TestHelpers:
    def test_pct_normal(self) -> None:
        assert output_table._pct(3, 4) == "75%"

    def test_pct_zero_total(self) -> None:
        assert output_table._pct(0, 0) == "n/a"

    def test_pct_full(self) -> None:
        assert output_table._pct(10, 10) == "100%"

    def test_pct_truncates_not_rounds(self) -> None:
        # 1/3 = 33.33... → integer division → 33%
        assert output_table._pct(1, 3) == "33%"

    def test_yn_true_colored(self) -> None:
        assert output_table._yn(True) == "[green]yes[/green]"

    def test_yn_false_colored(self) -> None:
        assert output_table._yn(False) == "[red]no[/red]"

    def test_yn_no_color(self) -> None:
        assert output_table._yn(True, color=False) == "yes"
        assert output_table._yn(False, color=False) == "no"


class TestRenderCrds:
    def test_returns_table(self, crd_stat: CRDStat) -> None:
        result = output_table.render_crds([crd_stat], 5)
        assert isinstance(result, Table)

    def test_column_count(self, crd_stat: CRDStat) -> None:
        result = output_table.render_crds([crd_stat], 5)
        assert len(result.columns) == 4

    def test_row_count(self, crd_stat: CRDStat) -> None:
        result = output_table.render_crds([crd_stat], 5)
        assert result.row_count == 1

    def test_empty_stats(self) -> None:
        result = output_table.render_crds([], 0)
        assert result.row_count == 0


class TestRenderCrdsPerNamespace:
    def test_returns_table(self, crd_stat: CRDStat) -> None:
        result = output_table.render_crds_per_namespace(
            [crd_stat], ["ns-a", "ns-b", "ns-c"]
        )
        assert isinstance(result, Table)

    def test_row_count_matches_namespaces(self, crd_stat: CRDStat) -> None:
        ns_names = ["ns-a", "ns-b", "ns-c"]
        result = output_table.render_crds_per_namespace([crd_stat], ns_names)
        assert result.row_count == len(ns_names)

    def test_column_count_includes_namespace_col(self, crd_stat: CRDStat) -> None:
        result = output_table.render_crds_per_namespace([crd_stat], ["ns-a"])
        # 1 NAMESPACE column + 1 CRD column
        assert len(result.columns) == 2


class TestRenderAdoption:
    def test_returns_table(self, adoption_stat: AdoptionStat) -> None:
        result = output_table.render_adoption([adoption_stat])
        assert isinstance(result, Table)

    def test_no_oc_columns_by_default(self, adoption_stat: AdoptionStat) -> None:
        result = output_table.render_adoption([adoption_stat])
        col_names = [c.header for c in result.columns]
        assert "ROUTES" not in col_names
        assert "DC" not in col_names

    def test_oc_columns_shown_when_data_present(
        self, adoption_stat: AdoptionStat, adoption_stat_oc: AdoptionStat
    ) -> None:
        result = output_table.render_adoption([adoption_stat, adoption_stat_oc])
        col_names = [c.header for c in result.columns]
        assert "ROUTES" in col_names
        assert "DC" in col_names

    def test_row_count(self, adoption_stat: AdoptionStat) -> None:
        result = output_table.render_adoption([adoption_stat])
        assert result.row_count == 1


class TestRenderIstio:
    def test_returns_table(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio([istio_stat])
        assert isinstance(result, Table)

    def test_column_count(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio([istio_stat])
        assert len(result.columns) == 5

    def test_row_count(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio([istio_stat])
        assert result.row_count == 1


class TestRenderIstioTraffic:
    def test_returns_table(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio_traffic([istio_stat])
        assert isinstance(result, Table)

    def test_column_count(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio_traffic([istio_stat])
        assert len(result.columns) == 6

    def test_correct_columns(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio_traffic([istio_stat])
        headers = [c.header for c in result.columns]
        assert "VirtualServices" in headers
        assert "DestinationRules" in headers
        assert "Gateways" in headers


class TestRenderIstioPolicies:
    def test_returns_table(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio_policies([istio_stat])
        assert isinstance(result, Table)

    def test_column_count(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio_policies([istio_stat])
        assert len(result.columns) == 4

    def test_mtls_column_present(self, istio_stat: IstioNamespaceStat) -> None:
        result = output_table.render_istio_policies([istio_stat])
        headers = [c.header for c in result.columns]
        assert "mTLS-MODE" in headers


class TestRenderServiceEntries:
    def test_returns_table(self, service_entry: "ServiceEntryStat") -> None:
        result = output_table.render_service_entries([service_entry])
        assert isinstance(result, Table)

    def test_column_count(self, service_entry: "ServiceEntryStat") -> None:
        result = output_table.render_service_entries([service_entry])
        assert len(result.columns) == 5

    def test_row_count(self, service_entry: "ServiceEntryStat") -> None:
        result = output_table.render_service_entries([service_entry])
        assert result.row_count == 1

    def test_empty(self) -> None:
        result = output_table.render_service_entries([])
        assert result.row_count == 0

"""Tests for output_csv.py CSV serialization."""
from __future__ import annotations

import csv
import io

import pytest

import output_csv
from kubectl import AdoptionStat, CRDStat, IstioNamespaceStat, ServiceEntryStat


def _parse(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


class TestRenderCrds:
    def test_returns_csv_with_header(self, crd_stat: CRDStat) -> None:
        result = output_csv.render_crds([crd_stat], 5)
        rows = _parse(result)
        assert len(rows) == 1

    def test_computed_adoption_pct(self, crd_stat: CRDStat) -> None:
        # namespace_count=2, total=5 → 40.0%
        rows = _parse(output_csv.render_crds([crd_stat], 5))
        assert float(rows[0]["adoption_pct"]) == pytest.approx(40.0)

    def test_total_namespaces_column(self, crd_stat: CRDStat) -> None:
        rows = _parse(output_csv.render_crds([crd_stat], 5))
        assert rows[0]["total_namespaces"] == "5"

    def test_total_instances_column(self, crd_stat: CRDStat) -> None:
        rows = _parse(output_csv.render_crds([crd_stat], 5))
        assert rows[0]["total_instances"] == "5"

    def test_zero_total_namespaces_gives_zero_pct(self, crd_stat: CRDStat) -> None:
        rows = _parse(output_csv.render_crds([crd_stat], 0))
        assert float(rows[0]["adoption_pct"]) == 0.0

    def test_empty_stats(self) -> None:
        rows = _parse(output_csv.render_crds([], 0))
        assert rows == []


class TestRenderAdoption:
    def test_returns_csv_with_all_fields(self, adoption_stat: AdoptionStat) -> None:
        rows = _parse(output_csv.render_adoption([adoption_stat]))
        assert len(rows) == 1
        assert rows[0]["namespace"] == "default"
        assert rows[0]["pod_count"] == "10"
        assert rows[0]["pods_with_limits"] == "7"
        assert rows[0]["has_network_policy"] == "True"

    def test_route_and_dc_default_zero(self, adoption_stat: AdoptionStat) -> None:
        rows = _parse(output_csv.render_adoption([adoption_stat]))
        assert rows[0]["route_count"] == "0"
        assert rows[0]["deploymentconfig_count"] == "0"

    def test_multiple_rows(
        self, adoption_stat: AdoptionStat, adoption_stat_oc: AdoptionStat
    ) -> None:
        rows = _parse(output_csv.render_adoption([adoption_stat, adoption_stat_oc]))
        assert len(rows) == 2

    def test_oc_fields(self, adoption_stat_oc: AdoptionStat) -> None:
        rows = _parse(output_csv.render_adoption([adoption_stat_oc]))
        assert rows[0]["route_count"] == "3"
        assert rows[0]["deploymentconfig_count"] == "2"


class TestRenderIstio:
    def test_returns_all_12_fields(self, istio_stat: IstioNamespaceStat) -> None:
        rows = _parse(output_csv.render_istio([istio_stat]))
        assert len(rows) == 1
        assert len(rows[0]) == 12

    def test_values(self, istio_stat: IstioNamespaceStat) -> None:
        rows = _parse(output_csv.render_istio([istio_stat]))
        assert rows[0]["namespace"] == "prod"
        assert rows[0]["mtls_mode"] == "STRICT"
        assert rows[0]["injection_enabled"] == "True"


class TestRenderIstioTraffic:
    def test_only_traffic_columns(self, istio_stat: IstioNamespaceStat) -> None:
        rows = _parse(output_csv.render_istio_traffic([istio_stat]))
        assert set(rows[0].keys()) == {
            "namespace", "virtual_services", "destination_rules",
            "gateways", "service_entries", "workload_entries",
        }

    def test_values(self, istio_stat: IstioNamespaceStat) -> None:
        rows = _parse(output_csv.render_istio_traffic([istio_stat]))
        assert rows[0]["virtual_services"] == "2"
        assert rows[0]["destination_rules"] == "1"


class TestRenderIstioPolicies:
    def test_only_policy_columns(self, istio_stat: IstioNamespaceStat) -> None:
        rows = _parse(output_csv.render_istio_policies([istio_stat]))
        assert set(rows[0].keys()) == {
            "namespace", "peer_authentications", "authorization_policies", "mtls_mode",
        }

    def test_mtls_mode(self, istio_stat: IstioNamespaceStat) -> None:
        rows = _parse(output_csv.render_istio_policies([istio_stat]))
        assert rows[0]["mtls_mode"] == "STRICT"

    def test_none_mode(self, istio_stat_no_injection: IstioNamespaceStat) -> None:
        rows = _parse(output_csv.render_istio_policies([istio_stat_no_injection]))
        assert rows[0]["mtls_mode"] == "none"


class TestRenderServiceEntries:
    def test_hosts_joined(self, service_entry: ServiceEntryStat) -> None:
        rows = _parse(output_csv.render_service_entries([service_entry]))
        assert rows[0]["hosts"] == "api.example.com; api2.example.com"

    def test_ports_joined(self, service_entry: ServiceEntryStat) -> None:
        rows = _parse(output_csv.render_service_entries([service_entry]))
        assert rows[0]["ports"] == "443/HTTPS; 80/HTTP"

    def test_resolution(self, service_entry: ServiceEntryStat) -> None:
        rows = _parse(output_csv.render_service_entries([service_entry]))
        assert rows[0]["resolution"] == "DNS"

    def test_empty(self) -> None:
        rows = _parse(output_csv.render_service_entries([]))
        assert rows == []

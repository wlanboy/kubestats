"""Tests for output_json.py JSON serialization."""
from __future__ import annotations

import json


import output_json
from kubectl import AdoptionStat, CRDStat, IstioNamespaceStat, ServiceEntryStat


class TestRenderCrds:
    def test_returns_valid_json(self, crd_stat: CRDStat) -> None:
        result = output_json.render_crds([crd_stat])
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_contains_name_and_group(self, crd_stat: CRDStat) -> None:
        parsed = json.loads(output_json.render_crds([crd_stat]))
        assert parsed[0]["name"] == "certificates.cert-manager.io"
        assert parsed[0]["group"] == "cert-manager.io"

    def test_instances_by_namespace_included(self, crd_stat: CRDStat) -> None:
        parsed = json.loads(output_json.render_crds([crd_stat]))
        assert "instances_by_namespace" in parsed[0]

    def test_empty_list(self) -> None:
        assert json.loads(output_json.render_crds([])) == []


class TestRenderAdoption:
    def test_returns_valid_json(self, adoption_stat: AdoptionStat) -> None:
        result = output_json.render_adoption([adoption_stat])
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert parsed[0]["namespace"] == "default"

    def test_all_fields_present(self, adoption_stat: AdoptionStat) -> None:
        parsed = json.loads(output_json.render_adoption([adoption_stat]))
        entry = parsed[0]
        for field in ("pod_count", "pods_with_limits", "has_network_policy",
                      "deployment_count", "pdb_count", "hpa_count",
                      "flux_resources", "argocd_resources"):
            assert field in entry


class TestRenderIstio:
    def test_returns_valid_json(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio([istio_stat]))
        assert isinstance(parsed, list)
        assert parsed[0]["namespace"] == "prod"

    def test_all_fields_present(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio([istio_stat]))
        entry = parsed[0]
        for field in ("injection_enabled", "pod_count", "sidecar_count",
                      "virtual_services", "destination_rules", "gateways",
                      "service_entries", "workload_entries",
                      "peer_authentications", "authorization_policies", "mtls_mode"):
            assert field in entry


class TestRenderIstioTraffic:
    def test_returns_valid_json(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio_traffic([istio_stat]))
        assert isinstance(parsed, list)

    def test_only_traffic_fields(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio_traffic([istio_stat]))
        entry = parsed[0]
        assert set(entry.keys()) == {
            "namespace", "virtual_services", "destination_rules",
            "gateways", "service_entries", "workload_entries",
        }

    def test_values_correct(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio_traffic([istio_stat]))
        assert parsed[0]["virtual_services"] == 2
        assert parsed[0]["gateways"] == 1


class TestRenderIstioPolicies:
    def test_returns_valid_json(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio_policies([istio_stat]))
        assert isinstance(parsed, list)

    def test_only_policy_fields(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio_policies([istio_stat]))
        entry = parsed[0]
        assert set(entry.keys()) == {
            "namespace", "peer_authentications", "authorization_policies", "mtls_mode",
        }

    def test_mtls_mode_value(self, istio_stat: IstioNamespaceStat) -> None:
        parsed = json.loads(output_json.render_istio_policies([istio_stat]))
        assert parsed[0]["mtls_mode"] == "STRICT"


class TestRenderServiceEntries:
    def test_returns_valid_json(self, service_entry: ServiceEntryStat) -> None:
        parsed = json.loads(output_json.render_service_entries([service_entry]))
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "external-api"

    def test_hosts_is_list(self, service_entry: ServiceEntryStat) -> None:
        parsed = json.loads(output_json.render_service_entries([service_entry]))
        assert isinstance(parsed[0]["hosts"], list)
        assert len(parsed[0]["hosts"]) == 2


class TestRenderAll:
    def test_top_level_keys(
        self,
        crd_stat: CRDStat,
        adoption_stat: AdoptionStat,
        istio_stat: IstioNamespaceStat,
        service_entry: ServiceEntryStat,
    ) -> None:
        result = output_json.render_all(
            [crd_stat], [adoption_stat], [istio_stat], [service_entry]
        )
        parsed = json.loads(result)
        assert set(parsed.keys()) == {"crds", "adoption", "istio", "service_entries"}

    def test_all_sections_are_lists(
        self,
        crd_stat: CRDStat,
        adoption_stat: AdoptionStat,
        istio_stat: IstioNamespaceStat,
        service_entry: ServiceEntryStat,
    ) -> None:
        parsed = json.loads(
            output_json.render_all([crd_stat], [adoption_stat], [istio_stat], [service_entry])
        )
        for key in ("crds", "adoption", "istio", "service_entries"):
            assert isinstance(parsed[key], list)

    def test_empty_inputs(self) -> None:
        parsed = json.loads(output_json.render_all([], [], [], []))
        assert all(parsed[k] == [] for k in ("crds", "adoption", "istio", "service_entries"))

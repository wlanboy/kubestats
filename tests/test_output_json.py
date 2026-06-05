"""Tests for output_json.py JSON serialization."""
from __future__ import annotations

import json

from kubestats import output_json
from kubestats.kubectl import AdoptionStat, CRDStat, IstioNamespaceStat, ServiceEntryStat


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
        expected = {
            "pod_count", "pods_with_limits", "pods_with_probes",
            "has_network_policy", "has_resource_quota", "has_limit_range",
            "deployment_count", "statefulset_count", "ingress_count",
            "pvc_count", "serviceaccount_count",
            "pdb_count", "hpa_count", "vpa_count",
            "flux_resources", "argocd_resources",
            "route_count", "deploymentconfig_count",
        }
        for field in expected:
            assert field in entry, f"field {field!r} missing from JSON output"

    def test_boolean_fields_are_bool(self, adoption_stat: AdoptionStat) -> None:
        parsed = json.loads(output_json.render_adoption([adoption_stat]))
        entry = parsed[0]
        assert entry["has_network_policy"] is True
        assert entry["has_resource_quota"] is True
        assert entry["has_limit_range"] is False

    def test_numeric_fields_are_int(self, adoption_stat: AdoptionStat) -> None:
        parsed = json.loads(output_json.render_adoption([adoption_stat]))
        entry = parsed[0]
        for field in ("pod_count", "pods_with_limits", "pods_with_probes",
                      "statefulset_count", "ingress_count", "pvc_count",
                      "serviceaccount_count", "vpa_count"):
            assert isinstance(entry[field], int), f"{field!r} should be int"

    def test_oc_fields_present_and_zero_by_default(self, adoption_stat: AdoptionStat) -> None:
        parsed = json.loads(output_json.render_adoption([adoption_stat]))
        assert parsed[0]["route_count"] == 0
        assert parsed[0]["deploymentconfig_count"] == 0

    def test_oc_fields_with_values(self, adoption_stat_oc: AdoptionStat) -> None:
        parsed = json.loads(output_json.render_adoption([adoption_stat_oc]))
        assert parsed[0]["route_count"] == 3
        assert parsed[0]["deploymentconfig_count"] == 2

    def test_empty_list(self) -> None:
        assert json.loads(output_json.render_adoption([])) == []


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

    def test_adoption_section_contains_new_fields(
        self,
        crd_stat: CRDStat,
        adoption_stat: AdoptionStat,
        istio_stat: IstioNamespaceStat,
        service_entry: ServiceEntryStat,
    ) -> None:
        parsed = json.loads(
            output_json.render_all([crd_stat], [adoption_stat], [istio_stat], [service_entry])
        )
        entry = parsed["adoption"][0]
        for field in ("pods_with_probes", "has_resource_quota", "has_limit_range",
                      "statefulset_count", "ingress_count", "pvc_count",
                      "serviceaccount_count", "vpa_count"):
            assert field in entry, f"field {field!r} missing from render_all adoption section"

    def test_empty_inputs(self) -> None:
        parsed = json.loads(output_json.render_all([], [], [], []))
        assert all(parsed[k] == [] for k in ("crds", "adoption", "istio", "service_entries"))

"""CSV serialization of report data."""
from __future__ import annotations

import csv
import io
from dataclasses import asdict

from .models import AdoptionStat, CRDStat, IstioNamespaceStat, ServiceEntryStat


def _buf(fields: list[str]) -> tuple[io.StringIO, csv.DictWriter]:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    return buf, writer


def _rows(stats: list, fields: list[str]) -> str:
    """Render dataclass instances as CSV, projected onto `fields` (extra fields ignored)."""
    buf, writer = _buf(fields)
    for s in stats:
        writer.writerow(asdict(s))
    return buf.getvalue()


def render_crds(stats: list[CRDStat], total_namespaces: int) -> str:
    fields = ["name", "group", "kind", "plural", "namespaced",
              "namespace_count", "total_namespaces", "total_instances", "adoption_pct"]
    buf, writer = _buf(fields)
    for s in stats:
        writer.writerow({
            "name": s.name,
            "group": s.group,
            "kind": s.kind,
            "plural": s.plural,
            "namespaced": s.namespaced,
            "namespace_count": s.namespace_count,
            "total_namespaces": total_namespaces,
            "total_instances": s.total_instances,
            "adoption_pct": s.adoption_pct(total_namespaces),
        })
    return buf.getvalue()


def render_adoption(stats: list[AdoptionStat]) -> str:
    return _rows(stats, [
        "namespace", "pod_count", "pods_with_limits", "pods_with_probes",
        "has_network_policy", "has_resource_quota", "has_limit_range",
        "deployment_count", "statefulset_count", "ingress_count", "pvc_count", "serviceaccount_count",
        "pdb_count", "hpa_count", "vpa_count",
        "flux_resources", "argocd_resources",
        "route_count", "deploymentconfig_count",
    ])


def render_istio(stats: list[IstioNamespaceStat]) -> str:
    return _rows(stats, [
        "namespace", "injection_enabled", "pod_count", "sidecar_count",
        "virtual_services", "destination_rules", "gateways",
        "service_entries", "workload_entries",
        "peer_authentications", "authorization_policies", "mtls_mode",
    ])


def render_istio_traffic(stats: list[IstioNamespaceStat]) -> str:
    return _rows(stats, [
        "namespace", "virtual_services", "destination_rules",
        "gateways", "service_entries", "workload_entries",
    ])


def render_istio_policies(stats: list[IstioNamespaceStat]) -> str:
    return _rows(stats, [
        "namespace", "peer_authentications", "authorization_policies", "mtls_mode",
    ])


def render_service_entries(entries: list[ServiceEntryStat]) -> str:
    fields = ["namespace", "name", "hosts", "resolution", "ports"]
    buf, writer = _buf(fields)
    for e in entries:
        writer.writerow({
            "namespace": e.namespace,
            "name": e.name,
            "hosts": "; ".join(e.hosts),
            "resolution": e.resolution,
            "ports": "; ".join(e.ports),
        })
    return buf.getvalue()

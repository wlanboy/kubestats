"""Rich table rendering — returns Table renderables, never prints directly."""
from __future__ import annotations

from rich import box
from rich.table import Table

from .models import (
    AdoptionStat,
    CRDStat,
    FetchError,
    IstioNamespaceStat,
    ServiceEntryStat,
)

UNKNOWN = "?"   # value could not be fetched (see the fetch error list)

_MTLS_COLOR: dict[str, str] = {
    "STRICT": "green",
    "PERMISSIVE": "yellow",
    "DISABLE": "red",
    "none": "dim",
}


def _n(value: object) -> str:
    """Cell text for a value; None (not fetched) becomes '?'."""
    return UNKNOWN if value is None else str(value)


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{part * 100 // total}%"


def _yn(value: bool, *, color: bool = True) -> str:
    if value:
        return "[green]yes[/green]" if color else "yes"
    return "[red]no[/red]" if color else "no"


def _table(title: str, first_column: str) -> Table:
    """A table with the standard title/box styling and its first (identifying) column."""
    table = Table(title=title, box=box.SIMPLE_HEAD)
    table.add_column(first_column, style="cyan", no_wrap=True)
    return table


def _add_rows(table: Table, stats: list, *fields: str) -> None:
    """Add one row per stat, projecting each onto `fields` in order."""
    for s in stats:
        table.add_row(*(_n(getattr(s, f)) for f in fields))


# ---------------------------------------------------------------------------
# CRD adoption
# ---------------------------------------------------------------------------

def render_crds(stats: list[CRDStat], total_namespaces: int) -> Table:
    table = _table("Custom Resource Adoption", "CRD")
    table.add_column("NAMESPACES", justify="right")
    table.add_column("INSTANCES", justify="right")
    table.add_column("ADOPTION", justify="right")

    for s in stats:
        # With fetch errors the counts are lower bounds: "3+?" instead of "3".
        unknown = f"+{UNKNOWN}" if s.fetch_errors else ""
        table.add_row(
            s.name,
            f"{s.namespace_count}{unknown} / {total_namespaces}",
            f"{s.total_instances}{unknown}",
            _pct(s.namespace_count, total_namespaces) + unknown,
        )

    return table


def render_crds_per_namespace(stats: list[CRDStat],
                               namespace_names: list[str]) -> Table:
    """Per-namespace breakdown: rows = namespaces, columns = CRDs."""
    table = _table("CRD Instances per Namespace", "NAMESPACE")
    for s in stats:
        table.add_column(f"{s.kind}\n[dim]{s.group}[/dim]", justify="right")

    for ns in namespace_names:
        row = [ns] + [
            UNKNOWN if ns in s.fetch_errors else str(s.instances_by_namespace.get(ns, 0))
            for s in stats
        ]
        table.add_row(*row)

    return table


# ---------------------------------------------------------------------------
# Adoption rate
# ---------------------------------------------------------------------------

def render_adoption(stats: list[AdoptionStat]) -> Table:
    show_oc = any(s.route_count != 0 or s.deploymentconfig_count != 0 for s in stats)

    table = _table("Adoption Rate per Namespace", "NAMESPACE")
    table.add_column("PODS", justify="right")
    table.add_column("LIMITS", justify="right")
    table.add_column("PROBES", justify="right")
    table.add_column("NETPOL", justify="center")
    table.add_column("QUOTA", justify="center")
    table.add_column("LIMITRANGE", justify="center")
    table.add_column("DEPLOYS", justify="right")
    table.add_column("STS", justify="right")
    table.add_column("INGRESS", justify="right")
    table.add_column("PVC", justify="right")
    table.add_column("SA", justify="right")
    table.add_column("PDB", justify="right")
    table.add_column("HPA", justify="right")
    table.add_column("VPA", justify="right")
    table.add_column("FLUX", justify="right")
    table.add_column("ARGO", justify="right")
    if show_oc:
        table.add_column("ROUTES", justify="right")
        table.add_column("DC", justify="right")

    for s in stats:
        row = [
            s.namespace,
            str(s.pod_count),
            f"{s.pods_with_limits}/{s.pod_count}",
            f"{s.pods_with_probes}/{s.pod_count}",
            _yn(s.has_network_policy),
            _yn(s.has_resource_quota),
            _yn(s.has_limit_range),
            str(s.deployment_count),
            str(s.statefulset_count),
            str(s.ingress_count),
            str(s.pvc_count),
            str(s.serviceaccount_count),
            _n(s.pdb_count),
            str(s.hpa_count),
            _n(s.vpa_count),
            _n(s.flux_resources),
            _n(s.argocd_resources),
        ]
        if show_oc:
            row += [_n(s.route_count), _n(s.deploymentconfig_count)]
        table.add_row(*row)

    return table


# ---------------------------------------------------------------------------
# Istio enrollment
# ---------------------------------------------------------------------------

def render_istio(stats: list[IstioNamespaceStat]) -> Table:
    table = _table("Istio Namespace Enrollment", "NAMESPACE")
    show_ambient = any(s.ambient_count for s in stats)

    table.add_column("INJECTION", justify="center")
    table.add_column("SIDECARS", justify="right")
    if show_ambient:
        table.add_column("AMBIENT", justify="right")
    table.add_column("PODS", justify="right")
    table.add_column("COVERAGE", justify="right")

    for s in stats:
        coverage = "n/a" if s.coverage_pct is None else f"{s.coverage_pct}%"
        color = "green" if s.injection_enabled else "red"
        row = [
            s.namespace,
            f"[{color}]{s.injection_mode}[/{color}]",
            str(s.sidecar_count),
        ]
        if show_ambient:
            row.append(str(s.ambient_count))
        row += [str(s.pod_count), coverage]
        table.add_row(*row)

    return table


# ---------------------------------------------------------------------------
# Istio traffic policies
# ---------------------------------------------------------------------------

def render_istio_traffic(stats: list[IstioNamespaceStat]) -> Table:
    table = _table("Istio Traffic Policies per Namespace", "NAMESPACE")
    table.add_column("VirtualServices", justify="right")
    table.add_column("DestinationRules", justify="right")
    table.add_column("Gateways", justify="right")
    table.add_column("ServiceEntries", justify="right")
    table.add_column("WorkloadEntries", justify="right")

    _add_rows(table, stats, "namespace", "virtual_services", "destination_rules",
              "gateways", "service_entries", "workload_entries")

    return table


# ---------------------------------------------------------------------------
# Istio security policies
# ---------------------------------------------------------------------------

def render_istio_policies(stats: list[IstioNamespaceStat]) -> Table:
    table = _table("Istio Security Policies per Namespace", "NAMESPACE")
    table.add_column("PeerAuthentication", justify="right")
    table.add_column("AuthorizationPolicies", justify="right")
    table.add_column("mTLS-MODE", justify="center")

    for s in stats:
        mode = _n(s.mtls_mode)
        # "STRICT (mesh)" is colored like "STRICT"
        color = _MTLS_COLOR.get(mode.split(" ", 1)[0], "white")
        table.add_row(
            s.namespace,
            _n(s.peer_authentications),
            _n(s.authorization_policies),
            f"[{color}]{mode}[/{color}]",
        )

    return table


# ---------------------------------------------------------------------------
# External services (ServiceEntries)
# ---------------------------------------------------------------------------

def render_service_entries(entries: list[ServiceEntryStat]) -> Table:
    table = _table("Istio External Services (ServiceEntries)", "NAMESPACE")
    table.add_column("NAME", no_wrap=True)
    table.add_column("HOSTS")
    table.add_column("RESOLUTION", justify="center")
    table.add_column("PORTS")

    for e in entries:
        table.add_row(
            e.namespace,
            e.name,
            ", ".join(e.hosts),
            e.resolution,
            ", ".join(e.ports),
        )

    return table


# ---------------------------------------------------------------------------
# Fetch errors
# ---------------------------------------------------------------------------

def render_fetch_errors(errors: list[FetchError]) -> Table:
    table = _table(f"Fetch errors (values shown as '{UNKNOWN}' are unknown, not zero)",
                   "NAMESPACE")
    table.add_column("METRIC")
    table.add_column("REASON", style="red")

    for e in sorted(errors, key=lambda e: (e.namespace, e.metric)):
        table.add_row(e.namespace, e.metric, e.reason)

    return table

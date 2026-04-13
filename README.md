# kubestats

A command-line tool for gathering and visualizing statistics about Kubernetes and OpenShift cluster resources — focused on adoption rates, operator usage, and service mesh coverage, always broken down **per namespace**.

Supports both **`kubectl`** (standard Kubernetes) and **`oc`** (OpenShift) backends via the global `--tool` flag.

---

## Backend: `kubectl` vs `oc`

| Aspect | `kubectl` (default) | `oc` (OpenShift) |
| --- | --- | --- |
| Namespace source | `Namespace` API | `project.openshift.io/v1/projects` (falls back to `Namespace`) |
| Extra adoption columns | — | `ROUTES`, `DC` (DeploymentConfigs) |
| Route support | — | `route.openshift.io/v1` Routes counted per namespace |
| DeploymentConfig support | — | `apps.openshift.io/v1` DeploymentConfigs counted per namespace |
| Config / auth | `~/.kube/config` | `~/.kube/config` (populated by `oc login`) |
| CRD / Istio / Flux data | full | full (identical API) |

OpenShift clusters expose the full Kubernetes API in addition to their own extensions, so all existing reports (CRDs, Istio, Flux, ArgoCD, …) work unchanged in `oc` mode. The extra columns appear automatically when there is OpenShift-specific data.

```bash
# Kubernetes cluster
analytics adoption

# OpenShift cluster
analytics --tool oc adoption
```

---

## Global Options

```bash
--tool, -t  [kubectl|oc]   CLI backend to use (default: kubectl)
```

The `--tool` flag must come **before** the sub-command:

```bash
analytics --tool oc crds --breakdown
analytics --tool oc all --output json --output-dir ./reports/
```

---

## Commands

### `analytics crds`

CRD adoption rate — how many instances of each CRD exist across which namespaces.

```bash
analytics crds [--namespace NS] [--breakdown] [--output table|json|csv] [--output-dir DIR]
```

Output (`--output table`):

```text
                 Custom Resource Adoption
 CRD                                         NAMESPACES  INSTANCES  ADOPTION
 certificates.cert-manager.io               12 / 24      42         50%
 issuers.cert-manager.io                     8 / 24      18         33%
 helmreleases.helm.toolkit.fluxcd.io        19 / 24      91         79%
 kustomizations.kustomize.toolkit.fluxcd.io 14 / 24      34         58%
 backupschedules.velero.io                   2 / 24       5          8%
```

Only CRDs with at least one instance are shown. Cluster-scoped CRDs are counted under the `(cluster)` key.

With `--breakdown`, a second table shows the raw instance count per namespace × CRD:

```text
          CRD Instances per Namespace
 NAMESPACE    certificates  issuers  helmreleases  backupschedules
 team-alpha   3             1        5             1
 team-beta    0             0        2             0
 platform     8             6        12            4
```

**Examples:**

```bash
# All namespaces
analytics crds

# One namespace with breakdown
analytics crds --namespace platform --breakdown

# Export as JSON
analytics crds --output json > crds.json

# OpenShift — same flags, uses Projects as namespace source
analytics --tool oc crds --breakdown
```

---

### `analytics adoption`

Per-namespace adoption metrics — raw counts for key platform capabilities.

```bash
analytics adoption [--namespace NS] [--output table|json|csv] [--output-dir DIR]
```

**Standard output (`kubectl`):**

```text
             Adoption Rate per Namespace
 NAMESPACE    PODS  LIMITS  NETPOL  DEPLOYS  PDB  HPA  FLUX  ARGO
 team-alpha   8     8/8     yes     3        1    1    5     0
 team-beta    5     2/5     no      2        0    0    2     0
 platform     14    14/14   yes     7        4    3    12    0
```

**OpenShift output (`--tool oc`) — includes two extra columns:**

```text
             Adoption Rate per Namespace
 NAMESPACE    PODS  LIMITS  NETPOL  DEPLOYS  PDB  HPA  FLUX  ARGO  ROUTES  DC
 team-alpha   8     8/8     yes     3        1    1    5     0     2       1
 team-beta    5     2/5     no      2        0    0    2     0     1       0
 platform     14    14/14   yes     7        4    3    12    0     8       3
```

The `ROUTES` and `DC` columns are **only shown when at least one namespace has data** — they stay hidden on plain Kubernetes clusters.

| Column | Source |
| --- | --- |
| `LIMITS` | pods with both CPU and memory limits set (`pods_with_limits / pod_count`) |
| `NETPOL` | at least one `NetworkPolicy` in the namespace |
| `PDB` | count of `PodDisruptionBudgets` |
| `HPA` | count of `HorizontalPodAutoscalers` targeting a Deployment |
| `FLUX` | sum of `HelmReleases` + `Kustomizations` (all API versions) |
| `ARGO` | count of ArgoCD `Applications` |
| `ROUTES` | count of OpenShift `Routes` (`route.openshift.io/v1`) — **oc only** |
| `DC` | count of `DeploymentConfigs` (`apps.openshift.io/v1`) — **oc only** |

**Examples:**

```bash
# Kubernetes
analytics adoption --namespace team-alpha

# OpenShift — with Routes and DeploymentConfig counts
analytics --tool oc adoption

# Export to CSV (includes route_count and deploymentconfig_count columns)
analytics --tool oc adoption --output csv > adoption.csv
```

---

### `analytics istio`

Istio service mesh usage. Without flags, shows namespace enrollment. Flags can be combined.

```bash
analytics istio [--traffic] [--external] [--policies]
                [--namespace NS] [--output table|json|csv] [--output-dir DIR]
```

**Enrollment** (default):

```text
           Istio Namespace Enrollment
 NAMESPACE    INJECTION  SIDECARS  PODS  COVERAGE
 team-alpha   yes        8         8     100%
 team-beta    no         0         5       0%
 platform     yes        12        14     85%
 legacy       no         0         3       0%
```

- `INJECTION` — value of the `istio-injection` label on the namespace
- `SIDECARS` — pods with an `istio-proxy` container running
- `COVERAGE` — `sidecars / pods`

**`--traffic`** — VirtualServices, DestinationRules, Gateways, ServiceEntries, WorkloadEntries per namespace:

```text
        Istio Traffic Policies per Namespace
 NAMESPACE    VirtualServices  DestinationRules  Gateways  ServiceEntries  WorkloadEntries
 team-alpha   4                2                 0         1               0
 platform     9                6                 2         3               2
 team-beta    0                0                 0         0               0
```

VirtualServices define routing rules (retries, timeouts, traffic splits). A namespace with Deployments but no VirtualServices relies on plain Kubernetes Service routing.

**`--external`** — ServiceEntries detail view (external services registered in the mesh):

```text
          Istio External Services (ServiceEntries)
 NAMESPACE  NAME              HOSTS                            RESOLUTION  PORTS
 platform   stripe-api        api.stripe.com                   DNS         443/HTTPS
 platform   internal-pg       postgresql.internal.example.com  DNS         5432/TCP
 team-alpha legacy-erp        legacy-erp.corp                  STATIC      8080/HTTP
```

ServiceEntries register external services into the mesh — databases, third-party APIs, legacy systems. Namespaces calling external hosts without a ServiceEntry bypass all mesh policies for that traffic.

**`--policies`** — PeerAuthentication and AuthorizationPolicies per namespace:

```text
       Istio Security Policies per Namespace
 NAMESPACE    PeerAuthentication  AuthorizationPolicies  mTLS-MODE
 team-alpha   1                   3                      STRICT
 platform     1                   8                      STRICT
 team-beta    0                   0                      none
```

**Examples:**

```bash
# Enrollment overview
analytics istio

# Combined: traffic and security policies
analytics istio --traffic --policies

# External services only, export CSV
analytics istio --external --output csv > external-services.csv

# OpenShift — same flags, identical output
analytics --tool oc istio --traffic
```

---

### `analytics all`

Runs all reports sequentially. Collects data first (4 steps), then renders all 6 sections.

```bash
analytics all [--output table|json|csv] [--output-dir DIR]
```

```text
╭─ kubectl analytics — all reports ──────────────╮
│ Namespaces: 24  Output: table                  │
╰────────────────────────────────────────────────╯
✓ [1/4] CRD statistics       3.2s
✓ [2/4] Adoption metrics     1.8s
✓ [3/4] Istio stats          1.1s
✓ [4/4] Service entries      0.4s

──────────── Custom Resource Adoption ────────────
 ...table...
─────────── Adoption Rate Metrics ────────────────
 ...table...
──────────── Istio Enrollment ────────────────────
 ...
```

In OpenShift mode the header shows `oc analytics`:

```text
╭─ oc analytics — all reports ────────────────────╮
│ Namespaces: 18  Output: table                   │
╰─────────────────────────────────────────────────╯
```

For CSV output, `--output-dir` is required — one file per report:

```bash
analytics all --output csv --output-dir ./reports/
# writes: crds.csv, adoption.csv, istio.csv,
#         istio-traffic.csv, istio-policies.csv, istio-external.csv

# OpenShift — adoption.csv includes route_count and deploymentconfig_count
analytics --tool oc all --output csv --output-dir ./reports/
```

For JSON output, a single combined file is written when `--output-dir` is given, or streamed to stdout:

```bash
analytics all --output json --output-dir ./reports/
# writes: all.json  (keys: crds, adoption, istio, service_entries)

analytics --tool oc all --output json > cluster-report.json
```

---

## Output Formats

All commands support `--output table|json|csv`.

- **table** (default) — rendered to the terminal with Rich; OpenShift-specific columns appear automatically when there is data
- **json** — serialized dataclass fields; streamed to stdout or written to `--output-dir`
- **csv** — one row per resource; always includes all fields (OpenShift fields are `0` on non-OpenShift clusters); streamed to stdout or written to `--output-dir`

```bash
# stream CSV to stdout
analytics istio --external --output csv > external-services.csv

# write to directory
analytics crds --output json --output-dir ./out/

# OpenShift full export
analytics --tool oc all --output csv --output-dir ./openshift-report/
```

---

## Design Goals

- **Read-only** — only Kubernetes/OpenShift API reads, no cluster mutations
- **No cluster-side components** — runs client-side, requires only kubeconfig access
- **Per namespace by default** — every view is namespaced; cluster-wide rollups are additive
- **Graceful degradation** — missing CRDs (Istio, Flux, ArgoCD, OpenShift extensions not installed) return 0, never crash
- **Unified codebase** — `kubectl` and `oc` use the same collection logic; the backend flag only changes which APIs are queried for namespace listing and OpenShift-specific resources

---

## Requirements

- Python >= 3.12
- Valid kubeconfig (`~/.kube/config`) or in-cluster service account
  - For Kubernetes: `kubectl cluster-info` should work
  - For OpenShift: `oc login <cluster-url>` populates the kubeconfig automatically

---

## Installation

```bash
cd kubestats
uv sync
```

This installs all dependencies (`kubernetes`, `rich`, `typer`) and creates a virtual environment.

```bash
# run via uv
uv run analytics --help

# or activate the venv and run directly
source .venv/bin/activate
analytics --help

# OpenShift usage
analytics --tool oc --help
```

---

## Development

```bash
cd kubestats

# install including dev dependencies (pyright, ruff)
uv lock --upgrade
uv sync

# type checking
uv run pyright

# linting
uv run ruff check

# run without installing
uv run python main.py --help
uv run python main.py --tool oc adoption
```

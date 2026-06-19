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
uv run analytics adoption

# OpenShift cluster
uv run analytics --tool oc adoption
```

---

## Global Options

```
--tool, -t    [kubectl|oc]   CLI backend to use (default: kubectl)
--kubeconfig  PATH           Path to kubeconfig file (default: ~/.kube/config, or $KUBECONFIG)
--context     TEXT           Kubeconfig context to activate (default: current context)
--version, -V                Print version and exit
```

All global flags must come **before** the sub-command:

```bash
# Switch to OpenShift backend
uv run analytics --tool oc adoption

# Use a specific kubeconfig file
uv run analytics --kubeconfig ~/configs/prod.yaml crds

# Use $KUBECONFIG environment variable
KUBECONFIG=~/configs/prod.yaml uv run analytics crds

# Activate a named context from your kubeconfig
uv run analytics --context staging-eu-west adoption

# Combine: explicit file + context
uv run analytics --kubeconfig ~/configs/all.yaml --context prod-us-east all

# Print version
uv run analytics --version
```

---

## Commands

### `analytics crds`

CRD adoption rate — how many instances of each CRD exist across which namespaces.

```bash
uv run analytics crds [--namespace NS] [--breakdown] [--search TEXT] [--group GROUP]
                      [--all] [--output table|csv] [--output-dir DIR]
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
# All namespaces, table output
uv run analytics crds

# Limit to one namespace
uv run analytics crds --namespace platform

# Show per-namespace instance matrix below the summary table
uv run analytics crds --breakdown

# Filter by CRD name, kind, or group (case-insensitive substring)
uv run analytics crds --search certificate
uv run analytics crds -s helmrelease

# Filter by API group
uv run analytics crds --group cert-manager.io
uv run analytics crds -g fluxcd.io

# Include CRDs with zero instances (installed but unused)
uv run analytics crds --all

# Combine filters with breakdown
uv run analytics crds --group istio.io --breakdown --namespace team-alpha

# Export as CSV
uv run analytics crds --output csv > crds.csv

# OpenShift — same flags, uses Projects as namespace source
uv run analytics --tool oc crds --breakdown
```

---

### `analytics adoption`

Per-namespace adoption metrics — raw counts for key platform capabilities.

```bash
uv run analytics adoption [--namespace NS] [--output table|csv] [--output-dir DIR]
```

**Standard output (`kubectl`):**

```text
             Adoption Rate per Namespace
 NAMESPACE    PODS  LIMITS  PROBES  NETPOL  QUOTA  LIMITRANGE  DEPLOYS  STS  INGRESS  PVC  SA  PDB  HPA  VPA  FLUX  ARGO
 team-alpha   8     8/8     6/8     yes     yes    no          3        1    2        4    3   1    1    0    5     0
 team-beta    5     2/5     0/5     no      no     no          2        0    1        0    1   0    0    0    2     0
 platform     14    14/14   14/14   yes     yes    yes         7        2    4        9    6   4    3    1    12    0
```

**OpenShift output (`--tool oc`) — includes two extra columns:**

```text
             Adoption Rate per Namespace
 NAMESPACE    PODS  LIMITS  PROBES  NETPOL  QUOTA  LIMITRANGE  DEPLOYS  STS  INGRESS  PVC  SA  PDB  HPA  VPA  FLUX  ARGO  ROUTES  DC
 team-alpha   8     8/8     6/8     yes     yes    no          3        1    2        4    3   1    1    0    5     0     2       1
 team-beta    5     2/5     0/5     no      no     no          2        0    1        0    1   0    0    0    2     0     1       0
 platform     14    14/14   14/14   yes     yes    yes         7        2    4        9    6   4    3    1    12    0     8       3
```

The `ROUTES` and `DC` columns are **only shown when at least one namespace has data** — they stay hidden on plain Kubernetes clusters.

| Column | Source |
| --- | --- |
| `LIMITS` | pods with both CPU and memory limits set (`pods_with_limits / pod_count`) |
| `PROBES` | pods where all containers have both a liveness and a readiness probe (`pods_with_probes / pod_count`) |
| `NETPOL` | at least one `NetworkPolicy` in the namespace |
| `QUOTA` | at least one `ResourceQuota` in the namespace |
| `LIMITRANGE` | at least one `LimitRange` in the namespace |
| `DEPLOYS` | count of `Deployments` |
| `STS` | count of `StatefulSets` |
| `INGRESS` | count of `Ingress` resources |
| `PVC` | count of `PersistentVolumeClaims` in the namespace |
| `SA` | count of `ServiceAccounts` in the namespace (includes the default one) |
| `PDB` | count of `PodDisruptionBudgets` |
| `HPA` | count of `HorizontalPodAutoscalers` targeting a Deployment |
| `VPA` | count of `VerticalPodAutoscalers` (`autoscaling.k8s.io/v1`) |
| `FLUX` | sum of `HelmReleases` + `Kustomizations` (all API versions) |
| `ARGO` | count of ArgoCD `Applications` |
| `ROUTES` | count of OpenShift `Routes` (`route.openshift.io/v1`) — **oc only** |
| `DC` | count of `DeploymentConfigs` (`apps.openshift.io/v1`) — **oc only** |

**Examples:**

```bash
# All namespaces
uv run analytics adoption

# Limit to one namespace
uv run analytics adoption --namespace team-alpha
uv run analytics adoption -n platform

# Export as CSV to stdout
uv run analytics adoption --output csv > adoption.csv

# Write output to a directory (file: adoption.csv)
uv run analytics adoption --output csv --output-dir ./out/

# OpenShift — includes ROUTES and DC columns
uv run analytics --tool oc adoption

# OpenShift — single namespace, CSV export
uv run analytics --tool oc adoption --namespace team-alpha --output csv > adoption.csv
```

---

### `analytics istio`

Istio service mesh usage. Without flags, shows namespace enrollment. Flags can be combined.

```bash
uv run analytics istio [--traffic] [--external] [--policies]
                [--namespace NS] [--output table|csv] [--output-dir DIR]
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
# Enrollment overview (default when no flag is given)
uv run analytics istio

# Limit to one namespace
uv run analytics istio --namespace team-alpha
uv run analytics istio -n platform

# Traffic policies only
uv run analytics istio --traffic

# Security policies only
uv run analytics istio --policies

# External services (ServiceEntries) only
uv run analytics istio --external

# Combine flags — all three views at once
uv run analytics istio --traffic --policies --external

# Combined: traffic and security policies
uv run analytics istio --traffic --policies

# Export external services as CSV
uv run analytics istio --external --output csv > external-services.csv

# Write traffic policies to a directory (file: istio-traffic.csv)
uv run analytics istio --traffic --output csv --output-dir ./out/

# OpenShift — same flags, identical output
uv run analytics --tool oc istio --traffic
```

---

### `analytics all`

Runs all reports sequentially. Collects data first (4 steps), then renders all 6 sections.

```bash
uv run analytics all [--namespace NS] [--breakdown] [--output table|csv] [--output-dir DIR]
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

**Examples:**

```bash
# All namespaces, table output (default)
uv run analytics all

# Limit to one namespace
uv run analytics all --namespace platform
uv run analytics all -n team-alpha

# Include the per-namespace CRD instance matrix (table output only)
uv run analytics all --breakdown

# CSV — --output-dir is required; writes one file per report:
#   crds.csv, adoption.csv, istio.csv,
#   istio-traffic.csv, istio-policies.csv, istio-external.csv
uv run analytics all --output csv --output-dir ./reports/

# OpenShift — adoption.csv includes route_count and deploymentconfig_count
uv run analytics --tool oc all --output csv --output-dir ./reports/

```

---

## Output Formats

All commands support `--output table|csv`.

- **table** (default) — rendered to the terminal with Rich; OpenShift-specific columns appear automatically when there is data
- **csv** — one row per resource; always includes all fields (OpenShift fields are `0` on non-OpenShift clusters); streamed to stdout or written to `--output-dir`

```bash
# stream CSV to stdout
uv run analytics istio --external --output csv > external-services.csv

# write to directory
uv run analytics crds --output csv --output-dir ./out/

# OpenShift full export
uv run analytics --tool oc all --output csv --output-dir ./openshift-report/
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

### Global install (recommended)

Build a wheel and install `analytics` as a system-wide command:

```bash
uv build
uv tool install dist/kubestats-0.1.0-py3-none-any.whl

# the command is now available everywhere
analytics --help
analytics adoption
analytics --tool oc crds
```

Upgrade after a new build:

```bash
uv build
uv tool install --force dist/kubestats-0.1.0-py3-none-any.whl
```

Uninstall:

```bash
uv tool uninstall kubestats
```

### Development install

```bash
uv sync
```

This installs all dependencies into a local `.venv` and makes the tool available via `uv run`:

```bash
uv run analytics --help
uv run analytics --tool oc adoption
```

---

## Building the Wheel

```bash
uv build
# creates:
#   dist/kubestats-0.1.0-py3-none-any.whl
#   dist/kubestats-0.1.0.tar.gz
```

To test the wheel before installing:

```bash
uv run --with dist/kubestats-0.1.0-py3-none-any.whl analytics --help
```

---

## Development

```bash
# install including dev dependencies (pyright, ruff, pytest)
uv sync

# type checking
uv run pyright

# linting
uv run ruff check

# tests
uv run pytest

# run
uv run analytics --help
uv run analytics --tool oc adoption
```

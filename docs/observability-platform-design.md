# Observability Platform - Architecture Notes

> **Status:** Reference / background. Captures the Kubernetes observability architecture and the
> collector-evolution reasoning that inform Noto's design. See [design.md](design.md) for Noto's
> actual decisions.

These notes walk the canonical Kubernetes observability architecture - from cluster fundamentals
through the **Node Agent (DaemonSet) + Cluster Agent (Deployment)** split - and the collector
evolution that converges on the OpenTelemetry Collector as the open-source substrate. They are
background reasoning for the **opinionated open-source end-to-end observability platform** and the
specialization agent that supports it.

---

## Part 1 - Kubernetes Foundations

### What is Kubernetes?

- Kubernetes is an open-source orchestration platform that automates the deployment,
  scaling, and management of containerized applications using a declarative model.
- You define the **desired state** of your infrastructure - such as the exact number of
  application instances you need running at any given time.
- Kubernetes then continuously monitors the environment and automatically **reconciles**
  any differences, ensuring the actual live state always matches your declared desired
  state without manual intervention.


---

### Node Types

Diagram of a **Cluster** containing:

- **Control Plane Node** (top)
- A divider, then the worker tier:
  - **Worker Node 1** … **Worker Node n** (the "…" indicates arbitrary scale-out)


---

### Control Plane Node

- **Primary Role:** Acts as the central nervous system and management tier of the
  Kubernetes cluster.
- **Responsibilities:** Makes global decisions (scheduling, scaling) and maintains the
  desired state of the cluster.
- **Workload:** Does not run user applications; strictly runs Kubernetes system services.
- **Key Components:**
  - **API Server:** The front-end control interface; all users and internal components
    communicate through this. **Think kube-state-metrics (e.g. `kube_pod_status_phase`).**
  - **etcd:** The highly-available key-value database containing the cluster's overall
    state and "source of truth."
  - **Scheduler:** Watches for new workloads (pods) and assigns them to the best available
    worker node.
  - **Controller Manager:** Runs continuous loops to monitor the cluster and act when the
    current state doesn't match the desired state.

---

### Worker Nodes

- **Primary Role:** The physical or virtual machines that actually run your applications.
- **Responsibilities:** Provides the computing power (CPU, Memory, Network) to execute
  containerized workloads.
- **Workload:** Receives instructions from the Control Plane and runs user-defined
  pods/containers.
- **Key Components:**
  - **Kubelet:** The primary agent living on every node; talks to the Control Plane and
    ensures assigned containers are running and healthy.
    **Think cAdvisor (e.g. `container_cpu_usage_seconds_total`).**
  - **Kube-proxy:** Manages local network rules, enabling communication to your pods from
    inside or outside the cluster.
  - **Container Runtime:** The underlying software (e.g., containerd, CRI-O) responsible
    for actually pulling images and running the containers.

---

## Part 2 - Scheduling (how workloads land on nodes)

### Scheduling

Diagram: a **Cluster** containing three empty **Node** boxes side by side. (Baseline before
any pods are placed.)

### Scheduling – Pods

Diagram: **Cluster** with three **Node**s, each now containing two **Pod** boxes. (Pods
distributed across nodes.)

### Scheduling – Deployment – 1 Replica (a)

Diagram: Cluster with three Nodes. A single **Pod** labeled **"My Application"** is placed
on **Node 1**. (Initial placement of a 1-replica Deployment.)

### Scheduling – Deployment – 1 Replica (b)

Diagram: Same Cluster/three Nodes. The **Pod "My Application"** has now moved to the
**middle Node**. (Illustrates that a Deployment's single replica can be rescheduled onto a
different node - pods are not pinned.)

### Scheduling – Deployment – 2 Replicas (a)

Diagram: Two replicas spread across nodes - 
- **Node 1** → Pod **"My Application-1"**
- **Node 2** → Pod **"My Application-2"**
- **Node 3** → empty

(Default Deployment behavior: spread replicas across nodes for availability.)

### Scheduling – Deployment – 2 Replicas (b)

Diagram: Both replicas co-located on **Node 1**:
- **Pod "My Application-1"** and **Pod "My Application-2"** both on Node 1
- Nodes 2 and 3 empty

(Contrasts with (a): without anti-affinity, replicas *can* end up on the same node.)

### Scheduling – DaemonSet

Diagram: Cluster with three Nodes, **one Pod per node**:
- Node 1 → **"My Application-1"**
- Node 2 → **"My Application-2"**
- Node 3 → **"My Application-3"**

(DaemonSet guarantees exactly one pod on *every* node - the deployment model that node-level
telemetry agents use.)

### Scheduling – Sidecar

Diagram: Cluster with three Nodes. The middle Node has one **Pod** containing **two
containers**:
- **Container: My Application**
- **Container: Helper Application**

(Sidecar pattern: a helper container shares the pod's lifecycle/network with the main app - 
the basis for log-shipping/telemetry sidecars.)

---

## Part 3 - A Quick Note on Networking

### Quick Note on Networking

Diagram of a **Cluster** with two **Worker Nodes** showing intra/inter-pod communication paths:

- **Worker Node (left):** contains **Pod A**.
- **Worker Node (right):** contains:
  - **Pod B** with **Container A**, which talks to **Container B** over **`localhost:8080`**
    (intra-pod, same network namespace).
  - **Pod C** and **Pod D**.
  - A path showing **`<node_ip>:8080`** reaching **Pod D**.
- **Deployment C Service** (bottom): fronts the deployment; reached via the DNS name
  **`deployment-c.<namespace>.svc.cluster.local`** from **Pod A**. An arrow shows the
  Service load-balancing back to **Pod C**.

**Key takeaways encoded in the diagram:**
- Containers in the same pod communicate over `localhost`.
- Pod-to-pod across nodes uses `<node_ip>:port`.
- Stable access uses a **Service** + cluster DNS name (`<svc>.<namespace>.svc.cluster.local`).

---

## Part 4 - General Architecture Pattern

### Node Agent vs. Cluster Agent

Two-column comparison of the canonical observability-agent topology.

**The Node Agent**
- **Deployment: DaemonSet (1 per node)**
- Responsible for:
  - **host level telemetry**
    - prom: `node_exporter`, `cadvisor`
    - otel: `hostmetrics`, `kubeletstats`
  - **pod level telemetry**
    - container resource usage (kubelet)
    - log collection
    - custom metrics/traces

**The Cluster Agent**
- **Deployment: Deployment (1–n replicas)**
- Responsible for:
  - acting as an API server proxy
  - collecting telemetry from "big singletons" (**KSM** = kube-state-metrics)
  - cluster metadata caching
  - K8s event collection
  - in certain cases serves unified egress

> This split is the conceptual backbone for everything that follows: each vendor/tooling
> below is a concrete instantiation of the **Node Agent (DaemonSet) + Cluster Agent
> (Deployment)** split.

---

## Part 5 - DataDog Agent Architecture

### Datadog Agent Architecture

Diagram of a **Cluster** with a **Control Plane Node** (`/apiserver`) and three **Worker
Nodes** (each with `/kubelet`):

- **Worker Node (left):** **Application Pod** + **Pod: Datadog Agent DaemonSet**
- **Worker Node (middle):** **Pod: Datadog Cluster Agent Deployment** + **Pod: Datadog Agent DaemonSet**
- **Worker Node (right):** **Pod: Datadog Agent DaemonSet**
- **Datadog Cluster Agent Service** (bottom) fronts the cluster agent.

**Numbered flow (right-hand legend):**
1. **Cluster Agent** polls the API server to collect cluster-level events, state metrics,
   and metadata.
2. **Node Agent** polls the local Kubelet to discover scheduled pods and pull container
   resource metrics via cAdvisor.
3. **Node Agent** queries your **Cluster Agent** through a Kubernetes Service. Retrieves
   metadata (labels, annotations) to properly tag collected metrics without overloading the
   control plane.
4. **Cluster Agent Service** acts as a **LoadBalancer**, enabling High Availability (HA)
   when running multiple replicas.
5. **Application pods** send DogStatsD metrics and traces directly to their local
   **Node Agent**.
6. **Node Agent** scrapes logs and Prometheus metrics directly from the application pods.

### Datadog Agent Architecture → Datadog SaaS

The same cluster diagram, now showing **egress to the external Datadog SaaS**
(large purple **"Datadog"** circle outside the cluster). Arrows (labeled with the same step
numbers) show the Node Agent DaemonSets and the Cluster Agent Service ultimately shipping
telemetry **out to Datadog**.

> **Net:** Datadog's model = per-node **Datadog Agent DaemonSet** + a **Datadog Cluster
> Agent Deployment** (fronted by a Service for HA), egressing to Datadog's hosted backend.

---

## Part 6 - OTEL Collector

### OTEL Collector Architecture

Diagram mirrors the Datadog layout but substitutes OTel components. **Cluster** with
**Control Plane Node** (`/apiserver`) and three **Worker Nodes** (each `/kubelet`):

- **Worker Node (left):** **Application Pod**, **Pod: Datadog Agent DaemonSet**, **Pod: Otel Collector DaemonSet**
- **Worker Node (middle):** **Pod: Otel Collector Gateway Deployment**, **Pod: Datadog Agent DaemonSet**, **Otel Collector DaemonSet**
- **Worker Node (right):** **Pod: Datadog Agent DaemonSet**, **Otel Collector DaemonSet**
- **Otel Collector Service** (bottom).

**Numbered flow (right-hand legend):**
1. **Otel Collector Gateway** polls the API server to collect cluster-level events, state
   metrics, and metadata.
2. **Datadog Node Agent** forwards DogStatsD metrics from application pods to the
   **Otel Collector Daemonset**.
3. **Otel Collector Daemonset** polls the local Kubelet to discover scheduled pods and pull
   container resource metrics via cAdvisor.
4. **Otel Collector Service** acts as a **LoadBalancer**, enabling High Availability (HA)
   for multiple **Otel Collector Gateway** replicas.
5. **Otel Collector Daemonset** telemetry is sent to the **Otel Collector Gateway** where it
   gets enriched with Kubernetes metadata (like labels and annotations).
6. **Application pods** emit dogstatsd metrics and traces directly to their local
   **Datadog Node Agent**.
7. **Datadog Node Agent** scrapes logs and Prometheus metrics directly from the
   **application pods**.
8. **Traces** are routed directly to the **Otel Collector Gateway**, typically bypassing the
   local **Otel Collector Daemonset**.

### OTEL Collector Architecture → Noto

Same diagram, now egressing to an external **Noto** backend (large green
**"Noto"** circle outside the cluster). The Otel Collector Gateway / Service ships
the enriched telemetry out to **the Noto backend**.

> **Net:** OTel model = **Otel Collector DaemonSet** (node agent) + **Otel Collector Gateway
> Deployment** (cluster agent / enrichment + egress), with the Datadog Node Agent still
> present to ingest DogStatsD during migration.

---

## Part 7 - noto-collector

### Why did we outgrow the Otel Collector? (Part 1 - the problem)

**The Catalyst: The OpenAI "Trial by Fire"** - The OTel Collector's StatsD receiver was too
basic for real-world Datadog migrations. Exposing it to OpenAI's scale and complexity
revealed critical gaps that required rapid, purpose-built evolution.

**Pain Points Discovered at Scale:**
- **Protocol Gaps:** OTel lacked support for DogStatsD v1.1–1.6 features, including multiple
  values per datagram, explicit timestamps, and container IDs.
- **Missing K8s Augmentation:** OTel couldn't enrich UDP metrics with pod-level metadata
  (service owner, model class) or handle Datadog Tag Autodiscovery.
- **Aggregation Mismatch:** Datadog's "last value wins" gauge logic and alignment timestamps
  caused dashboard divergence compared to default OTel behavior.
- **Resource Exhaustion:** Massive tag cardinality and UDP buffer overflows caused silent
  metric drops and crash-looping OOMs across clusters.
- **Missing Features:** Lack of support for "set" metrics and staleness markers left
  dashboards with lingering, inaccurate data.

### Why did we outgrow the Otel Collector? (Part 2 - the solution)

**Why Not Just Fix OTel?** Upstreaming fixes to the OTel collector was too slow for OpenAI's
cadence. Furthermore, OTel's approach required tedious, manual configuration for massive
deployments. Noto-collector provided a frictionless, out-of-the-box DaemonSet solution.

**The Rapid Release Arc (v0.108 – v0.115):**
- **Scale & Reliability:** Added multi-socket UDP listeners, auto-growing buffers, and
  cgroup-aware memory limits.
- **Full Spec Support:** Integrated full DogStatsD v1.1–v1.6 support, timer-to-histogram
  aggregation, and set metric tracking.
- **Rich Enrichment:** Shipped container ID-to-pod resolution, IP fallbacks, and label
  extraction directly from annotations.

**Current State & Next Steps:**
- **The Legacy:** This "trial by fire" battle-hardened the Noto-collector, making it the
  bedrock for all current Datadog migration customers.
- **Next-Gen Integration:** These lessons are actively shaping the **`noto-collector-ng`**,
  building DogStatsD capabilities directly into OTel `metric` types to get the best of both
  worlds.

### noto-collector Architecture

Diagram (same cluster layout) egressing to **Noto**, with the right-hand legend:

> The Noto-collector architecture is a hybrid model, combining elements of both the Datadog
> Agent and OpenTelemetry (OTel) Collector deployment patterns.

- **Datadog Node Agent:** Remains active in this setup. It is responsible for forwarding
  local DogStatsD metrics from application pods to the **OTel Collector DaemonSet**.
- **OTel Collector Gateway:** Retained with a highly specific scope. Because the
  Noto-collector does not natively support Datadog trace ingestion, this gateway's sole
  role is to receive Datadog traces directly from the **Datadog Node Agent**.
- **Noto-collector DaemonSet:** Functions similarly to the standard Datadog Agent by
  egressing its telemetry directly to the Noto backend, bypassing the need for a
  centralized gateway for its own data stream.

---

## Part 8 - noto-collector-ng (Next-Gen)

### Why did we outgrow the noto-collector?

**The Challenge: The Datadog Integrations Gap** - While Noto-collector solved the DogStatsD
ingestion problem, it couldn't solve the integration scale problem. Datadog boasts a massive
library of integrations that "just work."

**The Solution: The `noto-integrations` Framework.**

**How the New Framework Scales:**
- **Declarative Catalog:** Integrations (like MySQL or NGINX) are now defined in lightweight
  YAML pairs (`integration.yaml` and `userconfig.schema.yaml`), stripping away the need for
  bulky custom code.
- **The Magic of Autodiscovery:** The framework uses entity selectors (`match: system`) to
  automatically discover services via pod labels or ports and dynamically wire up
  sub-receivers - perfectly mimicking Datadog's annotation-driven discovery.
- **Hybrid Receiver Model:** It seamlessly wires up existing upstream OTel contrib receivers
  for heavy lifting, while allowing custom receivers (like `mysqld`) to fill specific Datadog
  parity gaps.
- **Global Enrichment:** Kubernetes metadata enrichment is applied globally as a shared
  layer: New integrations automatically inherit rich K8s metadata without redeclaring it.

### noto-collector-ng Architecture

Diagram (same cluster layout) egressing to **Noto**, with right-hand legend:

> The **noto-collector-ng** architecture closely resembles a standard OTel Collector deployment
> (it IS a (modified) OTel collector), utilizing a centralized gateway for telemetry
> enrichment and egress.

- **Datadog Node Agent:** Remains active in this setup. It is responsible for forwarding
  local DogStatsD metrics from application pods to the **noto-collector-ng** DaemonSet.
- **Direct Trace Ingestion:** The **noto-collector-ng** now natively supports Datadog trace
  ingestion, eliminating the need for a separate OTel Collector.
- **Centralized Gateway:** The noto-collector-ng Gateway receives all downstream telemetry,
  enriches it with Kubernetes metadata, and egresses the final data to the Noto backend.

---

## Part 9 - Recap

### Recap

1. **Surviving the "Trial by Fire":** Standard OTel couldn't handle real-world Datadog
   metrics at OpenAI's scale. We built and rapidly evolved the Noto-collector to solve the
   ingestion crisis, adding full DogStatsD spec support, high-volume reliability, and
   critical pod-level metadata.
2. **Hitting the Integration Ceiling:** Noto-collector perfected custom metric ingestion,
   but it couldn't solve the broader ecosystem challenge. Customers expect Datadog's massive
   library of plug-and-play integrations, while OTel forced them into tedious, manual
   scrape-job configurations.
3. **The Next-Gen Solution - `noto-integrations`:** To achieve true Datadog parity at scale,
   we outgrew the Noto-collector and built a new declarative framework. By combining
   Datadog-style autodiscovery, global metadata enrichment, and YAML-based catalog
   definitions, we turned massive integration rollouts from a code problem into a simple
   configuration update.

---

## Synthesis - The Evolution Arc (for our platform reasoning)

These notes tell one continuous story, and the **order of operations matters**:

```
K8s fundamentals  ─►  Node/Cluster agent split  ─►  Datadog Agent  ─►  OTel Collector
        (the "why" of the two-tier agent pattern)      (vendor)         (open standard)
                                                                              │
                                                                              ▼
                                              noto-collector (DogStatsD-hardened OTel DaemonSet)
                                                                              │
                                                                              ▼
                                              noto-collector-ng (declarative integrations + native
                                                              trace ingest, centralized gateway)
```

**The throughline relevant to our opinionated open-source observability platform:**

1. Every model reduces to the **Node Agent (DaemonSet) + Cluster Agent (Deployment +
   Service)** pattern. This is the opinionated topology to standardize on.
2. **OTel is the open-source convergence point** - both noto-collector and noto-collector-ng
   "are (modified) OTel collectors." An open-source platform should standardize on the OTel
   Collector (DaemonSet + Gateway) as the substrate.
3. The hard problems are not "collect telemetry" but: **DogStatsD/protocol parity, K8s
   metadata enrichment, tag cardinality / resource safety, and integration autodiscovery.**
   These are exactly where an opinionated framework (and a support agent) adds value.
4. The noto-collector-ng's **declarative YAML integration catalog + autodiscovery** is the
   model for "opinionated, configuration-not-code" - the same philosophy as gojira's
   `gojira.yaml` workload classes. An observability-specialization agent can own generating,
   validating, and reconciling these declarative integration definitions.

These notes inform the scope of the open-source observability specialization agent (Noto) and how it
fits the broader platform.

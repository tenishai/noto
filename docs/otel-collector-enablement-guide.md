# OpenTelemetry Collector Enablement Guide

> **Status:** Reference. The collection patterns, processor opinions, and pipeline assembly Argus
> encodes as defaults. References to "Argus' reference config" describe the opinionated configuration
> Argus ships. Treat the content as a guide to validate against upstream OpenTelemetry docs, not as
> ground truth.

This guide covers how to deploy, monitor, and configure the OpenTelemetry Collector in Kubernetes,
with a focus on telemetry processing. It is organized in two parts: collection patterns and the
Prometheus Operator model (Part A), then the collector enablement guide proper (Part B).

---

# PART A - Collection Patterns & The Prometheus Operator Model

Setting up a metric collector in Kubernetes (like the **OpenTelemetry Collector**, **Prometheus
Agent**, **Telegraf**, or **Fluent Bit**) involves deciding *where* and *how* the collector runs in
relation to your applications and infrastructure.

In Kubernetes, there are three primary architectural patterns for deploying collectors:
**DaemonSet**, **Sidecar**, and **Deployment** (often called a Gateway or Centralized pattern). Each
serves a distinct purpose and has different trade-offs.

Here is a breakdown of how to set them up and when to use each.

## 1. The DaemonSet Pattern (Node-Level Collection)

A **DaemonSet** ensures that exactly one copy of a specific Pod runs on every *single* Node in your
Kubernetes cluster. If a new Node is added, a new collector Pod is automatically spun up on it.

**How it works for metrics:**

The collector running as a DaemonSet is responsible for gathering infrastructure metrics for the
Node it resides on. It typically mounts the Node's filesystem (like `/var/log` or `/proc`) to read
system-level data. It can also be configured to discover and scrape metrics from all application
Pods running on that specific Node.

**Setup approach:**

- Create a Kubernetes `DaemonSet` manifest.
- Configure the collector to scrape metrics from the host (CPU, memory, disk, network) using node
  exporters or host metrics receivers.
- Configure the collector to talk to the local `kubelet` to get container-level metrics (cAdvisor).
- Add RBAC permissions (ServiceAccount, ClusterRole, ClusterRoleBinding) so the collector can watch
  Pods and Nodes via the Kubernetes API.

**Pros & Cons:**

- ✅ **Pros:** Highly efficient. It avoids duplicating collection effort. If you have 50 Pods on a
  Node, you only need 1 collector to scrape them all. It is the best pattern for infrastructure
  metrics (host CPU/RAM) and logs.
- ❌ **Cons:** Security boundary. Because the DaemonSet collects data for *all* applications on the
  Node, it often requires high privileges (mounting host paths, wide RBAC permissions). If one
  tenant's app floods the collector with metrics, it can impact the collection for other tenants on
  the same Node (noisy neighbor problem).

## 2. The Sidecar Pattern (Application-Level Collection)

A **Sidecar** is a secondary container that runs inside the *exact same Pod* as your main
application container. They share the same lifecycle, network namespace (localhost), and storage
volumes.

**How it works for metrics:**

The application container exposes its metrics (e.g., at `http://localhost:8080/metrics` or by
writing to a shared file). The Sidecar collector container, running right next to it, scrapes
`localhost` or reads the shared file, processes the metrics, and pushes them out to a centralized
backend.

**Setup approach:**

- You do not deploy the collector as a standalone resource. Instead, you add a second `container`
  definition into your application's `Deployment` or `Pod` manifest.
- Configure the collector to scrape `localhost:<app-port>` or listen for pushes on `localhost`.
- *(Modern approach):* Use a Mutating Admission Webhook (like the OpenTelemetry Operator) to
  automatically inject the sidecar container into your Pods based on annotations, so developers
  don't have to manually edit their YAML.

**Pros & Cons:**

- ✅ **Pros:** Perfect isolation. The collector is dedicated to one specific application. If the app
  goes crazy, it only crashes its own sidecar. It also allows developers to heavily customize the
  collector configuration just for their app without affecting the rest of the cluster. No complex
  RBAC is needed because it only talks to its own Pod.
- ❌ **Cons:** Resource overhead. If you have 1,000 Pods, you now have 1,000 collector containers
  running. Even if a collector only uses 50MB of RAM, 50MB × 1,000 = 50GB of RAM wasted on
  collection overhead. It is also terrible for host-level metrics, as you would get duplicated host
  data from every Pod.

## 3. The Deployment Pattern (Cluster-Level / Gateway Collection)

A **Deployment** is the standard way to run stateless applications in Kubernetes. You specify how
many replicas (Pods) you want, and Kubernetes schedules them wherever there is room. In the
observability world, this is often called the **Gateway** or **Aggregator** pattern.

**How it works for metrics:**

Unlike DaemonSets or Sidecars, a collector running as a Deployment usually *does not* scrape *local
applications or nodes directly*. Instead, it acts as a centralized processing hub. It receives
metrics pushed to it by the Sidecars or DaemonSets, or it scrapes external endpoints (like cloud
provider APIs, databases, or third-party services) that don't live on a specific cluster node.

**Setup approach:**

- Create a Kubernetes `Deployment` manifest with 2 or more replicas for high availability.
- Place a Kubernetes `Service` in front of the Deployment (e.g.
  `collector-gateway-service`) so internal traffic can be load-balanced to the collector Pods.
- Configure your DaemonSets or Sidecars to forward their data to this Service rather than sending it
  directly over the internet to your observability backend (like Datadog, Grafana Cloud, or Splunk).
- Configure the Gateway collector to perform heavy processing: batching, tail-based sampling,
  filtering out PII (Personally Identifiable Information), or enriching metrics with cluster-wide
  metadata.

**Pros & Cons:**

- ✅ **Pros:** Centralized control. You can manage API keys and credentials in one place (the
  Gateway) instead of distributing them to every Node or Pod. It is great for heavy data processing
  that would consume too much CPU on a worker node. It allows you to scale the collector tier
  independently of your applications.
- ❌ **Cons:** Adds a network hop. Data goes App → DaemonSet → Gateway → Backend. It introduces a
  single point of failure (though mitigated by running multiple replicas). It cannot collect
  node-level metrics directly because it floats randomly around the cluster.

## Summary: Which one should you use?

In a robust, production-grade Kubernetes cluster, you almost never choose just one. You typically use
a combination:

1. **Use a DaemonSet** as your default foundation. It is the most efficient way to get Kubernetes
   infrastructure metrics, Node metrics, and logs.
2. **Use Sidecars** only when absolutely necessary. Reserve them for specific applications that need
   unique metric processing, specialized scraping, or strict security isolation.
3. **Use a Deployment (Gateway)** as your middleman. Send all data from the DaemonSets and Sidecars
   to the Gateway. Let the Gateway handle the heavy lifting (filtering, batching, adding API keys)
   before sending the data out to your final storage backend.

---

## ServiceMonitors & Target Allocators

In a dynamic Kubernetes environment, Pods are constantly spinning up and tearing down. Because IP
addresses change by the minute, you cannot hardcode a list of IPs for your metric collectors to
scrape.

This is where **Service Monitors** and **Target Allocators** come in. They are advanced mechanisms
used by Kubernetes "Operators" (like the Prometheus Operator and OpenTelemetry Operator) to automate
**Service Discovery** (finding what to scrape) and **Load Balancing** (deciding who scrapes it).

### 1. Service Monitors (The "What to Scrape")

A **ServiceMonitor** is a Custom Resource Definition (CRD) introduced by the **Prometheus Operator**.
It is the modern, cloud-native way to tell a metric collector: *"Here is an application, and here is
how you scrape its metrics."*

**The Problem it Solves:**
Historically, to add a new application to Prometheus, a cluster admin had to manually edit a massive,
centralized `prometheus.yml` configuration file, add the new endpoints, and reload the server. In
Kubernetes, where developers are deploying new microservices daily, this creates a massive bottleneck.

**How a ServiceMonitor Works:**
Instead of a centralized config file, the configuration is decentralized and declarative.

1. A developer deploys their application and a Kubernetes `Service` to expose it.
2. The developer deploys a tiny `ServiceMonitor` YAML file right alongside their app.
3. The `ServiceMonitor` uses **Label Selectors** (e.g. `matchLabels: app=my-web-api`) to point to the
   application's Kubernetes Service. It specifies the port, the path (usually `/metrics`), and how
   often to scrape (e.g. `interval: 30s`).
4. The Prometheus Operator constantly watches the cluster for new `ServiceMonitors`. When it sees one,
   it automatically updates the underlying metric collector's configuration to start scraping those
   Pods.

**Analogy:** A ServiceMonitor is like putting a beacon on your application that broadcasts: *"I am a
web app. I have metrics on port 8080 at the `/metrics` path, please come read them every 30 seconds."*

### 2. Target Allocators (The "Who Scrapes What")

A **Target Allocator** is a specific, optional component introduced by the **OpenTelemetry (OTel)
Operator**. It solves a massive architectural problem when trying to scale metric collectors.

**The Problem it Solves:**
Imagine you have 10,000 Pods in your cluster. A single metric collector cannot scrape all of them - 
it will run out of memory or CPU. So, you deploy a `Deployment` of collectors with 5 replicas. But
Prometheus scraping is "pull-based." If you just run 5 independent collectors, all 5 collectors will
scrape the exact same 10,000 Pods. You will overwhelm your network, overwhelm your applications, and
end up with 5 identical copies of your metrics in your database.

**How the Target Allocator Works:**
The Target Allocator is a separate microservice that sits next to your collector Deployment. It acts
as the "brain" for distributing work.

1. **Discovery:** The Target Allocator connects to the Kubernetes API and discovers all the endpoints
   that need to be scraped (often by reading `ServiceMonitors`).
2. **Sharding (Allocation):** It takes the total list of targets (say, 10,000 Pods) and divides them
   evenly among your 5 OpenTelemetry Collector replicas.
3. **Configuration:** Each collector replica asks the Target Allocator: *"Which targets am I
   responsible for?"* The Target Allocator hands Collector A targets 1–2000, Collector B targets
   2001–4000, and so on.
4. **Rebalancing:** If a collector crashes or you scale up to 10 replicas, the Target Allocator
   instantly mathematically rebalances the targets so no data is duplicated and no target is left
   un-scraped.

**Analogy:** If the metric targets are a giant pile of mail, and the collectors are postal workers,
the Target Allocator is the manager standing at the front desk handing specific stacks of mail to
specific workers so they don't do the same job twice.

### Summary: How They Work Together

In a modern observability stack (especially one using OpenTelemetry to scrape Prometheus metrics),
these two concepts work in perfect harmony:

1. **The Developer** deploys their app and a `ServiceMonitor` to define *what* needs to be scraped.
2. **The Target Allocator** reads that ServiceMonitor, discovers the underlying Pod IP addresses, and
   chops them up into even chunks.
3. **The Collector Replicas** (running as a Deployment) get their specific chunk of targets from the
   Target Allocator and actually perform the HTTP scraping.

Together, they allow you to **decentralize** metric configuration (via ServiceMonitors) while
horizontally scaling your metric collection without data duplication (via the Target Allocator).

---

## Step-by-Step: Wiring It Up

To deploy a `ServiceMonitor` and connect it to your metric collector, you need to understand the
relationship between three Kubernetes resources: the **Service** (which exposes the metrics), the
**ServiceMonitor** (which defines *how* to scrape the Service), and the **Collector Configuration**
(which selects *which* ServiceMonitors to read).

Here is a step-by-step guide on how to set this up for both application and infrastructure metrics,
assuming you have the Prometheus Operator or OpenTelemetry Operator installed in your cluster.

### Step 1: Deploying Application Metrics

For an application to be scraped, it must expose a metrics endpoint (typically
`http://<pod-ip>:8080/metrics`).

**1. The Application Service**

First, your application needs a standard Kubernetes `Service`. The `ServiceMonitor` will use the
labels on this Service to find your application.

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-app-service
  namespace: my-namespace
  labels:
    app: my-app
    # This label is critical: it's how the ServiceMonitor finds this Service
    metrics-exposed: "true"
spec:
  selector:
    app: my-app
  ports:
    - name: metrics
      port: 8080
      targetPort: 8080
```

**2. The ServiceMonitor**

Next, you deploy the `ServiceMonitor`. This tells the operator to go find the Service above and
scrape its endpoints.

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: my-app-monitor
  namespace: my-namespace
  labels:
    # The Collector uses this label to find this ServiceMonitor
    release: my-observability-stack
spec:
  selector:
    matchLabels:
      metrics-exposed: "true"   # Matches the label on the Service above
  endpoints:
    - port: metrics             # Matches the port name in the Service above
      path: /metrics
      interval: 30s             # Scrape every 30 seconds
```

### Step 2: Deploying Infrastructure Metrics

Infrastructure metrics usually come from components like **kube-state-metrics** (cluster-level
metrics) or **node-exporter** (host-level hardware metrics). The concept is exactly the same as
application metrics.

If you deploy `node-exporter` (often deployed as a DaemonSet), it comes with a Service. You then
deploy a `ServiceMonitor` for it.

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: node-exporter-monitor
  namespace: monitoring
  labels:
    release: my-observability-stack
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: node-exporter
  endpoints:
    - port: metrics
      interval: 15s
```

*(Note: If you install the popular `kube-prometheus-stack` Helm chart, all the ServiceMonitors for
infrastructure metrics are created and connected for you automatically.)*

### Step 3: Connecting the ServiceMonitors to the Collector

Now you have ServiceMonitors sitting in your cluster, but your collector (Prometheus or
OpenTelemetry) needs to be told to look at them. This is done using **Label Selectors**.

**Scenario A: Using the Prometheus Operator**

If you are using Prometheus, you configure the Prometheus Custom Resource. Notice the
`serviceMonitorSelector`:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: Prometheus
metadata:
  name: my-prometheus
  namespace: monitoring
spec:
  serviceAccountName: prometheus
  serviceMonitorSelector:
    matchLabels:
      # This tells Prometheus to scrape ANY ServiceMonitor in the
      # cluster that has the label 'release: my-observability-stack'
      release: my-observability-stack
```

**Scenario B: Using OpenTelemetry with the Target Allocator**

If you are using the OpenTelemetry (OTel) Collector to scrape Prometheus metrics, you configure the
`OpenTelemetryCollector` Custom Resource. You must enable the **Target Allocator** and tell it to act
like Prometheus by watching for `ServiceMonitors`.

```yaml
apiVersion: opentelemetry.io/v1alpha1
kind: OpenTelemetryCollector
metadata:
  name: my-otel-collector
  namespace: monitoring
spec:
  mode: statefulset
  # Enable the Target Allocator
  targetAllocator:
    enabled: true
    prometheusCR:
      enabled: true
      serviceMonitorSelector:
        matchLabels:
          # The Target Allocator will find ServiceMonitors with this label,
          # calculate the targets, and distribute them to the OTel replicas.
          release: my-observability-stack
  config: |
    receivers:
      prometheus:
        config:
          scrape_configs: []
          # The Target Allocator automatically injects the scrape configs here
    exporters:
      logging: {}
    service:
      pipelines:
        metrics:
          receivers: [prometheus]
          exporters: [logging]
```

### Summary of the Flow

1. **Collector Config** (`serviceMonitorSelector`) matches → **ServiceMonitor Labels**.
2. **ServiceMonitor Config** (`selector.matchLabels`) matches → **Service Labels**.
3. **Service** routes traffic to → **Pod `/metrics` endpoint**.

By setting it up this way, whenever a developer wants to add a new application, they simply deploy
their Service and ServiceMonitor with the correct labels. The Operator (Prometheus or OTel)
automatically detects it and updates the Collector without you ever having to restart or manually
edit the Collector's configuration.

---

## The Role of the Gateway Collector

When you introduce a Proxy or Gateway Collector into your architecture, you are moving from a
single-tier collection strategy to a **two-tier architecture**.

In this setup, your first-tier collectors (the ones using the `ServiceMonitor` and Target Allocator
to scrape the Pods) no longer send data directly to your final database (like Datadog, Grafana Cloud,
or Splunk). Instead, they forward everything to the centralized Gateway Collector.

The Gateway is typically deployed as a standard Kubernetes `Deployment` with multiple replicas and a
Kubernetes `Service` in front of it to load-balance incoming traffic. Its primary responsibilities
are:

1. **Centralized Authentication:** Instead of distributing API keys or TLS certificates to 100
   different scraping collectors across your cluster, only the Gateway holds the credentials for your
   external observability backend.
2. **Data Enrichment & Redaction:** The Gateway is the perfect place to add cluster-wide tags (e.g.
   `cluster_name: us-east-prod`) or drop sensitive data (PII) before it leaves your network.
3. **Traffic Shaping & Routing:** It can route specific metrics to different destinations. For
   example, it can send security-related metrics to Splunk and performance metrics to
   Prometheus/Grafana.
4. **Batching and Egress Reduction:** It combines thousands of tiny data streams from your scraping
   collectors into large, efficient batches, drastically reducing the number of outbound network
   connections to your cloud provider.

### How to Set It Up (OpenTelemetry Example)

To build this, you need two configurations: one for the **Gateway** and one for the **Scraping
Collector** (the one we built in the previous step). The standard protocol for them to talk to each
other is **OTLP** (OpenTelemetry Protocol).

**1. Deploying the Gateway Collector**

The Gateway needs an **OTLP** receiver to accept traffic from your internal scrapers, some processors
to clean the data, and an exporter to send it to your final destination.

```yaml
apiVersion: opentelemetry.io/v1alpha1
kind: OpenTelemetryCollector
metadata:
  name: otel-gateway
  namespace: monitoring
spec:
  # Deployed as a scalable Deployment
  mode: deployment
  replicas: 3
  config: |
    receivers:
      # 1. Receive data from the scraping collectors via OTLP
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
    processors:
      # 2. Add standard processing
      memory_limiter:
        check_interval: 1s
        limit_mib: 1500
      batch:
        send_batch_size: 10000
        timeout: 5s
      # Add a global tag to all metrics passing through
      attributes/cluster:
        actions:
          - key: cluster.name
            value: "prod-us-east-1"
            action: insert
    exporters:
      # 3. Export to your final backend (e.g., Prometheus remote write, Datadog, etc.)
      prometheusremotewrite:
        endpoint: "https://my-hosted-prometheus.com/api/v1/write"
        headers:
          Authorization: "Bearer <SECRET_API_TOKEN>"
    service:
      pipelines:
        metrics:
          receivers: [otlp]
          processors: [memory_limiter, attributes/cluster, batch]
          exporters: [prometheusremotewrite]
```

> The OTel Operator will automatically create a Kubernetes Service for this Gateway, typically named
> `otel-gateway-collector` on port 4317.

**2. Modifying the Scraping Collector (From the previous step)**

Now, you go back to the collector that is doing the actual Prometheus scraping (the one connected to
the Target Allocator and ServiceMonitors). You must change its **Exporter**. Instead of sending data
to the internet, you tell it to export via OTLP to the Gateway's internal Kubernetes Service.

```yaml
apiVersion: opentelemetry.io/v1alpha1
kind: OpenTelemetryCollector
metadata:
  name: my-otel-scraper
  namespace: monitoring
spec:
  mode: statefulset
  targetAllocator:
    enabled: true
    prometheusCR:
      enabled: true
      serviceMonitorSelector:
        matchLabels:
          release: my-observability-stack
  config: |
    receivers:
      prometheus:
        config:
          scrape_configs: []   # Target Allocator handles this
    exporters:
      # CHANGED: Export to the Gateway using OTLP
      otlp/gateway:
        # Point to the Kubernetes Service created by the Gateway Deployment above
        endpoint: "otel-gateway-collector.monitoring.svc.cluster.local:4317"
        tls:
          insecure: true   # Safe inside the cluster network
    service:
      pipelines:
        metrics:
          receivers: [prometheus]
          exporters: [otlp/gateway]   # Route to the gateway
```

### The Final Data Flow

By adding the Gateway, your architecture now looks like this:

1. **ServiceMonitor** tells the **Target Allocator** where the application Pods are.
2. The **Target Allocator** assigns Pod IPs to the **Scraping Collectors** (Tier 1).
3. The **Scraping Collectors** pull the `/metrics` from the applications.
4. The **Scraping Collectors** instantly push that raw data via internal OTLP to the **Gateway
   Collector** (Tier 2).
5. The **Gateway Collector** receives it, adds the `cluster.name` label, batches the data
   efficiently, applies the secret API token, and pushes it over the internet to your **Observability
   Backend**.

---

## Deployment vs. StatefulSet for Collectors

One more option: **StatefulSet**.

A **StatefulSet** is a Kubernetes workload API object used to manage stateful applications. While
Deployments are great for stateless applications (where any pod can be replaced by another identical
pod), StatefulSets are designed for applications that require a persistent identity and stable storage
across rescheduling and restarts.

When deploying **metrics collectors** (like Prometheus, OpenTelemetry collectors, or Grafana Agents)
in Kubernetes, using a StatefulSet is highly beneficial for several key reasons:

### 1. Stable, Unique Network Identity

In a StatefulSet, each pod is assigned a sticky, unique identity (e.g. `collector-0`, `collector-1`,
`collector-2`).

- **Why it matters for metrics:** Metrics collectors often need to discover targets and scrape
  metrics without duplicating effort. With a stable hostname, you can configure target sharding
  (dividing the work of scraping targets among multiple collectors). If `collector-0` goes down and
  comes back up, it retains the same hostname, ensuring that other services or clustered collectors
  know exactly which shard of data it is responsible for.

### 2. Persistent Storage (PersistentVolumeClaims)

StatefulSets manage the deployment and scaling of a set of Pods, and provide guarantees about the
ordering and uniqueness of these Pods. They also seamlessly integrate with Persistent Volume Claims
(PVCs).

- **Why it matters for metrics:** Collectors often buffer or store metrics locally before forwarding
  them to a centralized backend (like Cortex, XSIAM, Prometheus Thanos, or a time-series database). If
  a pod crashes and is rescheduled to a new node, the StatefulSet ensures the new pod reattaches to
  the exact same Persistent Volume. This prevents data loss for metrics that were collected but not
  yet transmitted.

### 3. Ordered Deployment and Scaling

StatefulSets deploy pods in a strict, sequential order (0 to N-1). When scaling down, they are
terminated in reverse order.

- **Why it matters for metrics:** If your metrics collectors form a cluster (for example, a
  high-availability Prometheus setup), they may need to join the cluster gracefully. Ordered startup
  ensures that primary nodes or seed nodes are initialized before replicas try to join them.

### 4. Sharding and Hash-based Target Assignment

When you have thousands of applications emitting metrics, a single collector pod cannot scrape them
all. You must scale out the collectors.

- **Why it matters for metrics:** You can use the predictable pod identity (`collector-0`,
  `collector-1`) to implement consistent hashing. For example, the OpenTelemetry collector can look at
  its own pod name index to determine which fraction of the Kubernetes nodes or services it should
  scrape. If it is `collector-1` out of 3 total pods, it knows it is responsible for "Shard 1".

### Summary: Deployment vs. StatefulSet for Collectors

| Feature | Deployment (Stateless) | StatefulSet (Stateful) |
|---|---|---|
| **Pod Naming** | Random hashes (e.g. `coll-7b9f8...`) | Predictable (e.g. `coll-0`, `coll-1`) |
| **Storage** | Ephemeral (lost on restart) or Shared (ReadWriteMany) | Persistent and unique per Pod (ReadWriteOnce) |
| **Use Case** | Receiving metrics via push (stateless forwarding) | Scraping targets (pull), buffering data, clustering |

In short, if your metrics collector only receives data and immediately forwards it without caching, a
standard **Deployment** (or DaemonSet, if you want one per node) is usually fine. But if your collector
needs to **scrape specific targets reliably, buffer data locally** to prevent data loss during network
outages, or **form a cluster**, a **StatefulSet** is the correct architectural choice.

---

## The Operator Pattern

In Kubernetes, the **Operator Pattern** is a method of packaging, deploying, and managing complex,
stateful applications. It essentially takes the knowledge of a human SysAdmin or Site Reliability
Engineer (SRE) and turns it into software that runs continuously inside the cluster.

To understand Operators (like the Prometheus Operator or OpenTelemetry Operator), you need to
understand three core Kubernetes concepts: **The Reconciliation Loop**, **Custom Resource Definitions
(CRDs)**, and **Custom Controllers**.

### 1. The Core Concept: The Reconciliation Loop

Kubernetes is built on a declarative, "desired state" model. You don't tell Kubernetes *how* to do
something; you tell it *what you want*.

At the heart of Kubernetes are "Controllers" running infinite loops called **Reconciliation Loops**.

1. **Observe:** Look at the current state of the cluster (Reality).
2. **Compare:** Look at the YAML file you submitted (Desired State).
3. **Act (Reconcile):** Make changes to reality until it matches the desired state.

*Example:* If you submit a `Deployment` asking for 3 Pods, the Deployment Controller notices only 1
Pod is running, so it spins up 2 more.

### 2. Extending the API: Custom Resource Definitions (CRDs)

Out of the box, Kubernetes understands generic primitives like Pods, Services, Deployments, and
ConfigMaps. However, it doesn't know what a "Database," a "Message Queue," or a "Prometheus Server"
is.

A **CRD (Custom Resource Definition)** is how you teach the Kubernetes API new vocabulary. When you
install an Operator, it injects new CRDs into the cluster. Suddenly, instead of deploying a massive,
complex web of generic Pods and ConfigMaps, you can submit a simple YAML file to the API where
`kind: Prometheus` or `kind: PostgresDatabase`.

### 3. The Brains: The Custom Controller (The Operator)

CRDs are just data templates - they don't do anything on their own. To make them work, you need a
**Custom Controller**.

An **Operator** is simply a Custom Controller that is programmed with deep, domain-specific knowledge
about a particular application. It watches the Kubernetes API for the new CRDs you created and runs a
reconciliation loop specific to that software.

### How the Prometheus Operator Uses This Architecture

Let's look at the **Prometheus Operator** as the perfect example of this pattern in action.

Before the Operator existed, if you wanted to run Prometheus in Kubernetes, you had to manually
provision a StatefulSet, mount persistent volumes, write a massive `prometheus.yml` configuration
file, inject it as a ConfigMap, and manually restart the server every time a developer added a new
app. It was highly error-prone.

When you install the Prometheus Operator, here is what happens architecturally:

**A. It Installs CRDs (The Vocabulary)**

The Operator adds new objects to your cluster, most notably:
- `Prometheus`: Defines the actual Prometheus server (version, storage, replicas).
- `ServiceMonitor` & `PodMonitor`: Defines how to scrape metrics from apps.
- `PrometheusRule`: Defines alerting rules.

**B. It Runs the Operator Pod (The SRE Bot)**

A Pod spins up containing the Operator code (written in Go). It sits there watching the Kubernetes API.

**C. The Reconciliation Magic (Day 1 & Day 2 Operations)**

Now, you submit a simple YAML file to the cluster:

```yaml
kind: Prometheus
metadata:
  name: my-monitoring
spec:
  replicas: 2
```

1. **Day 1 (Deployment):** The Operator sees this `Prometheus` object. It knows exactly how to deploy
   Prometheus. It automatically creates the necessary `StatefulSets`, `Secrets`, and `Services`
   required to run a highly available Prometheus cluster.
2. **Day 2 (Configuration/Management):** A developer deploys a new app and a `ServiceMonitor`. The
   Operator sees the `ServiceMonitor`, dynamically writes the complex scrape configuration, updates
   the Prometheus ConfigMap, and hot-reloads Prometheus via its internal API - zero downtime, zero
   human intervention.

### Summary

The Operator Pattern is a combination of **Custom Resources** (which define what you want - your
complex app to look like) and a **Custom Controller** (which holds the operational logic to make it
happen). It abstracts away the massive complexity of managing stateful, distributed systems on
Kubernetes, turning manual operational tasks into automated, declarative code.

---

## End-to-End Lifecycle of a Single Metric

This follows the journey of a single counter metric - let's say `http_requests_total` - from the
moment a developer's code executes, all the way to a dashboard query. The metric's life cycle,
particularly within a robust, two-tier architecture using OpenTelemetry (OTel) and Kubernetes,
follows this structured flow:

### Phase 1: Generation & Exposure (The Application)

1. **The Code Executes:** A user visits a web page. Inside the application code, an HTTP handler fires.
2. **The Client Library:** The developer has instrumented their code using a Prometheus or
   OpenTelemetry client library. When the handler fires, the code calls `http_requests_total.inc()`.
   The client library increments the counter in the application's local, in-memory registry.
3. **The Metrics Endpoint:** The client library runs a tiny web server inside the application, usually
   on a dedicated path like `/metrics`. If you were to `curl` this endpoint, you would see plain text
   exposing the current state of that in-memory registry:

```
# HELP http_requests_total Total number of HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="GET", status="200"} 42
```

4. At this point, the metric just sits in the Pod's memory, waiting to be read.

### Phase 2: Discovery & Allocation (The Control Plane)

For the metric to leave the Pod, the collection system needs to find it. This happens out-of-band via
the Kubernetes API.

4. **The ServiceMonitor:** The developer deployed a `ServiceMonitor` YAML file. It tells the Kubernetes
   API: *"Hey, there is an app matching the label `app=frontend` that has metrics on port 8080 at
   `/metrics`."*
5. **The Target Allocator:** The OpenTelemetry Target Allocator (watching the Kubernetes API) sees the
   `ServiceMonitor`. It queries Kubernetes to find all Pod IPs that currently match `app=frontend`
   (e.g., `10.1.2.3` and `10.1.2.4`).
6. **The Sharding:** The Target Allocator looks at its pool of OpenTelemetry Scraper Replicas (e.g. 2
   Scraper Pods). It calculates a consistent hash and tells Scraper A to handle `10.1.2.3` and Scraper
   B to handle `10.1.2.4`.

### Phase 3: Scraping & Translation (The Scraper / Tier 1)

7. **The Scrape:** Scraper A wakes up, looks at its assigned targets, and executes an HTTP GET request
   to `http://10.1.2.3:8080/metrics`.
8. **The Receiver:** The `prometheus` receiver inside the OpenTelemetry Scraper downloads the plain
   text metric `http_requests_total{method="GET"} 42`.
9. **The Translation (Prometheus to OTLP):** OpenTelemetry does not process data in Prometheus text
   format. Immediately upon receiving it, the OTel Collector translates the metric into OTLP
   (OpenTelemetry Protocol).
   - The name `http_requests_total` is kept.
   - The type is mapped from Prometheus counter to OTLP Sum (cumulative, monotonic).
   - The label `method="GET"` is mapped to an OTLP Attribute.
   - It appends Kubernetes metadata (like `pod_name` and `namespace`) so you know exactly which Pod
     generated the 42.
10. **The Push:** The Scraper immediately pushes this OTLP payload over a fast, internal gRPC
    connection to the central Gateway Collector.

### Phase 4: Processing & Exporting (The Gateway / Tier 2)

11. **The Receiver:** The Gateway Collector receives the OTLP payload via its `otlp` receiver.
12. **The Pipeline (Processors):** The metric passes through a chain of processors configured by the
    platform team:
    - **Memory Limiter:** Checks if the Gateway is running out of RAM (if so, it drops data to prevent
      crashing).
    - **Resource/Attribute Processor:** Injects a global attribute, tagging the metric with
      `cluster.name = "prod-us-east"`.
    - **Filter Processor:** Looks for specific attributes and drops them if they contain PII
      (Personally Identifiable Information).
    - **Batch Processor:** Holds the metric in memory for a few seconds, bundling it with thousands of
      other metrics from across the cluster into one massive, compressed payload.
13. **The Exporter:** The Gateway's `prometheusremotewrite` exporter converts the OTLP batch back into
    Prometheus timeseries format (Snappy-compressed protobufs) and securely POSTs it over the internet
    to your long-term storage backend (e.g., Datadog, Grafana Mimir, AWS AMP).

### Phase 5: Storage & Visualization (The Backend)

14. **Ingestion:** The time-series database (TSDB) receives the payload. It indexes the metric name
    (`http_requests_total`) and its labels (`method`, `pod`, `namespace`, `cluster`).
15. **Storage:** The data point (`value: 42, timestamp: 1698243800`) is written to a Write-Ahead Log
    (WAL) on disk and eventually compacted into long-term block storage.
16. **The Query:** An SRE opens a Grafana dashboard and runs a PromQL query:
    `sum(rate(http_requests_total{cluster="prod-us-east"}[5m]))`
17. **The Result:** The TSDB retrieves the data points, calculates the per-second rate of requests over
    the last 5 minutes, sums them up across all Pods, and draws a line on a graph.

### Summary of the Metric's Journey

```
App Memory -> Scraped by OTel Scraper -> Converted to OTLP -> Enriched by OTel Gateway
-> Batched & Compressed -> Sent over Internet -> Indexed in TSDB -> Queried on a Dashboard.
```

---

## Diagram

> Validate this diagram against the prose above before relying on it.

```
ServiceMonitor ──Target Discovery──▶ Target Allocator
(Defines targets                     (Distributes scraping load)
 for scraping)                                │
       │                                      │ Prometheus Scrape
       └──────────────┬───────────────────────┘
                      ▼
            Tier 1 Scraping Collectors
            (Scrapes metrics from targets)
                      │ OTLP Forwarding
                      ▼
            OTLP Forwarding ──Export──▶ Observability Backend
                      ▲                  (Stores and visualizes metrics)
                      │
            Tier 2 Gateway Collector
            (Aggregates and processes metrics)
```

---

## OTEL VOCAB - Core Concepts Glossary

> *Practical glossary of common OpenTelemetry Collector terminology, focused on how it actually shows
> up in configs and pipelines.*

**Core Concepts**
- **Collector:** The service/binary that receives, processes, and exports telemetry (metrics, logs,
  traces).
- **Telemetry:** The three signal types: **metrics, logs, traces.**
- **Signal:** A category of telemetry:
  - Metrics
  - Logs
  - Traces

**Pipeline Architecture**
- **Pipeline:** A configured flow of telemetry: `receivers → processors → exporters`
- **Receiver:** Entry point for data into the collector (pull or push). Examples: `otlp`,
  `prometheus`, `jaeger`.
- **Processor:** Mutates, filters, or batches data in-flight. Examples: `batch`, `memory_limiter`,
  `attributes`, `resource`.
- **Exporter:** Sends telemetry to a backend. Examples: `otlp`, `prometheusremotewrite`, `logging`,
  vendor backends (Datadog, etc.).

**Data Model**
- **Resource:** Describes the entity producing telemetry (e.g., service, host, pod).
- **Resource Attributes:** Key-value metadata about the resource: `service.name=checkout`,
  `k8s.pod.name=xyz`.
- **Instrumentation Scope** (formerly Instrumentation Library): Identifies the library emitting
  telemetry.
- **Attributes:** Key-value pairs attached to spans, logs, or metrics.

**Tracing-Specific**
- **Span:** A single unit of work in a trace.
- **Trace:** A collection of spans representing a request flow.
- **Span Context:** Metadata identifying a span (`trace_id`, `span_id`).
- **Sampling:** Deciding which traces to keep:
  - Head sampling
  - Tail sampling (`tail_sampling` processor)

**Metrics-Specific**
- **Metric:** A time-series data point (counter, gauge, histogram, etc.).
- **Aggregation Temporality:**
  - Cumulative (total since start)
  - Delta (change since last collection)
- **Histogram:** Distribution of values across buckets.
- **Exponential Histogram:** More efficient histogram format (newer OTel feature).

**Logs-Specific**
- **Log Record:** A structured log entry.
- **Severity:** Log level (INFO, WARN, ERROR, etc.).
- **Body:** The main log message.

**Collector Components (Advanced)**
- **Connector:** Bridges pipelines (e.g., traces → metrics). Example: `spanmetrics`.
- **Extension:** Adds capabilities outside pipelines. Examples: `health_check`, `pprof`, `zpages`,
  `basicauth`.

**Processing & Control**
- **Batching:** Grouping telemetry before export (`batch` processor).
- **Backpressure:** When downstream systems are slow and collector buffers or drops data.
- **Queue / Retry:** Exporters often support retry + queue for reliability.
- **Memory Limiter:** Prevents collector from OOM (`memory_limiter` processor).

**Deployment & Scaling**
- **Agent Mode:** Runs close to workload (e.g., daemonset per node).
- **Gateway Mode:** Centralized collector receiving from many agents.
- **Target Allocator:** Distributes scrape targets across collectors (used with Prometheus receiver).

**Prometheus Integration (relevant to your work)**
- **Scrape Config:** Prometheus-style config inside the `prometheus` receiver.
- **Relabeling:** Modify labels during scraping.
- **Remote Write:** Export metrics via `prometheusremotewrite`.

**Config Terminology**
- **Component ID:** Named instance of a receiver/processor/exporter.

```yaml
otlp:
  ...
batch:
  ...
```

- **Service Section:** Where pipelines are defined.

```yaml
service:
  pipelines:
    metrics:
      receivers: [...]
      processors: [...]
      exporters: [...]
```

**Common Processors (worth knowing deeply)**
- `batch`
- `memory_limiter`
- `attributes`
- `resource`
- `filter`
- `transform`
- `tail_sampling`
- `k8sattributes`

**Debugging & Observability**
- **zPages:** Live debugging UI.
- **pprof:** Performance profiling endpoint.
- **Internal Metrics:** Collector emits its own metrics (e.g., dropped spans, queue size).

---

# PART B - OpenTelemetry Collector Enablement Guide

## Overview

The OpenTelemetry (OTel) Collector is a vendor-agnostic proxy that can receive, process, and export
telemetry data (metrics, logs, and traces). This document serves as an enablement guide for engineers
and architects to understand how to deploy, monitor, and configure the Collector, with a specific
focus on telemetry processing.

## 1. Deployment Patterns

Choosing the right deployment pattern for the OTel Collector depends on your infrastructure, scaling
needs, and security requirements. There are several primary deployment patterns, often used together
in a mature observability architecture.

### A. The Agent Pattern

In the Agent pattern, the Collector runs as close to the application as possible.

- **Kubernetes:** Deployed as a **DaemonSet** (one per node) or a **Sidecar** (one per pod).
- **Virtual Machines:** Deployed as a background service/daemon on the same host as the application.

**Pros:**
- **Context Enrichment:** Easily enriches telemetry with host or Kubernetes node/pod metadata.
- **Simplified App Configuration:** Applications simply send telemetry to localhost or the node IP. No
  need for complex routing or authentication in the app code.

**Cons:**
- Limited ability to do "tail-based" sampling (which requires seeing all spans for a trace across
  different nodes).
- Resource usage (CPU/Memory) on the application host.
- Load balancing relies on nodes having an even balance of traffic, which is not always the case.

### B. The Gateway Pattern

In the Gateway pattern, the Collector runs as a standalone, scalable cluster (usually a Kubernetes
Deployment behind a Load Balancer).

**Pros:**
- **Centralized Configuration:** Manage API keys, backend destinations, and global filtering in one
  place.
- **Advanced Processing:** Enables tail-based sampling, as the gateway can aggregate spans from all
  agents.
  - Note that for this to work, all spans for a trace must reach a single collector; this means having
    Agents that forward spans to the gateway collectors using the **Load Balancing exporter**.
- **Security:** Keeps credentials out of the edge nodes/agents. Only the Gateway needs egress access
  to external observability vendors.

**Cons:**
- Requires managing a separate cluster/infrastructure.
- Additional network hop.
- Requires a gRPC/HTTP/2-aware load balancer to avoid hotspots in individual collectors.
  - This can also be mitigated by using the **Load Balancing exporter** in agents that send to the
    gateway.

### C. The StatefulSet Pattern

In this pattern, the Collector is deployed as a **StatefulSet**, typically managed by the
OpenTelemetry Operator alongside the **TargetAllocator** component.

**Use Case:** The TargetAllocator is used as the standard best practice for discovering Prometheus
targets (via ServiceMonitors, PodMonitors, or static configs). Deploying the Collector as a
StatefulSet is the preferred scaling mechanism for scraping, as it allows the deployment to leverage
autoscaling, and balance the load of targets more effectively than a Daemonset.

**Pros:**
- **Scalability & Autoscaling:** Decouples the number of Collector replicas from the number of
  Kubernetes nodes, allowing autoscaling based on load rather than infrastructure size.
- **Resource Efficiency:** Avoids the overhead of running a resource-heavy DaemonSet on every single
  node just for metrics scraping.
- **Automatic Sharding:** The TargetAllocator handles the complex logic of dynamically dividing scrape
  targets among the StatefulSet replicas.
- **Drop-in Prometheus Replacement:** Excellent for teams migrating from a sharded Prometheus setup to
  an OpenTelemetry-based pipeline.

**Cons:**
- **Complexity:** Requires deploying the OpenTelemetry Operator and configuring the TargetAllocator
  component.
- **State Management:** StatefulSets require persistent networking identity, which adds slight
  operational complexity compared to stateless Deployments.

### Recommended Architecture: Agent + Gateway (+ StatefulSet for massive metrics)

For production, the best practice is often a combination:

1. **Agents** run on nodes/pods to scrape host metrics, collect application data via localhost, enrich
   it with local metadata (like Kubernetes pod names), and send it to the Gateway.
2. **Gateways** receive data from Agents, perform heavy processing (redaction, tail-sampling, global
   routing), and securely export it to your backend(s).
3. **StatefulSet Collectors** (Optional) are introduced specifically to handle massive scale Prometheus
   endpoint scraping alongside the Gateway, sharded via the TargetAllocator.

> The Argus reference config uses this combined strategy - we leverage an Agent for local collection of
> spans/push-based metrics, and forward telemetry to the Gateway for secondary processing
> (k8sattributes enrichment, and spanMetrics generation). A StatefulSet is also deployed to handle
> discovery/scraping of Prometheus metrics as needed.

## 2. Monitoring Collector Health

Treat the OTel Collector as a mission-critical component. If it goes down, your visibility goes down.
With that said, there are several mechanisms available to help operators monitor the health of the
collector and its configured pipelines.

### A. Health Check Extension

The `health_check` extension exposes an HTTP endpoint that responds to liveness and readiness probes.

```yaml
extensions:
  health_check:
    endpoint: 0.0.0.0:13133
service:
  extensions: [health_check]
```

- **Liveness:** Tells Kubernetes the container is running.
- **Readiness:** Tells Kubernetes the pipeline is fully initialized and ready to accept data.

### B. Self-Observability (Prometheus Metrics)

The Collector emits its own internal metrics, which should be scraped and monitored. By default, these
are exposed on port 8888.

```yaml
service:
  telemetry:
    metrics:
      address: 0.0.0.0:8888
```

**Key Metrics to Monitor:**
- `otelcol_process_memory_rss`: Memory usage. Monitor for OOM (Out of Memory) risks.
- `otelcol_receiver_refused_[spans|metrics|log_records]`: Indicates the Collector is dropping data at
  the door (usually due to full queues or memory limits).
- `otelcol_exporter_send_failed_[spans|metrics|log_records]`: Indicates issues communicating with your
  backend (network issues, rate limiting, bad credentials).
- `otelcol_processor_batch_batch_send_size`: Helps tune your batch processor.

> The Argus managed **OpenTelemetry Ingestion & Health** dashboard exposes many of these metrics by
> default, so customers scraping their collector metrics and sending them to the platform have an
> out-of-the-box view providing a high-level overview of collector health.

**Example Customer Collector Dashboard:** *(source linked an internal Argus dashboard URL - omitted)*

The dashboard shows panels such as:
- **OpenTelemetry Collector Receivers** - Receiver Metric Data Points Accepted / Refused; Receiver
  Trace Spans Accepted / Refused (Data Points / Second, Trace Spans / Second over time).

### C. Troubleshooting Extensions

- **zPages:** Exposes internal HTTP endpoints to view trace/metric processing stats in real-time. Good
  for debugging.
- **pprof:** Go performance profiling for CPU/Memory analysis if you suspect a memory leak, or need to
  understand what is driving resource utilization in the collector.

## 3. Pipeline Processors Deep Dive

Processors sit between **Receivers** and **Exporters**. They transform, filter, batch, and enrich data.

**CRITICAL RULE: Order Matters!**

The order in which processors are defined in the `pipelines` section of the service block is the exact
order they are executed.

- Always put the `memory_limiter` processor first, to best protect the collector against OOMs due to
  resource constraints.
- Always put the `batch` processor last, as batching should be done after any potential processors that
  drop or sample data.

### 3.1 Memory Limiter Processor (`memory_limiter`)

**Purpose:** The ultimate safeguard. It prevents the Collector from crashing due to Out Of Memory (OOM)
errors by dropping data or forcing garbage collection when memory thresholds are reached.

**Configuration:**
- `check_interval`: How frequently to check memory usage vs limits. This is usually set to 1s.
- `limit_mib`: The hard limit. If memory exceeds this, data is aggressively dropped, and the processor
  forces GC in the collector to try and free memory. This can also be expressed as a percentage via
  `limit_percentage`.
- `spike_limit_mib`: A buffer to handle sudden spikes. Dropping starts at `limit_mib − spike_limit_mib`.
  This can also be expressed as a percentage via `spike_limit_percentage`.

> The Argus reference collector config looks like the example below - for collectors that leverage HPA,
> the default `targetMemoryUtilization` is 70%, so HPA scaling should ideally kick in before the
> `memory_limiter` rejects traffic (assuming the workload does not ramp up too quickly).

```yaml
processors:
  # WARNING: Percentages are intended to be relative
  # to the container's memory limit (not request).
  memory_limiter:
    check_interval: 1s
    limit_percentage: 90
    spike_limit_percentage: 10
# Pipeline execution:
# service:
#   pipelines:
#     traces:
#       processors: [memory_limiter, ...]
# However, if the container's memory limit is not set,
# the memory_limiter will silently fall back
# to interpreting the percentages relative to the total memory on the Node.
```

### 3.2 Resource Detection Processor (`resourcedetection`)

**Purpose:** Automatically detects host-level information (Cloud provider, OS, Kubernetes node, EC2
instance ID) and appends it to the resource attributes of the telemetry data.

The resource detection processor supports several different detectors to enrich telemetry data with
resource attributes related to the host that the collector is running on. The
[processor documentation] provides a complete overview of the different detectors and the information
that they can add to telemetry when used.

**Note:** Because resource detection is automatically scoped to the running host, this processor should
only be used when running the collector as an Agent.

```yaml
processors:
  resourcedetection:
    detectors: [system, gcp]
    timeout: 2s
    override: false   # Do not override attributes already provided by the app
```

### 3.3 Kubernetes Attributes Processor (`k8sattributes`)

**Purpose:** Essential for Agent deployments in Kubernetes. It discovers Kubernetes metadata (Pod Name,
Namespace, Node Name, Deployment, Labels, Annotations) and adds them as resource attributes based on
the incoming IP address of the pod.

**Note:** Requires RBAC permissions (ServiceAccount) to read the Kubernetes API.

```yaml
processors:
  k8sattributes:
    pod_association:
      # Trust existing resource attributes that uniquely identify a Pod, if present.
      - sources:
          - from: resource_attribute
            name: k8s.pod.ip
      - sources:
          - from: resource_attribute
            name: k8s.pod.uid
      - sources:
          # Require that both Pod name and Namespace name match
          # since Pod names are only unique within their Namespace.
          - from: resource_attribute
            name: k8s.pod.name
          - from: resource_attribute
            name: k8s.namespace.name
      # Use the IP of the sender, if any.
      - sources:
          - from: connection
    # Block advancing telemetry on startup until we have metadata available.
    wait_for_metadata: true
    wait_for_metadata_timeout: 10s
    extract:
      metadata:
        - k8s.daemonset.name
        - k8s.deployment.name
        - k8s.namespace.name
        - k8s.node.name
        - k8s.pod.name
        - k8s.pod.uid
        - k8s.statefulset.name
      labels:
        - from: pod
          key: app.kubernetes.io/name
          tag_name: app.kubernetes.io/name
        - from: pod
          key: app.kubernetes.io/component
          tag_name: app.kubernetes.io/component
```

The `k8sattributes` processor can be used in either a Gateway or an Agent deployment configuration,
although in the latter case it is important to scope the processor to only fetch metadata related to
the node the Agent is running on, to limit resource consumption and API server load:

```yaml
processors:
  k8sattributes:
    # Only fetch metadata related to the Node the collector is running on.
    filter:
      node_from_env_var: KUBE_NODE_NAME
```

If running an Agent+Gateway architecture where the Gateway is performing enrichment, the Agent should
include the `k8sattributes` processor configured in passthrough mode, which ensures the IP address of
the pod that telemetry came from is preserved as the source when sent to the gateway (this ensures
that the `connection` source for `pod_association` works as expected for forwarded telemetry):

```yaml
# Forward the source Pod IP to the Gateway's k8sattributes processor.
k8sattributes/passthrough:
  passthrough: true
```

### 3.4 Resource Processor (`resource`)

**Purpose:** Manually mutate resource-level attributes (the metadata that applies to an entire batch of
spans/metrics/logs, like `service.name` or `deployment.environment`).

When doing manual enrichment of telemetry with attributes related to the environment where a collector
is running (like environment, cluster, region, etc), it is best to use the Resource processor rather
than the Attributes processor, as the former is more inline with the intent of how resource attributes
vs data-attributes should be differentiated (**hint:** resource attributes are related to the
environment/entity the telemetry comes from, data attributes are related to the telemetry/the request
it represents).

```yaml
processors:
  resource:
    attributes:
      - key: environment
        value: "production"
        action: insert
      - key: cloud.region
        action: delete
```

### 3.5 Transform Processor (`transform`)

**Purpose:** The most powerful data mutation tool in the Collector. It uses the OpenTelemetry
Transformation Language (OTTL) to interact with telemetry data at a granular level. Useful for redacting
PII, dropping specific spans, or complex conditional logic.

*[OTTL Function Reference]*

In the Argus reference OTel configuration, we leverage the Transform processor to do conditional
processing of telemetry, usually to target specific metrics in a pipeline.

**Example:**

Example from our reference config that removes `k8sattributes` from `kube-state-metrics` where the
enriched attributes are for the `kube-state-metrics` pod and not other pods in the cluster:

```yaml
# Kube-state-metrics is a special kind of scrape target because it's a Pod that
# reports metrics about other Pods. For metrics related to Pods, we remove the "k8s.*"
# attributes that were automatically added by the prometheus receiver since those refer to
# the KSM Pod itself. Then the "resource" processor below will set the correct attributes
# with a clean slate.
transform/kube-state-metrics:
  error_mode: ignore
  metric_statements:
    - context: resource
      conditions:
        - attributes["k8s.container.name"] == "kube-state-metrics" and
          attributes["namespace"] != nil and attributes["pod"] != nil
      statements:
        - delete_matching_keys(attributes, "k8s\\.*")
```

Another example that redacts information from spans in a pipeline:

```yaml
processors:
  transform:
    error_mode: ignore
    trace_statements:
      - context: span
        statements:
          # Redact sensitive data from a specific attribute
          - replace_pattern(attributes["http.target"], "password=[^&]*", "password=REDACTED")
```

### 3.6 Group By Attributes Processor (`groupbyattrs`)

**Purpose:** Re-groups Spans, Metrics, or Logs in the pipeline to a Resource based on the specified
attributes. Said differently, this processor will "promote" specified attributes to the resource scope.

**Use Case:** In the Argus reference configuration, we leverage the group by attributes processor to
elevate labels on metrics from cAdvisor and Kube State Metrics (such as pod and namespace) from the
datapoint scope to the resource scope, so they can be used as source attributes in the `k8sattributes`
processor. This allows for more flexible metadata enrichment than can be done with Prometheus relabel
rules, as the target metrics are being scraped from is not the entity that the metrics describe.

```yaml
processors:
  # Elevate these datapoint attributes (from Prom labels)
  # to resource attributes.
  # This is necessary so we can refer to them in the "resource"
  # context in the processors below.
  groupbyattrs/pod-association:
    keys:
      - namespace
      - pod
      - container
```

### 3.7 Batch Processor (`batch`)

**Purpose:** Groups telemetry into batches before sending them to the Exporter.

**Why it's essential:** It vastly improves network efficiency, enables better data compression when
using zstd or gzip, and reduces the number of outgoing connections and API requests to your backend.
**Always place this last in your pipeline.**

The Argus reference config uses a batch size of 1k elements, with a max batch size of 2k elements and a
1s timeout. This provides a good baseline level of batching/compression that trades memory overhead
batching elements client-side (and memory overhead processing batches platform-side) vs network
compression/number of requests made to the platform.

The timeout can be adjusted to larger values if collector metrics show that most batches are being sent
after the timeout is triggered, as opposed to reaching the desired batch size. The tradeoff here is that
larger timeouts means waiting longer to send data to Argus, so it affects time-to-data being queryable
within the platform. This should not be a significant concern though, as batch timeouts should always be
on the order of seconds.

**Configuration:**

```yaml
processors:
  batch:
    timeout: 1s            # Max time to wait before sending a partial batch
    send_batch_size: 1000  # Number of spans/metrics/logs before sending
    send_batch_max_size: 2000  # Hard limit to prevent payload too large errors
```

## Exporting Telemetry to Argus

To send telemetry to the Argus platform from the OTel collector, we need to configure an OTLP exporter
like shown below:

```yaml
exporters:
  otlp/argus:
    endpoint: ${env:ARGUS_OTLP_GRPC_ENDPOINT}
    timeout: 30s
    retry_on_failure:
      enabled: true
    sending_queue:
      num_consumers: 50
    compression: zstd
    headers:
      API-Token: ${env:ARGUS_API_TOKEN}
      # Return a description if metrics sent to the platform are rejected
      # due to validation issues
      Argus-Metrics-Validation-Response: SHORT
```

A couple of key points to call out in the above configuration:
- The **`timeout`** should be set to a reasonable value and not too aggressive; here we are using 30s as
  a baseline.
  - Larger batch sizes will take longer to process, so if `send_batch_size` is increased, the timeout
    should be as well.
- Set **`compression: zstd`** to get the best data compression and minimize the cost of egressing data
  to the platform. `gzip` is also supported on the backend, but tends to be slightly less efficient.
- **`sending_queue`** controls the number of workers that deque batches from the exporter's queue of
  items to send. Having more allows the collector to have a higher max throughput of exported telemetry
  (along with more compute resources).
  - Monitoring `otelcol_exporter_queue_size` is a good way to determine whether the `num_consumers` may
    need to be increased; if the queue size is small, the collector is able to keep up with its workload
    effectively.
  - Note: The default `num_consumers` is 10, and the default `queue_size` is 1000.

For sending **Logs** to Argus, we should configure a separate exporter that sends OTLP data over HTTP,
instead of the gRPC based exporter shown above:

```yaml
exporters:
  otlphttp/argus:
    endpoint: ${env:ARGUS_OTLP_HTTP_ENDPOINT}
    timeout: 30s
    retry_on_failure:
      enabled: true
    sending_queue:
      num_consumers: 50
    compression: zstd
    headers:
      API-Token: ${env:API_TOKEN}
```

> The core configuration is fundamentally the same as it is for the gRPC based exporter. Argus supports
> ingesting Metrics/Logs via the OTLPHTTP exporter as well, but our preferred method is via gRPC.

## Example Recommended Pipeline Assembly

Here is an example of how these processors are combined into pipelines, using the reference Gateway
configuration that Argus provides:

```yaml
service:
  pipelines:
    metrics:
      receivers:
        - otlp
        - forward/self-scrape
      processors:
        - memory_limiter        # 1. Protect against OOM
        - k8sattributes         # 2. Add K8s context
        - resource/default      # 3. Standardize resource attributes
        - batch                 # 4. Compress and batch for export
      exporters:
        - otlp/argus
    traces:
      receivers:
        - otlp
      processors:
        - memory_limiter        # 1. Protect against OOM
        - k8sattributes         # 2. Add K8s context
        - resource/default      # 3. Standardize resource attributes
        - batch                 # 4. Compress and batch for export
      exporters:
        - otlp/argus
        - spanmetrics           # connector at the end of the main "traces"
                                # pipeline (no receivers; does attribute
                                # enrichment of the generated span metrics)

    # We create a separate pipeline that uses the same receivers but does no
    # additional processing before exporting them.
    metrics/spanmetrics:
      receivers:
        - spanmetrics
      processors:
        - memory_limiter        # 1. Protect against OOM
        - resource/spanmetrics  # 2. Standardize resource attributes
        - groupbyattrs/spanmetrics  # 3. Group for processing
        - transform/spanmetrics     # 4. Drop noisy data
        - metricstransform/spanmetrics  # 5. Aggregate/de-dupe series
        - batch                 # 6. Compress and batch for export
      exporters:
        - otlp/argus

    # Use a separate pipeline for self-scrape so we can run additional
    # processors before forwarding to the regular pipeline.
    metrics/self-scrape:
      receivers:
        - prometheus/self-scrape
      processors:
        - memory_limiter        # 1. Protect against OOM
        - resource/self-scrape  # 2. Standardize resource attributes
      exporters:
        - forward/self-scrape
```



# crds/

OpenTelemetry Operator and Prometheus Operator custom resources Argus renders: `OpenTelemetryCollector`
(Agent / Gateway / TargetAllocator), `ServiceMonitor`, `PodMonitor`. This is runtime config; the agent
regenerates these from the contract rather than hand-editing collector YAML.

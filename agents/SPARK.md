# Spark

## Finding Spark UIs on Falcon

Spark drivers with an exposed UI get a Kubernetes service labeled
`spark-ui.mai.microsoft.com/exposed=true`, reachable over Tailscale. List them
with the driver pod and UI URL (set `--context` to the cluster, e.g.
`falcon-phx-ca`, `falcon-dfw-ca`):

```bash
kubectl --context falcon-phx-ca get services -A \
  -l spark-ui.mai.microsoft.com/exposed=true -o json |
jq -r '
  (["NAMESPACE", "DRIVER", "SPARK_UI"],
   (.items[]
    | select(.metadata.namespace | IN("souschef", "souschef-airflow", "souschef-airflow-staging"))
    | [
        .metadata.namespace,
        .metadata.annotations["spark-ui.mai.microsoft.com/driver-pod"],
        if .status.loadBalancer.ingress[0].hostname then
          "http://\(.status.loadBalancer.ingress[0].hostname):4040/"
        else
          "<Tailscale ingress pending>"
        end
      ]))
  | @tsv
' | column -t -s $'\t'
```

Airflow-launched drivers run in `souschef-airflow` and are named after the task
(e.g. `data-pipeline-ingest-spark-metrics-*` for `ingest_spark_metrics`). The
Spark REST API is at `<SPARK_UI>/api/v1`.

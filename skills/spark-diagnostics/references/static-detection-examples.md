# Static Detection Examples

Use these examples when the static auditor output needs manual review or fixtures.

## Driver Collection

Detect direct and multi-line variants:

```python
rows = (
    df
    .filter("status = 'active'")
    .collect()
)
```

```python
pdf = (
    df.select("id", "payload")
    .toPandas()
)
```

Prefer bounded samples, aggregations, or distributed writes over collecting production data to the driver.

## Serverless Unsupported APIs

Flag these in Databricks serverless paths:

- `.rdd`
- `.rdd.isEmpty()`
- `SparkContext`
- `sc.broadcast` for runtime payload/serialization review
- DataFrame `cache`/`persist`/`unpersist`/`checkpoint`, catalog cache methods, and SQL `CACHE`/`UNCACHE`/`REFRESH`/`CLEAR CACHE`
- DBFS root temp paths

## Databricks YAML Cluster Contracts

Flag likely runtime-contract hazards in DAB or bundle YAML:

```yaml
resources:
    jobs:
        train_job:
            job_clusters:
                - job_cluster_key: gpu_train
                    new_cluster:
                        spark_version: 15.4.x-gpu-ml-scala2.12
                        node_type_id: g5.4xlarge
                        num_workers: 0
```

This is not proof the job is wrong in every mode. The scanner labels it low
confidence unless the YAML itself establishes Spark-task context; confirm the
assigned task, access mode, and runtime evidence before changing topology.

Also review GPU node types after topology changes:

```yaml
new_cluster:
    node_type_id: g5.12xlarge
    num_workers: 1
```

Verify from runtime evidence that training uses worker GPUs, or document why it
intentionally does not. A node type alone does not prove GPU utilization.

## SQL And Table Coverage Hazards

Flag wall-clock windows in reproducibility-sensitive diagnostics:

```sql
SELECT *
FROM catalog.schema.training_source
WHERE event_date >= current_date() - INTERVAL 30 DAYS;
```

Prefer fixed parameters or dates for diagnostic repros:

```sql
SELECT *
FROM catalog.schema.training_source
WHERE event_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31';
```

Flag prep-table references for lineage audit, not automatic deletion:

```sql
SELECT * FROM prep_train
UNION ALL
SELECT * FROM prep_val;
```

Inventory producers and consumers before removing or materializing these intermediates.

## UDF And Context Misuse

Review UDFs for captured Spark sessions, broadcast variables created in the wrong scope, and large Python objects serialized to workers.

## Fixture Guidance

When adding auditor fixtures, include both positive and negative examples:

- one case that must be flagged
- one safe bounded action
- one Databricks YAML job cluster with explicit Spark-task context and one intentionally non-Spark single-node task
- one SQL wall-clock window and one fixed-window query
- one quoted string or comment that should not be flagged

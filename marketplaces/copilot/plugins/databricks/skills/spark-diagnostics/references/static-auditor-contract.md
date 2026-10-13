# Static Auditor Contract

From the target repository root, run:

```powershell
python -B <skill-root>/scripts/audit_spark_antipatterns.py .
```

The command always writes one schema-1 JSON document to stdout. It uses exit 0
when scanning completed, even if findings exist; exit 1 when bounded scanning
was incomplete; and exit 2 for an invalid path or argument. The report contains
`complete`, root-relative slash paths under `root: "."`, summary counts,
findings, and value-free diagnostics. Source lines are absent by default.

Use `--excerpts` only when a bounded redacted source hint is necessary. All caps
must be positive. Default limits cover files, each file's bytes, total bytes,
notebook code cells, individual notebook-cell bytes, notebook JSON nodes,
findings, diagnostics, and directory entries. Tighten them for an untrusted or
very large repository, for example:

```powershell
python -B <skill-root>/scripts/audit_spark_antipatterns.py . --max-files 2000 --max-total-bytes 33554432
```

The scanner reads regular contained files only. On POSIX it traverses through
descriptor-anchored directories and opens descendants with `openat` plus
`O_DIRECTORY`/`O_NOFOLLOW`; a parent-path swap cannot redirect an already-open
directory's reads. On Windows it opens the reparse point itself, rejects a
reparse handle, derives the final handle path before content is read, and rejects
handles outside the point-in-time canonical root. It validates `fstat` on the
opened descriptor and holds Windows sources against replacement while reading.
If the necessary safe-open or identity capability is unavailable, it fails that
file or directory closed. It rejects direct symlink or reparse targets and skips
linked/reparse descendants, malformed files, invalid UTF-8, malformed notebooks,
permission errors, and capped inputs while keeping valid neighboring files in
scope. `complete: false` means a report is a partial slice, not proof of absence.

This is a point-in-time local source audit, not a hostile-concurrent-filesystem
authorization mechanism. The scanner rejects discovered identity or containment
changes instead of following them, but operating-system namespace races outside
the available descriptor/handle guarantees remain outside its authority.

Notebook input stays byte-bounded before object expansion. The scanner event-walks
the `cells` array and only calls the standard JSON decoder for one cell after its
byte and structural-node budgets pass; it never deserializes the complete notebook
object. It validates unknown top-level members, the closing object, duplicate
`cells` members, and whitespace-only EOF before reporting a complete scan. The
current implementation still keeps one capped raw file and briefly validates its
UTF-8, so its memory bound is proportional to `--max-file-bytes`, not
constant-memory streaming.

## Interpretation Limits

The Python scanner masks comments and string literals with Python tokens, so
comments, quoted examples, and fenced literal examples are not evidence. Member
calls receive high or medium Spark findings only when local AST assignments show
SparkSession, DataFrame, or SparkContext provenance. Unknown receivers become
low-confidence `possible_*` leads. SQL comments and quoted literals are likewise
masked. Databricks `%%sql` and `%sql` notebook cells use SQL lexical rules; other
notebook magics are reported as unsupported rather than parsed as Python. YAML
GPU and worker findings are constrained to their own `new_cluster` and containing
job, never context borrowed from a different job. Review every finding against the
target runtime; a static match alone does not prove the code runs on serverless.

`show()` is reported only for explicit high-output or untruncated requests.
`count()` is an action worth justifying, not proof of a defect. Broadcast
findings require runtime payload and serialization evidence; source length says
nothing about object size. YAML `num_workers` and GPU findings are calibrated to
explicit configuration context, not guesses about the job.

Excerpt redaction covers common Authorization/Bearer, provider, AWS, Databricks,
JWT, key/value, and URL-credential shapes before serialization. It is a defense
in depth layer, not a claim that regex can recognize every secret format; avoid
`--excerpts` for sensitive repositories unless the bounded evidence is needed.

## Current Runtime Basis

Databricks documents that serverless supports Spark Connect APIs rather than RDD
APIs, limits DBFS access, and rejects DataFrame, catalog, and SQL cache APIs.
Use its current [serverless limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations)
as the authoritative compatibility surface. `SparkContext` is also unavailable
on Standard access mode and serverless, per the [unsupported Spark Connect
feature reference](https://docs.databricks.com/aws/en/error-messages/unsupported-connect-feature-error-class).

For DBFS findings, distinguish root and mounts from reserved `dbfs:/Volumes`,
`dbfs:/databricks-datasets`, and MLflow system paths. Databricks documents those
categories and the disabled-root behavior in [Disable access to DBFS root and
mounts](https://docs.databricks.com/aws/en/dbfs/disable-dbfs-root-mounts), and
recommends volumes, external locations, or workspace files in [DBFS and Unity
Catalog best practices](https://docs.databricks.com/aws/en/dbfs/unity-catalog).

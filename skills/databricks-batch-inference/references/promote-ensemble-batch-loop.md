# Promote And Ensemble Batch Loop

Use this reference when promotion, threshold, or ensemble changes need controlled batch-scoring runs.

## Loop Contract

1. Review metrics, false positives, false negatives, null outputs, and unscorable rows for the current window.
2. Change one promotion, threshold, feature, or ensemble rule at a time.
3. Resolve the requested model alias once; validate the immutable version URI, verified run ID, exact scoring signature, source Delta version, and observation timestamp before deployment.
4. Deploy or update the job with the owning Databricks workflow.
5. Run the next bounded date or partition window, not all history.
6. Monitor until the Databricks job reaches a real terminal state.
7. Run reconciliation SQL and review sample source fields against output predictions.

When the user asks to deploy, run, watch, and debug, hand off to `databricks-deploy-monitor` for deployment and monitoring. Return here when the job succeeds so scoring reconciliation and output review are completed.

## Broken Model Version Handling

If a model version has zeroed metrics, corrupt outputs, unusable schema, or reconciliation proves it cannot be scored safely:

- stop new scoring runs that resolve to that version
- remove or move the production alias before considering deletion
- capture registered model name, version, run ID, aliases, current stages, and downstream jobs or endpoints
- verify no active job, serving endpoint, or audit record still requires the version
- delete only when the registry operation is supported and the user explicitly wants deletion

Quarantine first, delete second. The safe default is to de-alias or move traffic away from the version.

## No-Rerun Cleanup Rule

Separate data correctness from terminal or notebook cleanup errors.

If the user says the results are good and asks to fix a post-run error without rerunning:

- patch the cleanup, display, export, or terminal-status error
- validate with static checks, unit-level checks, or a tiny local reproduction when possible
- do not rerun the Databricks scoring job unless the user later asks for a rerun

## Reviewable Trace

Promotion and ensemble scoring should preserve:

- source window and source filter
- source Delta version and observation timestamp definition
- requested alias, immutable model URI, resolved version, and verified run ID
- threshold, label map, ensemble weights, or rule version
- row counts before and after scoring
- duplicate-key counts
- unscorable counts and reasons
- sample links or keys for reviewed rows

Use [../assets/scoring-run-audit-ddl.sql](../assets/scoring-run-audit-ddl.sql) when this trace should be durable.

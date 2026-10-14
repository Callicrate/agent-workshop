# Databricks Project Status: $PROJECT_NAME

## Contents

- [Report Metadata](#report-metadata)
- [Executive Summary](#executive-summary)
- [Scope And Source Coverage](#scope-and-source-coverage)
- [Health Scorecard](#health-scorecard)
- [Active Incidents And Failures](#active-incidents-and-failures)
- [Reliability And Uptime](#reliability-and-uptime)
- [Jobs And Pipelines](#jobs-and-pipelines)
- [Tables And Data Products](#tables-and-data-products)
- [Models And MLflow](#models-and-mlflow)
- [Serving Endpoints](#serving-endpoints)
- [Usage, Performance, And Cost](#usage-performance-and-cost)
- [Security And Operational Readiness](#security-and-operational-readiness)
- [Trends And Emerging Risks](#trends-and-emerging-risks)
- [Findings](#findings)
- [Prioritized Recommendations](#prioritized-recommendations)
- [Unknowns And Evidence Gaps](#unknowns-and-evidence-gaps)
- [Evidence Appendix](#evidence-appendix)

## Report Metadata

- **Report path:** `$REPORT_PATH`
- **Generated at:** `$GENERATED_AT` UTC
- **Repository root:** `$PROJECT_ROOT`
- **Repository branch:** `$REPOSITORY_BRANCH`
- **Repository commit:** `$REPOSITORY_COMMIT`
- **Repository worktree:** `$REPOSITORY_WORKTREE`
- **Databricks profile:** `$PROFILE`
- **Workspace host / principal:** Unknown until verified
- **Environment:** Unknown until verified
- **Current window:** `$REVIEW_START` to `$REVIEW_END`
- **Baseline window:** `$BASELINE_START` to `$BASELINE_END`
- **Window timezone:** `$WINDOW_TIMEZONE`
- **Window basis:** `Latest complete days`
- **Operation mode:** Inspect-only

<!-- STATUS-REPORT: Replace every STATUS-REPORT comment with evidence-backed content before validation. -->

## Executive Summary

- **Overall status:** `unknown`

<!-- STATUS-REPORT: State healthy, watch, degraded, critical, or unknown; name the strongest evidence, active failures, and most urgent action. -->

## Scope And Source Coverage

<!-- STATUS-REPORT: List included resource classes, evidence sources, unavailable sources, exclusions, and freshness of the review itself. -->

### Target Manifest

| Resource class | Stable ID or name | Environment | Owner | Source of truth | Live state | Inspected |
|---|---|---|---|---|---|---|
| Jobs / pipelines | Unknown | Unknown | Unknown | Unknown | Unknown | No |
| Tables / data products | Unknown | Unknown | Unknown | Unknown | Unknown | No |
| MLflow / registered models | Unknown | Unknown | Unknown | Unknown | Unknown | No |
| Serving endpoints | Unknown | Unknown | Unknown | Unknown | Unknown | No |
| Warehouses / dashboards / alerts | Unknown | Unknown | Unknown | Unknown | Unknown | No |

### Evidence Coverage Ledger

<!-- STATUS-REPORT: Coverage must be complete, partial, blocked, or not applicable. Complete or partial rows use pages=<n>; raw=<n>; unique=<n> and exactly requested=<date..date|timestamp..timestamp|all|non-temporal>; observed=<date..date|timestamp..timestamp|none|snapshot:<timestamp>|unknown>. Complete rows cannot use observed=unknown. Non-temporal rows require snapshot:<offset-aware-timestamp>. Explain the affected conclusions for partial, blocked, or not-applicable rows. -->

| Evidence source | Project filter / scope | Pages / raw / unique records | Requested / observed window | Coverage | Limitation and affected conclusions |
|---|---|---|---|---|---|
| Live resource and history APIs | Unknown | Unknown | Unknown | Unknown | Unknown |

## Health Scorecard

<!-- STATUS-REPORT: Use healthy, watch, degraded, critical, unknown, or not applicable. Do not invent an overall numeric score. -->

| Dimension | Status | Current window | Baseline | Trend | Strongest evidence |
|---|---|---|---|---|---|
| Reliability / uptime | Unknown | Unknown | Unknown | Unknown | Unknown |
| Jobs / pipelines | Unknown | Unknown | Unknown | Unknown | Unknown |
| Tables / data quality | Unknown | Unknown | Unknown | Unknown | Unknown |
| Models / MLflow | Unknown | Unknown | Unknown | Unknown | Unknown |
| Serving | Unknown | Unknown | Unknown | Unknown | Unknown |
| Usage / performance / cost | Unknown | Unknown | Unknown | Unknown | Unknown |
| Security / operations | Unknown | Unknown | Unknown | Unknown | Unknown |

## Active Incidents And Failures

<!-- STATUS-REPORT: List active incidents first. State none observed only after source coverage supports that claim. -->

| Finding ID | Severity | Signature | Resource / task | Occurrences / affected runs | Status and recovery | First / last seen | Impact | Evidence |
|---|---|---|---|---|---|---|---|---|

## Reliability And Uptime

<!-- STATUS-REPORT: Define uptime per resource. Include expected opportunities, starts, successes, failures, cancellations, retries, last success, duration, queue delay, recovery, and SLAs. -->

## Jobs And Pipelines

<!-- STATUS-REPORT: Cover task-level failures, trigger type, schedules or source events, runtime settings, retries, output completeness, Lakeflow update/event/expectation health, and live/source drift. -->

## Tables And Data Products

<!-- STATUS-REPORT: Cover freshness, bounded row/storage growth, missing windows, schema drift, nulls, duplicates, SCD2 or expectation checks, consumer mapping, and evidence retention. -->

## Models And MLflow

<!-- STATUS-REPORT: Cover run cadence/status, current and prior versions, metric definitions and deltas, thresholds, provenance, source freshness, registry drift, and monitoring. -->

## Serving Endpoints

<!-- STATUS-REPORT: Cover endpoint state, served versions, routing, events, request volume, errors, latency, fallbacks, schema violations, logs, drift, and semantic readiness. Mark not applicable with evidence when no endpoint exists. -->

## Usage, Performance, And Cost

<!-- STATUS-REPORT: Cover attributable and unattributed usage/cost, runtime, query or request volume, latency, retries, DBUs, data growth, unit economics, and efficiency versus baseline. State access gaps. -->

## Security And Operational Readiness

<!-- STATUS-REPORT: Cover run-as identity, owners/permissions, policy/runtime drift, notifications/alerts, timeouts, idempotency, recovery, and source-of-truth drift. -->

## Trends And Emerging Risks

<!-- STATUS-REPORT: Separate confirmed trends from sparse watch items. Include sample counts, thresholds, projected failure mechanism, and horizon only when evidence supports it. -->

## Findings

<!-- STATUS-REPORT: Use unique finding IDs, severity critical/high/medium/low, status current/recurring/resolved/emerging, and confidence high/medium/low. Follow review-workflow.md for lifecycle floors; do not infer/default lifecycle or rewrite historical reports. If no findings remain, state that explicitly with the evidence scope instead of adding a synthetic row. -->

| Finding ID | Severity | Status | Confidence | Resource / owner | Evidence and impact | Recommendation | Verification |
|---|---|---|---|---|---|---|---|

## Prioritized Recommendations

### Immediate

<!-- STATUS-REPORT: Current critical/high failures and containment or recovery recommendations. -->

### Near Term

<!-- STATUS-REPORT: Changes justified for the next normal delivery window. -->

### Longer Term

<!-- STATUS-REPORT: Evidence-backed resilience, observability, cost, or maintainability improvements. -->

## Unknowns And Evidence Gaps

<!-- STATUS-REPORT: List inaccessible APIs, system tables, missing baselines, undefined SLAs, absent owners, and conclusions weakened by each gap. -->

## Evidence Appendix

<!-- STATUS-REPORT: Record inspected API objects, run IDs, statement IDs, table/model/endpoint names, bounded queries, dashboards, logs, repository commit, and collection timestamps. Do not include secrets. -->

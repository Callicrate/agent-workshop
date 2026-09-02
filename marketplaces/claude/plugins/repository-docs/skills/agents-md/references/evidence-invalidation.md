# Evidence Invalidation

Use this only for live operations, CTF/security labs, deployment, remote access, or repositories where environment state can poison prior findings.

## Invalidation Triggers

Document triggers that require revalidation before future agents rely on earlier evidence:

- VPN, route, proxy, or network change
- service restart, reset, redeploy, or data refresh
- regenerated credentials, sessions, cookies, or tokens; record the invalidation trigger, not the sensitive value
- failed environment setup discovered after work was done
- teammate or user correction that invalidates a core assumption
- stale status file or known poisoned run
- target, cluster, warehouse, or runtime change

Reuse prior evidence only if it was produced in the current working tree state.
Re-run or re-check after branch changes, dependency updates, file moves, environment resets, or user corrections.

## AGENTS.md Pattern

For operational repositories, add compact Coordination and Evidence guidance when relevant.
Include:

- what evidence is invalidated
- why it is invalidated
- affected time window or run identifiers
- required revalidation command or check
- where to record replacement evidence

## Rule

When the user corrects a core assumption, explicitly invalidate prior affected work before continuing.
Do not preserve prior negative results as evidence after a known environment fault.

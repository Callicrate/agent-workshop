# State Transition Patterns

Use this reference for product UIs that show live resources, closed resources, polling data, workspace inventory state, operational dashboards, log monitors, status files, cross-agent status docs, or stale transport state.

## Required States

Plan and render these states when relevant:

- loading
- empty
- ready
- stale
- polling or refreshing
- closed or disconnected
- error with recovery action
- permission or unavailable state
- poisoned or suspect after transport/session changes
- monitoring active with owner and stop condition
- handoff state with last trusted observation

## Polling Registration

For live views, make the polling contract explicit:

- trigger
- interval or event source
- cancellation condition
- last refreshed timestamp
- stale threshold
- retry and backoff behavior
- watcher or background-process owner
- user-visible status cadence
- trusted and untrusted data sources

Do not leave a spinner as the only indication of progress when the resource can be closed, stale, or unavailable.

## Operational Dashboards And Monitors

For dashboards, terminal monitors, status files, and long-running operational views, record:

- refresh cadence and stale threshold
- last observed timestamp
- source confidence and transport assumptions
- background watcher owner, PID/job ID, or process name when known
- what changed since the last update
- next action after the snapshot
- whether work continued after writing status

Writing a status file or capturing a monitor screenshot is not a stop condition unless the user explicitly asked only for a report.

After VPN, proxy, browser-session, container-network, or transport changes, mark displayed state as suspect until a small sanity check verifies that the UI is reading current data.

## Visual QA

Check state transitions in screenshots or interactive verification:

- spinner does not resize layout
- stale or closed state does not look like success
- action buttons disable or change meaning during transitions
- polling indicators do not obscure primary data
- mobile layout keeps status labels readable
- stale or poisoned state is visibly distinct from fresh success
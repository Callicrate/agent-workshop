# Disposable preview cache

## Decision
Approve a two-day internal preview for three designers. This is not a production launch or a durability claim.

## Mechanism and boundary
A local process reads synthetic, checked-in color tokens and renders thumbnail previews. The in-memory cache avoids rerendering identical tokens within that process. Restarting drops the cache and rebuilds it from the same tokens. The cache is never authoritative; no customer data, network listener, shared storage, or production credentials are involved.

## Acceptance record
The supplied test record reports that 40 of 40 token inputs produce the same thumbnail hash with caching enabled and disabled. Restart followed by regeneration also matched all 40 hashes. This is a supplied record, not independent reviewer execution. No performance measurement or claim is made.

## Constraints and next step
Replication and cache persistence are intentionally excluded. The designer running the preview owns restart and can disable caching with the existing configuration switch. Approval covers only the bounded preview. A production proposal would require a separate design and evidence review.

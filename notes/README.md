# notes/

Durable, monorepo-wide **notes** — caveats, gotchas, and non-obvious decisions
discovered while doing the work, written down so the next person (or the next
session) doesn't have to rediscover them.

## Notes vs. gaps

These are **not gaps**. The distinction:

| | `plans/gaps/` | `notes/` |
|---|---|---|
| **What** | something **missing or unresolved** — a deferred decision, an out-of-scope dependency, an ambiguity to settle | something **resolved or decided** that is worth remembering — a caveat, a gotcha, a deliberate choice and its reason |
| **State** | open until closed | standing reference; updated when reality changes |
| **Lifecycle** | `status: archived` when resolved (node kept so links survive) | kept as long as it's true |

If you're tempted to file a gap but the thing is actually *settled* (you just
want to remember why, or warn about a sharp edge), it's a **note**, not a gap.

## Format

- One file per topic: `notes/<topic>.md`.
- Lead with the date and a one-line summary, then the specific caveats/decisions.
- Convert relative dates to absolute. Link related artifacts (plans, research, ADRs).
- When a note stops being true, update or delete it — stale notes are worse than none.

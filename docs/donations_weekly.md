# Weekly donations v1

Status: `derivation core implemented; production adapter/public projection pending`

## Scope

Phase 1 implements a deterministic pure-Python derivation core for weekly
donation counter evidence. It accepts normalized fictional or future adapter
observations and does not read SQLite, call the API, write files, build public
JSON, or interact with the frontend.

The authoritative future source remains confirmed raw snapshots in the local
snapshot-history SQLite. Weekly values are deterministic derived projections,
not a second authoritative store.

## Week contract

- Timezone: `Europe/Moscow`, loaded through `zoneinfo.ZoneInfo` and an installed
  IANA timezone database.
- Start: Monday 00:00 inclusive.
- End: next Monday 00:00 exclusive.
- Week ID: ISO year/week of the Moscow-local Monday, for example `2026-W34`.
- Timestamps are timezone-aware; naive timestamps fail closed.

The current Windows Python installation has no packaged `tzdata`. The core
therefore uses the already installed Git for Windows IANA database as a
standard-library `ZoneInfo.from_file` fallback. It does not hardcode UTC+3 and
does not install an external dependency.

## Interval and counter semantics

A transition is evidence for interval `(A, B]`, where A and B are consecutive
confirmed observations of one stable private identity in one continuous
membership segment.

- Increase: `after - before` is a confirmed positive increment.
- Unchanged: confirmed contribution is zero.
- Negative: `reset_or_unknown`; contribution is zero and `after` becomes the
  baseline for the next pair.
- Missing endpoint: `unavailable`; contribution is zero. The core never skips
  over the missing observation. A later usable value can be the baseline only
  for its next consecutive pair.

`donations` and `donations_received` use identical temporal rules but are
derived independently. They are not forced to reconcile arithmetically.

## Week-boundary attribution

If A and B belong to the same Moscow week, a positive delta contributes to
that week's confirmed lower bound.

If the interval crosses any Monday 00:00 boundary:

- no positive value is attributed to any week;
- no proportional interpolation is attempted;
- the transition keeps its counter classification;
- every crossed week receives `boundary_ambiguous` evidence;
- intermediate weeks receive no invented contribution.

An interval ending exactly at Monday 00:00 is ambiguous because B belongs to
the new week. An interval from Monday 00:00 to Monday 01:00 is attributable to
the new week.

## Membership and gaps

- First appearance: baseline only.
- Join/rejoin: the first observation in a new explicit membership segment is a
  new baseline.
- Leave/absence: no transition exists.
- Rename: irrelevant to the pure model; stable private identity remains the
  key.
- Segment identifiers may not reappear after a later segment begins.
- Elapsed time greater than two hours sets `gap_affected`.
- A same-week positive delta through a gap remains confirmed and is counted.
- A cross-week gap is excluded by the boundary rule, not by interpolation.

## Result models

`DonationObservation` is the immutable input. `DonationTransition` preserves
classification, positive raw delta evidence, attribution status, affected
weeks, and the gap flag.

`PlayerWeeklyDonations` contains private per-player/week confirmed lower
bounds, transition counts, reset/unavailable/gap/boundary evidence, observation
coverage, and status. Results are ordered by week ascending and stable internal
identity ascending.

`AggregateWeeklyDonations` is calculated only by summing the player/week
results for the same scope. It adds participant, contributor, affected-player,
observation, and transition evidence counts. There is no separate aggregate
algorithm that can drift from player results.

## Completeness

Statuses are `complete`, `partial`, and `insufficient_data`, but Phase 1 does
not invent a threshold for complete historical coverage.

- A current week is always `partial`, including when it has no observations.
- A week with no observed participants is `insufficient_data`.
- A completed week with no usable same-week transition is
  `insufficient_data`.
- Other derivable weeks remain `partial` until an integration phase provides
  evidence and an approved rule for complete coverage.

The values named `donations_confirmed` and
`donations_received_confirmed` mean the sum of positive counter increments
unambiguously attributable to that week. They may be lower bounds and must not
be described as complete totals without sufficient coverage evidence.

## Deferred work

- Production SQLite adapter and production parity validation.
- Current-roster public filtering and versioned allowlist.
- Compact public JSON.
- Builder and updater integration.
- Frontend and public wording.
- Controlled PreviewOnly, natural-run, and Pages validation.

No API request, public output, leadership rule, donation requirement, fuzzy
identity, backfill, or new authoritative storage is part of Phase 1.

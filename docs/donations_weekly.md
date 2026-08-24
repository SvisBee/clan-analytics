# Weekly donations v1

Status: `public projection core implemented; builder/site integration pending`

## Scope

Phase 1 implements a deterministic pure-Python derivation core for weekly
donation counter evidence. Phase 2 adds a read-only production adapter that
converts confirmed snapshot-history rows into that pure input model. Phase 3
adds an in-memory, privacy-safe public projection core for fictional and future
builder inputs. These phases do not write derived data to SQLite or interact
with the frontend. Phase 3 does not create a production public JSON file.

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

Direct `ZoneInfo("Europe/Moscow")` lookup in the current Python executable is
unavailable without that fallback, while the project loader succeeds in the
same executable. Phase 4 must validate this existing fallback in the natural
builder environment when integrating the metric; Phase 3 does not change the
updater or its environment.

## Read-only snapshot adapter

`donations_weekly_adapter.py` validates the existing snapshot store with the
snapshot-history contract, then opens it through SQLite URI `mode=ro`. It reads
only confirmed observation timestamps, payload references, stable private
player tags, and the two donation counters. It does not set journal mode, open
a writable connection, or create derived tables.

Confirmed clan observations are processed chronologically. First appearance
starts a segment; presence in the immediately preceding confirmed observation
continues it; one confirmed absence closes it; and later reappearance starts a
new deterministic segment. Failed collection slots are not invented as
absences. Private tags exist only in memory as the stable identity supplied to
the core and are not included in summaries or audit artifacts.

The adapter does not calculate week IDs, deltas, resets, gaps, boundary
ambiguity, completeness, or totals. Those rules remain exclusively in the pure
derivation core.

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

Production evidence still does not justify an arbitrary numeric completeness
threshold. W31 and W34 are completed but partial lower-bound buckets. W32 and
W33 remain `insufficient_data`; they must not be presented as zero activity.
The current W35 bucket is partial. A future `complete` status requires an
objective uninterrupted-coverage contract, not a chosen percentage.

## Phase 2 production parity

The read-only adapter was validated against 146 confirmed clan observations
and 3,267 emitted member observations. It observed 34 stable internal
identities across 36 membership segments, including two rejoin segment starts.

- `2026-W31`: completed, partial, 28 participants, 2,668 donations and 2,606
  received donations confirmed.
- `2026-W32`: insufficient data.
- `2026-W33`: insufficient data.
- `2026-W34`: completed, partial, 27 participants, 1,928 donations and 1,928
  received donations confirmed.
- `2026-W35`: current, partial, 18 participants, 30 donations and 30 received
  donations confirmed at the Phase 2 parity cutoff.

The historical prefix also reproduces the discovery transition taxonomy and
raw positive evidence exactly. The production SQLite hash and logical row
counts were unchanged before and after validation.

Parity validation exposed and fixed one pure-core ordering defect: when no
explicit coverage start was supplied, the default now uses the globally
earliest observation timestamp instead of the first identity-sorted row.

## Phase 3 public schema v1

`donations_weekly_public.py` builds and validates a JSON-ready structure in
memory. Its top-level allowlist is `schema_version`, `timezone`, `scope`,
`metric_semantics`, `generated_at_utc`, `latest_observed_at_utc`, and `weeks`.
The scope is `current_roster`; metric semantics are
`confirmed_lower_bound`.

The projection publishes at most two buckets: the current Moscow week and the
most recent usable completed week. A completed `insufficient_data` bucket is
skipped. If no usable completed bucket has evidence for a current member, only
the current week is returned. Current status is always `partial`; completed
status is preserved from the derivation core, without a new completeness
threshold.

Each public week contains only current-roster player rows with derived evidence
for that week. Departed identities and their contributions are excluded.
Current members without historical evidence are not given invented zero rows.
Week totals, participant count, contributor count, and reset/gap/boundary flags
are recalculated only from the published rows, so public totals cannot drift
from the visible current-roster scope.

The private stable identity is used only for the in-memory join. Public rows
contain the current roster `nickname` plus the two confirmed counters and three
evidence flags. Rename history therefore follows the stable private identity
but displays the current name. Nickname is never an identity key; duplicate
nicknames remain separate rows. Tags, hashes, internal IDs, segment IDs,
payload/observation IDs, fingerprints, and source-run IDs are forbidden.

Leaderboard ordering is deterministic: donated confirmed descending, received
confirmed descending, current nickname case-insensitively, then private stable
identity as an in-memory final tie-breaker. The final tie-breaker is never
serialized. The strict schema validator rejects unknown fields, invalid types,
negative counters, inconsistent totals, invalid selection, and privacy scanner
violations.

## Deferred work

- Compact public JSON.
- Builder and updater integration.
- Frontend and public wording.
- Controlled PreviewOnly, natural-run, and Pages validation.

Phase 4 will obtain current private identities from the latest confirmed
snapshot and call the Phase 3 projection at the existing roster normalization
boundary before player tags are removed. Production JSON, builder wiring,
timezone fallback validation in the natural runtime, and controlled preview are
explicitly deferred.

No API request, public output, leadership rule, donation requirement, fuzzy
identity, backfill, or new authoritative storage is part of Phase 1 through
Phase 3.

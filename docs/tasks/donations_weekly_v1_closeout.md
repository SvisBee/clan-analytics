# donations_weekly_v1 closeout

Status: `completed`

Superseded public semantics: this closeout records the original schema-v1
confirmed-delta implementation. The production public metric has been replaced
by schema v2 `game_counter_snapshot`, which publishes direct raw counters for
the current roster and the last observed raw counters from the immediately
previous Moscow week. The pure delta core is retained only as an internal
historical/audit utility.

## Public semantics correction closeout

Schema v2 is now the validated production contract. Natural run
`20260825-170003-26a1d230` first published it in data commit
`872ec9db2c5d1700e8d273192d275b9f9b1c848e`; no extra API request was added.
The 26 August follow-up reconciled all 42 current-roster rows against the latest
confirmed raw counters with mismatch, missing and extra counts all equal to
zero. The previous-week projection matched all 16 identities with available
evidence and remained explicitly partial for 26 missing identities.

No reset cluster was observed at the well-covered 24 August Moscow boundary.
Earlier boundaries lack close observations, so actual game-reset timing remains
an observational follow-up and does not change the direct-counter semantics.
Consecutive no-change natural runs also proved byte stability. The original v1
material below remains the historical record and is not the current public
metric definition.

## Scope

`donations_weekly_v1` derives conservative weekly donation evidence from the
existing confirmed clan snapshot history, publishes a current-roster-only
projection and renders it on the public site. It does not create a second
authoritative store, backfill missing history, assign requirements or automate
leadership decisions.

## Architecture

- `data/clan_snapshot_history/clan_snapshot_history.v1.sqlite3` remains the sole
  authoritative source for confirmed historical counters and stays outside Git,
  Pages and Codebase Memory.
- The production adapter validates the store and opens it with SQLite URI
  `mode=ro`.
- A pure deterministic core derives transitions and weekly confirmed lower
  bounds without storage or public-output concerns.
- The public projection joins stable private identity to the exact current
  roster only in memory, excludes departed members and recalculates all totals
  from visible rows.
- The builder emits allowlist-only `site/data/donations-weekly.json` together
  with the other five approved public JSON files.
- The frontend reads only that public JSON. It performs no identity join,
  counter delta, week attribution or departed-member filtering.

## Temporal semantics

- Calendar weeks use `Europe/Moscow`, Monday 00:00 inclusive through the next
  Monday 00:00 exclusive.
- A consecutive observation pair is evidence for interval `(A, B]`.
- Positive deltas that cross a week boundary are excluded instead of
  interpolated and are marked as boundary-ambiguous evidence.
- Counter decreases are `reset_or_unknown`, contribute zero and establish the
  next baseline.
- Gaps are retained as evidence. Same-week positive deltas remain confirmed;
  cross-week deltas remain excluded by the boundary rule.
- Join and rejoin begin a new membership segment, so counters are never compared
  across a confirmed absence.
- Published values are confirmed lower bounds, not claims of complete weekly
  totals.

## Production validation

Phase 2 read-only parity covered 146 confirmed observations and reproduced the
documented historical lower bounds, including `2026-W31` and `2026-W34`, before
any public integration. Phase 4 then published schema v1 through controlled run
`20260824-142333-853dc0f2` and Pages workflow `32721733331`. Phase 5 published
the independent frontend through commit
`31417c0cb7a179863d0766cbb6d5b1b531401689` and successful Pages workflow
`32725053172`.

The first automatic normal run after the Phase 5 frontend and documentation was
`20260824-160003-5206424c`, correlated with the 2026-08-24 16:00 Moscow hourly
slot. It started at 16:00:03, finished at 16:01:13, completed every stage in
order and reported `process_exit_code=0`, `LastTaskResult=0` and
`no_public_change`.

The weekly builder reported two selected weeks and 36 public rows. Public
semantics were unchanged, and the weekly file retained Git blob
`694ecaf46a91d272281783bec11735e32dee6d92` and SHA-256
`b9d11aa7bb3930f42e869053df643cfbc36f413969d94351738ef725274a3280`.
No data commit or push was required. The normal snapshot stage recorded exactly
one confirmed observation for the run, with a newly inserted payload, after
tests and before atomic apply.

Safe selected-week aggregates at closeout:

| Week | Selection | Status | Participants | Contributors | Donated | Received | Reset | Gap | Boundary | Warnings |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: |
| `2026-W35` | current | partial | 18 | 2 | 30 | 30 | no | yes | yes | 2 |
| `2026-W34` | previous usable | partial | 18 | 16 | 1897 | 1908 | yes | yes | yes | 3 |

Every week total equals the sum of its published rows. The current selection
matches the Moscow week containing the natural run. The previous selection is
the latest usable completed week; insufficient-data weeks are not published as
zero activity.

All six public JSON files parsed and passed their contracts and recursive
privacy scan. Pages returned HTTP 200 and matched repository bytes for the root,
weekly JSON, HTML-linked JavaScript and CSS. Browser checks passed on desktop,
360 px and 320 px: current was selected by default, previous switched
client-side, summaries and warning counts matched JSON, the leaderboard and
existing sections rendered, and console warnings/errors were zero.

Focused validation passed 268 Python tests. The full suite passed 374 tests.
Both frontend Node contracts passed 12 tests; all three frontend JavaScript
files, 46 tracked Python files and 16 PowerShell scripts passed syntax checks.
SQLite integrity and foreign-key checks passed before and after tests, and its
hash and aggregate row counts remained unchanged by offline validation.

## Privacy

- Stable tags exist only in the local SQLite and private in-memory join.
- Public rows contain only current nicknames already approved by the roster
  contract and aggregate evidence fields.
- Tags, tag hashes, internal IDs, segment/payload/observation IDs,
  fingerprints and source-run IDs are absent from public weekly JSON.
- Departed identities and their contributions are excluded from public rows and
  totals.
- Token, Authorization, DPAPI data, private paths and raw payloads are absent
  from Git and Pages outputs.

## Definition of Done

- [x] Pure derivation core implemented.
- [x] Monday and Moscow timezone semantics tested.
- [x] Cross-week ambiguity handled conservatively.
- [x] Reset semantics conservative.
- [x] Membership segments correct.
- [x] Read-only production adapter validated.
- [x] W31 and W34 historical parity validated.
- [x] Public projection privacy validated.
- [x] Current-roster scope validated.
- [x] Departed contributions excluded.
- [x] Public totals equal visible rows.
- [x] Builder integration production validated.
- [x] Six-file publication contract valid.
- [x] No-change byte stability validated.
- [x] Public JSON published.
- [x] Pages JSON validated.
- [x] Frontend production validated.
- [x] Mobile and desktop validated.
- [x] Graceful error state tested.
- [x] Natural hourly run after frontend deployment validated.
- [x] Snapshot observation created normally.
- [x] No additional API requests added by weekly derivation.
- [x] SQLite remains sole authority.
- [x] Privacy passed.
- [x] Full tests passed.
- [x] Git clean and synchronized at validation baseline.
- [x] Scheduled Task healthy.
- [x] Documentation current.

## Deferred work

- Historical backfill and departed-player public history.
- Retention or compaction changes to the authoritative snapshot store.
- Any objective future completeness rule.
- Predictions, donation requirements, alerts, punishments and other leadership
  automation.

The next roadmap entry is `clan_games_v1 source research`. This closeout records
that ordering only; no work on the next stage was started.

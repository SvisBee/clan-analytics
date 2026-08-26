const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..", "..");
const source = fs.readFileSync(
  path.join(root, "site/assets/js/donations-weekly-contract.js"), "utf8"
);
const context = { globalThis: {}, Intl, Date, Error, Set, Object };
vm.runInNewContext(source, context, { filename: "donations-weekly-contract.js" });
const contract = context.globalThis.ClanAnalyticsDonationsWeeklyContract;

const player = (nickname, donations, received) => ({
  nickname, donations, donations_received: received
});
const coverage = (overrides = {}) => ({
  stale_end_snapshot: false,
  stale_player_count: 0,
  missing_player_count: 0,
  insufficient_data: false,
  reset_observed: false,
  ...overrides
});
const week = (overrides = {}) => ({
  week_id: "2026-W35",
  week_start: "2026-08-24T00:00:00+03:00",
  week_end: "2026-08-31T00:00:00+03:00",
  selection: "current",
  status: "current",
  snapshot_at_utc: null,
  donations: 30,
  donations_received: 30,
  participant_count: 3,
  contributing_player_count: 2,
  coverage: coverage(),
  players: [player("Один", 20, 10), player("Один", 10, 20), player("Ноль", 0, 0)],
  ...overrides
});
const payload = (weeks = [
  week(),
  week({
    week_id: "2026-W34",
    week_start: "2026-08-17T00:00:00+03:00",
    week_end: "2026-08-24T00:00:00+03:00",
    selection: "previous",
    status: "recorded",
    snapshot_at_utc: "2026-08-23T20:00:00Z",
    donations: 45,
    donations_received: 40,
    participant_count: 2,
    contributing_player_count: 2,
    players: [player("Первый", 25, 20), player("Второй", 20, 20)]
  })
]) => ({
  schema_version: 2,
  timezone: "Europe/Moscow",
  scope: "current_roster",
  metric_semantics: "game_counter_snapshot",
  weeks
});

test("schema v2 is accepted and current is the default selection", () => {
  const value = payload();
  assert.equal(contract.validateWeeklyPayload(value), value);
  assert.equal(contract.selectWeek(value).selection, "current");
  assert.equal(contract.selectWeek(value, "previous").week_id, "2026-W34");
});

test("current-only payload is accepted and has no previous selection", () => {
  const value = payload([week()]);
  assert.equal(contract.selectWeek(value).week_id, "2026-W35");
  assert.equal(contract.selectWeek(value, "previous"), null);
});

test("malformed payloads fail closed", () => {
  assert.throws(() => contract.validateWeeklyPayload({ schema_version: 3, weeks: [] }));
  assert.throws(() => contract.validateWeeklyPayload(payload([week({ donations: -1 })])));
  assert.throws(() => contract.validateWeeklyPayload(payload([week({ players: {} })])));
  assert.throws(() => contract.validateWeeklyPayload(payload([week({ week_end: "invalid" })])));
});

test("raw-counter wording and previous date range are explicit", () => {
  const current = contract.weekPresentation(week());
  const previous = payload().weeks[1];
  assert.equal(current.title, "Текущие показатели в игре");
  assert.match(current.explanation, /последние значения счётчиков/);
  const previousPresentation = contract.weekPresentation(previous);
  assert.equal(previousPresentation.title, "Последний снимок предыдущей календарной недели");
  assert.match(previousPresentation.explanation, /предыдущую неделю по московскому времени/);
  assert.match(previousPresentation.explanation, /Игровой момент сброса пока не подтверждён/);
  assert.doesNotMatch(previousPresentation.title + previousPresentation.explanation, /итог|игровая неделя|игровой период/i);
  assert.equal(contract.formatWeekRange(previous), "17–23 августа");
});

test("only objective freshness and missing-evidence warnings are rendered", () => {
  const stale = week({
    selection: "previous",
    status: "partial",
    snapshot_at_utc: "2026-08-23T18:00:00Z",
    coverage: coverage({ stale_end_snapshot: true, stale_player_count: 1 })
  });
  const presentation = contract.weekPresentation(stale);
  assert.match(presentation.explanation, /Игровой момент сброса пока не подтверждён/);
  assert.equal(presentation.warnings.length, 1);
  assert.match(presentation.warnings[0], /раньше конца календарной недели/);
  assert.doesNotMatch(presentation.warnings.join(" "), /reset|boundary|дельт|минимум/i);
});

test("server ordering, duplicate nicknames and zero rows remain untouched", () => {
  const selected = contract.selectWeek(payload());
  assert.deepEqual(selected.players.map((item) => item.nickname), ["Один", "Один", "Ноль"]);
  assert.equal(selected.players[2].donations, 0);
  assert.equal(selected.donations, 30);
  assert.equal(selected.donations_received, 30);
  assert.equal(selected.participant_count, 3);
  assert.equal(selected.contributing_player_count, 2);
});

test("schema v1 is normalized only for bounded rollout compatibility", () => {
  const legacy = {
    schema_version: 1,
    timezone: "Europe/Moscow",
    scope: "current_roster",
    metric_semantics: "confirmed_lower_bound",
    weeks: [{
      week_id: "2026-W35",
      week_start: "2026-08-24T00:00:00+03:00",
      week_end: "2026-08-31T00:00:00+03:00",
      selection: "current",
      status: "partial",
      donations_confirmed: 4,
      donations_received_confirmed: 3,
      participant_count: 1,
      contributing_player_count: 1,
      players: [{ nickname: "Игрок", donations_confirmed: 4, donations_received_confirmed: 3 }]
    }]
  };
  const normalized = contract.validateWeeklyPayload(legacy);
  assert.equal(normalized.legacy_schema, true);
  assert.equal(normalized.weeks[0].donations, 4);
  assert.match(contract.weekPresentation(normalized.weeks[0], { legacySchema: true }).explanation, /schema v2/);
});

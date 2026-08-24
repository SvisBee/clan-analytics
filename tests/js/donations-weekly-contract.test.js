const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..", "..");
const source = fs.readFileSync(
  path.join(root, "site/assets/js/donations-weekly-contract.js"),
  "utf8"
);
const context = { globalThis: {}, Intl, Date, Error, Set, Object };
vm.runInNewContext(source, context, { filename: "donations-weekly-contract.js" });
const contract = context.globalThis.ClanAnalyticsDonationsWeeklyContract;

const player = (nickname, donated, received) => ({
  nickname,
  donations_confirmed: donated,
  donations_received_confirmed: received,
  reset_affected: false,
  gap_affected: false,
  boundary_ambiguous: false
});

const week = (overrides = {}) => ({
  week_id: "2026-W35",
  week_start: "2026-08-24T00:00:00+03:00",
  week_end: "2026-08-31T00:00:00+03:00",
  selection: "current",
  status: "partial",
  is_current: true,
  donations_confirmed: 30,
  donations_received_confirmed: 30,
  participant_count: 3,
  contributing_player_count: 2,
  reset_affected: false,
  gap_affected: true,
  boundary_ambiguous: true,
  players: [player("Один", 20, 10), player("Один", 10, 20), player("Ноль", 0, 0)],
  ...overrides
});

const payload = (weeks = [
  week(),
  week({
    week_id: "2026-W34",
    week_start: "2026-08-17T00:00:00+03:00",
    week_end: "2026-08-24T00:00:00+03:00",
    selection: "previous_usable",
    is_current: false,
    donations_confirmed: 45,
    donations_received_confirmed: 40,
    participant_count: 2,
    contributing_player_count: 2,
    reset_affected: true,
    players: [player("Первый", 25, 20), player("Второй", 20, 20)]
  })
]) => ({
  schema_version: 1,
  timezone: "Europe/Moscow",
  scope: "current_roster",
  metric_semantics: "confirmed_lower_bound",
  generated_at_utc: "2026-08-24T11:23:36Z",
  latest_observed_at_utc: "2026-08-24T11:23:36Z",
  weeks
});

test("schema v1 is accepted and current is the default selection", () => {
  const value = payload();
  assert.equal(contract.validateWeeklyPayload(value), value);
  assert.equal(contract.selectWeek(value).selection, "current");
  assert.equal(contract.selectWeek(value, "previous_usable").week_id, "2026-W34");
});

test("current-only payload is accepted and has no previous selection", () => {
  const value = payload([week()]);
  assert.equal(contract.selectWeek(value).week_id, "2026-W35");
  assert.equal(contract.selectWeek(value, "previous_usable"), null);
});

test("malformed payloads fail closed", () => {
  assert.throws(() => contract.validateWeeklyPayload({ schema_version: 2, weeks: [] }));
  assert.throws(() => contract.validateWeeklyPayload(payload([week({ donations_confirmed: -1 })])));
  assert.throws(() => contract.validateWeeklyPayload(payload([week({ players: {} })])));
  assert.throws(() => contract.validateWeeklyPayload(payload([week({ week_end: "invalid" })])));
});

test("week labels and exclusive end boundary format for Russian readers", () => {
  const current = week();
  const previous = payload().weeks[1];
  assert.equal(contract.weekPresentation(current).title, "Текущая неделя");
  assert.equal(contract.weekPresentation(previous).title, "Предыдущая доступная неделя");
  assert.equal(contract.formatWeekRange(previous), "17–23 августа");
});

test("partial, current and insufficient states use lower-bound wording", () => {
  const current = contract.weekPresentation(week());
  assert.equal(current.badge, "Подтверждённый минимум");
  assert.match(current.explanation, /Неделя ещё идёт/);

  const previous = contract.weekPresentation(payload().weeks[1]);
  assert.equal(previous.badge, "Неполные данные");
  assert.match(previous.explanation, /подтверждённый минимум/);

  const insufficient = contract.weekPresentation(week({ status: "insufficient_data" }));
  assert.equal(insufficient.badge, "Недостаточно данных");
  assert.match(insufficient.explanation, /Недостаточно подтверждённых данных/);
});

test("warnings are emitted only for true evidence flags", () => {
  const warnings = contract.weekPresentation(week()).warnings;
  assert.equal(warnings.length, 2);
  assert.match(warnings[0], /разрывы/);
  assert.match(warnings[1], /границу недели/);
  assert.equal(
    contract.weekPresentation(week({ gap_affected: false, boundary_ambiguous: false })).warnings.length,
    0
  );
});

test("server ordering, duplicate nicknames and zero rows remain untouched", () => {
  const selected = contract.selectWeek(payload());
  assert.deepEqual(selected.players.map((item) => item.nickname), ["Один", "Один", "Ноль"]);
  assert.equal(selected.players[2].donations_confirmed, 0);
  assert.equal(selected.donations_confirmed, 30);
  assert.equal(selected.donations_received_confirmed, 30);
  assert.equal(selected.participant_count, 3);
  assert.equal(selected.contributing_player_count, 2);
});

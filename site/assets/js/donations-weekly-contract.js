/* Shared, dependency-free public weekly-donations display contract. */
(() => {
  const fail = (message) => {
    throw new Error(`Weekly donations data is invalid: ${message}`);
  };
  const isRecord = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const requireString = (value, field) => {
    if (typeof value !== "string" || !value.trim()) fail(`${field} must be a string`);
  };
  const requireCounter = (value, field) => {
    if (!Number.isInteger(value) || value < 0) fail(`${field} must be a non-negative integer`);
  };
  const requireBoolean = (value, field) => {
    if (typeof value !== "boolean") fail(`${field} must be a boolean`);
  };

  const validatePlayerV2 = (player) => {
    if (!isRecord(player)) fail("player must be an object");
    requireString(player.nickname, "nickname");
    requireCounter(player.donations, "player donations");
    requireCounter(player.donations_received, "player donations_received");
  };

  const validateWeekV2 = (week) => {
    if (!isRecord(week)) fail("week must be an object");
    requireString(week.week_id, "week_id");
    requireString(week.week_start, "week_start");
    requireString(week.week_end, "week_end");
    if (!["current", "previous"].includes(week.selection)) fail("selection is unsupported");
    if (!["current", "recorded", "partial"].includes(week.status)) fail("status is unsupported");
    if (week.snapshot_at_utc !== null) {
      requireString(week.snapshot_at_utc, "snapshot_at_utc");
      if (Number.isNaN(new Date(week.snapshot_at_utc).getTime())) fail("snapshot_at_utc is invalid");
    }
    for (const field of ["donations", "donations_received", "participant_count", "contributing_player_count"]) {
      requireCounter(week[field], field);
    }
    if (!isRecord(week.coverage)) fail("coverage must be an object");
    for (const field of ["stale_end_snapshot", "insufficient_data", "reset_observed"]) {
      requireBoolean(week.coverage[field], `coverage ${field}`);
    }
    requireCounter(week.coverage.stale_player_count, "coverage stale_player_count");
    requireCounter(week.coverage.missing_player_count, "coverage missing_player_count");
    if (!Array.isArray(week.players)) fail("players must be an array");
    week.players.forEach(validatePlayerV2);
    const start = new Date(week.week_start);
    const end = new Date(week.week_end);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) {
      fail("week boundaries are invalid");
    }
  };

  const normalizeLegacyV1 = (payload) => {
    if (!Array.isArray(payload.weeks)) fail("legacy weeks must be an array");
    return {
      schema_version: 2,
      timezone: payload.timezone,
      scope: payload.scope,
      metric_semantics: "legacy_confirmed_delta_rollout",
      legacy_schema: true,
      weeks: payload.weeks.slice(0, 2).map((week) => ({
        week_id: week.week_id,
        week_start: week.week_start,
        week_end: week.week_end,
        selection: week.selection === "previous_usable" ? "previous" : "current",
        status: week.selection === "current" ? "current" : "partial",
        snapshot_at_utc: null,
        donations: week.donations_confirmed,
        donations_received: week.donations_received_confirmed,
        participant_count: week.participant_count,
        contributing_player_count: week.contributing_player_count,
        coverage: {
          stale_end_snapshot: false,
          stale_player_count: 0,
          missing_player_count: 0,
          insufficient_data: true,
          reset_observed: false
        },
        players: Array.isArray(week.players) ? week.players.map((player) => ({
          nickname: player.nickname,
          donations: player.donations_confirmed,
          donations_received: player.donations_received_confirmed
        })) : []
      }))
    };
  };

  const validateWeeklyPayload = (payload) => {
    if (!isRecord(payload)) fail("payload must be an object");
    let normalized = payload;
    if (payload.schema_version === 1) normalized = normalizeLegacyV1(payload);
    if (normalized.schema_version !== 2) fail("schema_version must equal 2");
    requireString(normalized.timezone, "timezone");
    requireString(normalized.scope, "scope");
    requireString(normalized.metric_semantics, "metric_semantics");
    if (!Array.isArray(normalized.weeks) || normalized.weeks.length < 1 || normalized.weeks.length > 2) {
      fail("weeks must contain one or two entries");
    }
    normalized.weeks.forEach(validateWeekV2);
    const selections = normalized.weeks.map((week) => week.selection);
    if (new Set(selections).size !== selections.length) fail("week selections must be unique");
    if (!selections.includes("current")) fail("current week is required");
    return normalized;
  };

  const selectWeek = (payload, selection = "current") => {
    const normalized = validateWeeklyPayload(payload);
    return normalized.weeks.find((week) => week.selection === selection) || null;
  };
  const dateParts = (date, timezone) => {
    const parts = new Intl.DateTimeFormat("ru-RU", {
      day: "numeric", month: "long", year: "numeric", timeZone: timezone
    }).formatToParts(date);
    return Object.fromEntries(parts.map((part) => [part.type, part.value]));
  };
  const formatWeekRange = (week, timezone = "Europe/Moscow") => {
    validateWeekV2(week);
    const start = dateParts(new Date(week.week_start), timezone);
    const inclusiveEnd = dateParts(new Date(new Date(week.week_end).getTime() - 1), timezone);
    if (start.year === inclusiveEnd.year && start.month === inclusiveEnd.month) {
      return `${start.day}–${inclusiveEnd.day} ${inclusiveEnd.month}`;
    }
    if (start.year === inclusiveEnd.year) {
      return `${start.day} ${start.month} – ${inclusiveEnd.day} ${inclusiveEnd.month}`;
    }
    return `${start.day} ${start.month} ${start.year} – ${inclusiveEnd.day} ${inclusiveEnd.month} ${inclusiveEnd.year}`;
  };

  const weekPresentation = (week, { legacySchema = false } = {}) => {
    validateWeekV2(week);
    const current = week.selection === "current";
    let title = current ? "Текущие показатели в игре" : "Предыдущая неделя";
    let badge = current ? "Текущие счётчики" : "Зафиксировано";
    let explanation = current
      ? "Показываются последние значения счётчиков пожертвований из данных клана."
      : "Последний зафиксированный итог перед началом новой недели.";
    const warnings = [];
    if (legacySchema) {
      badge = "Обновление данных";
      explanation = "Временно показана ранее опубликованная версия данных до первого обновления schema v2.";
      warnings.push("Ожидается публикация прямых игровых счётчиков.");
    } else {
      if (week.coverage.stale_end_snapshot) {
        badge = "Последний доступный снимок";
        explanation = "Последний доступный снимок был сделан раньше конца недели.";
        warnings.push("Итог может не включать активность после последнего доступного снимка.");
      }
      if (week.coverage.insufficient_data) {
        warnings.push("Для части текущего состава нет наблюдения за выбранную неделю.");
      }
    }
    return { title, badge, explanation, warnings };
  };

  globalThis.ClanAnalyticsDonationsWeeklyContract = {
    formatWeekRange,
    selectWeek,
    validateWeeklyPayload,
    weekPresentation
  };
})();

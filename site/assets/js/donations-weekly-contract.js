/* Shared, dependency-free public weekly-donations display contract. */
(() => {
  const allowedSelections = new Set(["current", "previous_usable"]);
  const allowedStatuses = new Set(["complete", "partial", "insufficient_data"]);

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

  const validatePlayer = (player) => {
    if (!isRecord(player)) fail("player must be an object");
    requireString(player.nickname, "nickname");
    requireCounter(player.donations_confirmed, "player donations_confirmed");
    requireCounter(player.donations_received_confirmed, "player donations_received_confirmed");
    requireBoolean(player.reset_affected, "player reset_affected");
    requireBoolean(player.gap_affected, "player gap_affected");
    requireBoolean(player.boundary_ambiguous, "player boundary_ambiguous");
  };

  const validateWeek = (week) => {
    if (!isRecord(week)) fail("week must be an object");
    requireString(week.week_id, "week_id");
    requireString(week.week_start, "week_start");
    requireString(week.week_end, "week_end");
    requireString(week.selection, "selection");
    requireString(week.status, "status");
    if (!allowedSelections.has(week.selection)) fail("selection is unsupported");
    if (!allowedStatuses.has(week.status)) fail("status is unsupported");
    requireBoolean(week.is_current, "is_current");
    requireCounter(week.donations_confirmed, "donations_confirmed");
    requireCounter(week.donations_received_confirmed, "donations_received_confirmed");
    requireCounter(week.participant_count, "participant_count");
    requireCounter(week.contributing_player_count, "contributing_player_count");
    requireBoolean(week.reset_affected, "reset_affected");
    requireBoolean(week.gap_affected, "gap_affected");
    requireBoolean(week.boundary_ambiguous, "boundary_ambiguous");
    if (!Array.isArray(week.players)) fail("players must be an array");
    week.players.forEach(validatePlayer);
    const start = new Date(week.week_start);
    const end = new Date(week.week_end);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) {
      fail("week boundaries are invalid");
    }
  };

  const validateWeeklyPayload = (payload) => {
    if (!isRecord(payload)) fail("payload must be an object");
    if (payload.schema_version !== 1) fail("schema_version must equal 1");
    requireString(payload.timezone, "timezone");
    requireString(payload.scope, "scope");
    requireString(payload.metric_semantics, "metric_semantics");
    requireString(payload.latest_observed_at_utc, "latest_observed_at_utc");
    if (Number.isNaN(new Date(payload.latest_observed_at_utc).getTime())) {
      fail("latest_observed_at_utc is invalid");
    }
    if (!Array.isArray(payload.weeks) || payload.weeks.length < 1) {
      fail("weeks must be a non-empty array");
    }
    payload.weeks.forEach(validateWeek);
    const selections = payload.weeks.map((week) => week.selection);
    if (new Set(selections).size !== selections.length) fail("week selections must be unique");
    if (!selections.includes("current")) fail("current week is required");
    return payload;
  };

  const selectWeek = (payload, selection = "current") => {
    validateWeeklyPayload(payload);
    return payload.weeks.find((week) => week.selection === selection) || null;
  };

  const dateParts = (date, timezone) => {
    const parts = new Intl.DateTimeFormat("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
      timeZone: timezone
    }).formatToParts(date);
    return Object.fromEntries(parts.map((part) => [part.type, part.value]));
  };

  const formatWeekRange = (week, timezone = "Europe/Moscow") => {
    validateWeek(week);
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

  const weekPresentation = (week) => {
    validateWeek(week);
    const title = week.selection === "current"
      ? "Текущая неделя"
      : "Предыдущая доступная неделя";

    let badge = "Данные собраны";
    let explanation = "Доступные данные относятся к завершённой неделе.";
    if (week.status === "insufficient_data") {
      badge = "Недостаточно данных";
      explanation = "Недостаточно подтверждённых данных для надёжного вывода.";
    } else if (week.selection === "current") {
      badge = "Подтверждённый минимум";
      explanation = "Неделя ещё идёт, поэтому итог может увеличиться.";
    } else if (week.status === "partial") {
      badge = "Неполные данные";
      explanation = "В сборе есть пробелы или неоднозначные интервалы. Показан только подтверждённый минимум.";
    }

    const warnings = [];
    if (week.gap_affected) warnings.push("В течение недели были разрывы в сборе данных.");
    if (week.reset_affected) warnings.push("Часть счётчиков сбрасывалась или изменилась неоднозначно.");
    if (week.boundary_ambiguous) {
      warnings.push("Некоторые изменения пересекли границу недели и не вошли в подтверждённую сумму.");
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

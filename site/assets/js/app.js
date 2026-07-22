document.documentElement.classList.add("js-ready");

const menuButton = document.querySelector(".menu-toggle");
const navigation = document.querySelector("#site-navigation");

if (menuButton && navigation) {
  const closeMenu = () => {
    navigation.classList.remove("is-open");
    menuButton.setAttribute("aria-expanded", "false");
  };

  menuButton.addEventListener("click", () => {
    const isOpen = navigation.classList.toggle("is-open");
    menuButton.setAttribute("aria-expanded", String(isOpen));
  });

  navigation.addEventListener("click", (event) => {
    if (event.target instanceof HTMLAnchorElement) {
      closeMenu();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && navigation.classList.contains("is-open")) {
      closeMenu();
      menuButton.focus();
    }
  });

  const wideViewport = window.matchMedia("(min-width: 62.01rem)");
  wideViewport.addEventListener("change", (event) => {
    if (event.matches) {
      closeMenu();
    }
  });
}

const navigationLinks = Array.from(
  document.querySelectorAll('.site-nav a[href^="#"]')
);

const setActiveNavigationLink = (activeId) => {
  navigationLinks.forEach((link) => {
    const isActive = link.getAttribute("href") === `#${activeId}`;
    if (isActive) {
      link.setAttribute("aria-current", "location");
    } else {
      link.removeAttribute("aria-current");
    }
  });
};

navigationLinks.forEach((link) => {
  link.addEventListener("click", () => {
    setActiveNavigationLink(link.getAttribute("href").slice(1));
  });
});

if ("IntersectionObserver" in window) {
  const sections = navigationLinks
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);

  const sectionObserver = new IntersectionObserver(
    (entries) => {
      const visibleSection = entries.find((entry) => entry.isIntersecting);
      if (visibleSection) {
        setActiveNavigationLink(visibleSection.target.id);
      }
    },
    { rootMargin: "-30% 0px -60% 0px" }
  );

  sections.forEach((section) => sectionObserver.observe(section));
}

const roleLabels = {
  leader: "Глава",
  coLeader: "Соруководитель",
  admin: "Старейшина",
  member: "Участник"
};

const roleOrder = {
  leader: 0,
  coLeader: 1,
  admin: 2,
  member: 3
};

const createElement = (tag, className = "", content) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = String(content);
  return element;
};

const safeText = (value, fallback = "Нет данных") =>
  value === null || value === undefined || value === "" ? fallback : String(value);

const setTextAll = (selector, value) => {
  document.querySelectorAll(selector).forEach((element) => {
    element.textContent = String(value);
  });
};

const requireElement = (selector) => {
  const element = document.querySelector(selector);
  if (!element) {
    throw new Error(`В HTML отсутствует обязательный элемент ${selector}`);
  }
  return element;
};

const calculateMedian = (values) => {
  const sorted = [...values].sort((left, right) => left - right);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
};

const applyClanBadge = (badgeUrl) => {
  if (!badgeUrl) return;

  document.querySelectorAll("[data-clan-badge]").forEach((image) => {
    const parent = image.parentElement;
    const fallback = parent.querySelector("[data-badge-fallback]");

    image.addEventListener("load", () => {
      image.hidden = false;
      if (fallback) fallback.hidden = true;
    }, { once: true });

    image.addEventListener("error", () => {
      image.hidden = true;
      if (fallback) fallback.hidden = false;
    }, { once: true });

    image.referrerPolicy = "no-referrer";
    image.src = badgeUrl;
  });
};

const createPlayerCard = (member, index) => {
  const russianCount = (value, forms) => {
    const remainder100 = Math.abs(value) % 100;
    const remainder10 = remainder100 % 10;
    if (remainder100 >= 11 && remainder100 <= 14) return forms[2];
    if (remainder10 === 1) return forms[0];
    if (remainder10 >= 2 && remainder10 <= 4) return forms[1];
    return forms[2];
  };
  const hasWarData = member.data_status === "available";
  const card = createElement(
    "article",
    `player-card${hasWarData ? "" : " player-card--no-war"}`
  );
  card.dataset.name = safeText(member.nickname, "").toLocaleLowerCase("ru");
  card.dataset.role = member.clan_role || "";
  card.dataset.th = member.town_hall_level ?? "";

  const header = createElement("div", "player-card__header");
  const avatar = createElement(
    "div",
    "player-avatar",
    String(index + 1).padStart(2, "0")
  );

  const identity = document.createElement("div");
  identity.append(
    createElement(
      "p",
      "player-card__eyebrow",
      roleLabels[member.clan_role] || safeText(member.clan_role, "Роль не указана")
    ),
    createElement("h3", "", safeText(member.nickname, "Без имени"))
  );

  const townHall = createElement("span", "town-hall");
  townHall.append(
    createElement("span", "", "TH"),
    document.createTextNode(safeText(member.town_hall_level, "?"))
  );

  header.append(avatar, identity, townHall);
  card.append(header);

  if (hasWarData) {
    const metrics = createElement("dl", "player-metrics player-metrics--history");
    const values = [
      ["Войны", member.war_participations ?? "–"],
      ["Атаки", member.attacks_available === null ? "–" : `${member.attacks_used} / ${member.attacks_available}`],
      ["Звёзды в атаках", member.stars_earned ?? "–"],
      ["Среднее", member.average_stars ?? "–"]
    ];
    values.forEach(([label, value]) => {
      const wrapper = document.createElement("div");
      wrapper.append(createElement("dt", "", label), createElement("dd", "", value));
      metrics.append(wrapper);
    });
    card.append(metrics);
  }

  const status = createElement("div", "player-card__status");
  status.append(
    createElement(
      "span",
      hasWarData ? "neutral-indicator" : "neutral-indicator neutral-indicator--limited",
      hasWarData ? "Военные данные доступны" : "Накопленная история войн пока отсутствует"
    )
  );
  if ((member.manual_war_participations || 0) > 0 || (member.manual_attacks_used || 0) > 0) {
    const wars = member.manual_war_participations || 0;
    const attacks = member.manual_attacks_used || 0;
    status.append(createElement(
      "small",
      "player-card__manual-note",
      `Учтены восстановленные данные: ${wars} ${russianCount(wars, ["война", "войны", "войн"])}, ${attacks} ${russianCount(attacks, ["атака", "атаки", "атак"])}`
    ));
  }
  if (member.manual_conflict_sources === true) {
    status.append(createElement("small", "", "Часть показателей имеет конфликт источников"));
  }
  card.append(status);
  return card;
};

const renderDistribution = (distribution, totalMembers) => {
  const root = document.querySelector("[data-distribution]");
  const maxMembers = Math.max(1, ...distribution.map((item) => item.members));
  root.replaceChildren();

  distribution.forEach((item) => {
    const row = document.createElement("div");
    const track = createElement("span", "distribution__track");
    const bar = document.createElement("span");
    bar.style.width = `${Math.max(4, (item.members / maxMembers) * 100)}%`;
    track.append(bar);
    row.append(
      createElement("span", "", `TH${item.town_hall_level}`),
      track,
      createElement("strong", "", item.members)
    );
    root.append(row);
  });

  setTextAll("[data-distribution-note]", `${totalMembers} участников`);
};

const renderRoleSummary = (members) => {
  const counts = members.reduce((result, member) => {
    const role = member.clan_role || "unknown";
    result[role] = (result[role] || 0) + 1;
    return result;
  }, {});

  const root = document.querySelector("[data-role-summary]");
  root.replaceChildren();
  Object.keys(roleOrder).forEach((role) => {
    if (!counts[role]) return;
    const chip = createElement("span", "role-chip");
    chip.append(
      document.createTextNode(`${roleLabels[role]}: `),
      createElement("strong", "", counts[role])
    );
    root.append(chip);
  });
};


const currentWarStateLabels = {
  inWar: "На момент снимка война шла",
  preparation: "Подготовка",
  warEnded: "Война завершена",
  notInWar: "Сейчас войны нет"
};
const currentWarDisplay = globalThis.ClanAnalyticsCurrentWarContract?.currentWarDisplay;

const formatDate = (value, options) => {
  if (!value) return "Нет данных";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ru-RU", options).format(date);
};

const createWarMemberCard = (member, index) => {
  const position = Number.isFinite(member.war_position)
    ? member.war_position
    : index + 1;
  const card = createElement("article", "player-card war-member-card");
  card.dataset.name = safeText(member.nickname, "").toLocaleLowerCase("ru");
  card.dataset.attacks = String(member.attacks_used ?? "");
  card.dataset.th = member.town_hall_level ?? "";
  card.dataset.position = String(position);

  const header = createElement("div", "player-card__header");
  const avatar = createElement(
    "div",
    "player-avatar",
    String(position).padStart(2, "0")
  );
  const identity = document.createElement("div");
  identity.append(
    createElement("p", "player-card__eyebrow", `Позиция ${position}`),
    createElement("h3", "", safeText(member.nickname, "Без имени"))
  );

  const townHall = createElement("span", "town-hall");
  townHall.append(
    createElement("span", "", "TH"),
    document.createTextNode(safeText(member.town_hall_level, "?"))
  );
  header.append(avatar, identity, townHall);

  const metrics = createElement("dl", "player-metrics");
  const warMetrics = [
    ["Атаки", `${member.attacks_used} / ${member.attacks_available}`],
    ["Звёзды в атаках", member.stars_earned],
    ["Среднее", member.average_stars ?? "–"]
  ];
  if (member.new_stars_contributed !== null && member.new_stars_contributed !== undefined) {
    warMetrics.push(["Новые звёзды", member.new_stars_contributed]);
  }
  warMetrics.forEach(([label, value]) => {
    const wrapper = document.createElement("div");
    wrapper.append(
      createElement("dt", "", label),
      createElement("dd", "", value)
    );
    metrics.append(wrapper);
  });

  const status = createElement("div", "player-card__status");
  let statusText = "Все атаки использованы";
  let statusClass = "neutral-indicator neutral-indicator--complete";

  if (member.attacks_used === 0) {
    statusText = "Атаки ещё не использованы";
    statusClass = "neutral-indicator neutral-indicator--limited";
  } else if (member.attacks_used < member.attacks_available) {
    const remaining = member.attacks_available - member.attacks_used;
    statusText = `Осталось атак: ${remaining}`;
    statusClass = "neutral-indicator";
  }

  status.append(createElement("span", statusClass, statusText));
  card.append(header, metrics, status);
  return card;
};

const renderCurrentWar = (war, config) => {
  const content = document.querySelector("[data-current-war-content]");
  const empty = document.querySelector("[data-current-war-empty]");

  if (!war || war.data_status !== "available" || war.state === "notInWar") {
    content.hidden = true;
    empty.hidden = false;
    document.querySelectorAll("[data-current-war-status]").forEach((element) => {
      element.textContent = currentWarStateLabels.notInWar;
    });
    return;
  }

  content.hidden = false;
  empty.hidden = true;

  const progress = war.attacks_available
    ? Math.round((war.attacks_used / war.attacks_available) * 100)
    : 0;
  const attacksLeft = Math.max(0, war.attacks_available - war.attacks_used);
  const display = currentWarDisplay ? currentWarDisplay(war) : {
    clanStars: "–", attackStarsTotal: null, averageStars: "–"
  };

  const setAll = (selector, value) => {
    document.querySelectorAll(selector).forEach((element) => {
      element.textContent = String(value);
    });
  };

  setAll("[data-current-war-status]", currentWarStateLabels[war.state] || war.state);
  setAll("[data-current-war-participants]", war.participants);
  setAll("[data-current-war-stars]", display.clanStars);
  setAll("[data-current-war-attacks-used]", war.attacks_used);
  setAll("[data-current-war-attacks-available]", war.attacks_available);
  setAll("[data-current-war-attacks-per-member]", war.attacks_per_member);
  setAll("[data-current-war-average-stars]", display.averageStars);
  setAll("[data-current-war-attacks-left]", attacksLeft);
  setAll("[data-current-war-progress-label]", `${progress}%`);

  document.querySelectorAll("[data-current-war-end]").forEach((element) => {
    element.textContent = formatDate(war.end_time, {
      day: "numeric",
      month: "long",
      year: "numeric"
    });
    if (element instanceof HTMLTimeElement) element.dateTime = war.end_time;
  });

  if (config.current_war_collected_at) {
    document.querySelectorAll("[data-current-war-collected-at]").forEach((element) => {
      const collectedAt = config.current_war_collected_at;
      element.textContent = formatDate(collectedAt, {
        dateStyle: "medium",
        timeStyle: "short"
      });
      if (element instanceof HTMLTimeElement) {
        element.dateTime = collectedAt;
      }
    });
  } else {
    document.querySelectorAll("[data-current-war-collected-row]").forEach((element) => {
      element.hidden = true;
    });
  }

  const progressRoot = document.querySelector("[data-current-war-progress]");
  progressRoot.setAttribute("aria-valuemax", String(war.attacks_available));
  progressRoot.setAttribute("aria-valuenow", String(war.attacks_used));
  progressRoot.setAttribute(
    "aria-valuetext",
    `${war.attacks_used} из ${war.attacks_available} атак`
  );
  requireElement("[data-current-war-progress-bar]").style.width = `${progress}%`;

  const attackCounts = war.members.reduce((counts, member) => {
    const key = String(member.attacks_used);
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});

  const summary = document.querySelector("[data-war-attack-summary]");
  summary.replaceChildren();
  [0, 1, 2].forEach((attacks) => {
    const chip = createElement("span", "role-chip");
    chip.append(
      document.createTextNode(`${attacks} атак: `),
      createElement("strong", "", attackCounts[String(attacks)] || 0)
    );
    summary.append(chip);
  });

  const members = [...war.members].sort((left, right) => {
    const leftPosition = Number.isFinite(left.war_position)
      ? left.war_position
      : Number.MAX_SAFE_INTEGER;
    const rightPosition = Number.isFinite(right.war_position)
      ? right.war_position
      : Number.MAX_SAFE_INTEGER;
    if (leftPosition !== rightPosition) return leftPosition - rightPosition;
    return safeText(left.nickname, "").localeCompare(
      safeText(right.nickname, ""),
      "ru"
    );
  });
  const grid = document.querySelector("[data-current-war-members]");
  const search = document.querySelector("[data-war-search]");
  const attacksFilter = document.querySelector("[data-war-attacks-filter]");
  const townHallFilter = document.querySelector("[data-war-th-filter]");
  const visibleCount = document.querySelector("[data-war-visible-count]");

  [...new Set(
    members
      .map((member) => member.town_hall_level)
      .filter((value) => Number.isFinite(value))
  )]
    .sort((left, right) => right - left)
    .forEach((level) => {
      const option = document.createElement("option");
      option.value = String(level);
      option.textContent = `TH ${level}`;
      townHallFilter.append(option);
    });

  const cards = members.map(createWarMemberCard);
  grid.replaceChildren(...cards);

  const filterCards = () => {
    const query = search.value.trim().toLocaleLowerCase("ru");
    let shown = 0;

    cards.forEach((card) => {
      const visible =
        (!query || card.dataset.name.includes(query)) &&
        (!attacksFilter.value || card.dataset.attacks === attacksFilter.value) &&
        (!townHallFilter.value || card.dataset.th === townHallFilter.value);
      card.hidden = !visible;
      if (visible) shown += 1;
    });

    grid.querySelector(".roster-empty")?.remove();
    if (!shown) {
      grid.append(
        createElement(
          "div",
          "roster-empty",
          "По выбранным фильтрам участников войны не найдено."
        )
      );
    }
    if (visibleCount) {
      visibleCount.textContent = `Показано: ${shown} из ${cards.length}`;
    }
  };

  search.addEventListener("input", filterCards);
  attacksFilter.addEventListener("change", filterCards);
  townHallFilter.addEventListener("change", filterCards);
  filterCards();
};

const renderWarHistory = (history) => {
  const root = document.querySelector("[data-history-wars]");
  if (!root || !history?.wars) return;
  const coverageLabels = {
    official_aggregate_only_with_manual_detail: "Официальный итог + детализация по скриншотам",
    official_partial_detail_with_manual_supplement: "API + дополнение по скриншотам"
  };
  const provenanceLabels = {
    screenshot: "Скриншоты",
    api_and_screenshot: "API + скриншот",
    mixed_sources: "API и скриншоты"
  };
  const sourceLabels = {
    api_and_screenshot: "API + скриншот",
    screenshot_only: "Только скриншот",
    ambiguous: "Недостаточно данных"
  };
  const position = (value) => value === null || value === undefined ? "–" : `№${value}`;
  const detailList = (title, rows, renderRow) => {
    const details = document.createElement("details");
    details.className = "history-details";
    details.append(createElement("summary", "", title));
    const list = createElement("div", "history-details__list");
    rows.forEach((row) => list.append(renderRow(row)));
    details.append(list);
    return details;
  };
  const completed = history.wars.filter((war) => war.lifecycle_status !== "active");
  root.replaceChildren(...completed.map((war) => {
    const card = createElement("article", "war-card");
    const detail = war.manual_detail;
    const score = war.clan_stars ?? "–";
    card.append(
      createElement("p", "war-card__date", formatDate(war.end_time, { dateStyle: "medium" })),
      createElement("h3", "", `Официальный API: ${score} звёзд`),
      createElement("p", "", detail
        ? provenanceLabels[detail.provenance] || "Дополнительная детализация доступна."
        : "Доступны только штатные API-данные.")
    );
    if (detail) {
      const metrics = createElement("dl", "player-metrics");
      const summary = detail.summary || {};
      const rows = [
        ["Участники", summary.participants],
        ["Детальные API-атаки", summary.api_detailed_attacks],
        ["Атаки на скриншотах", summary.screenshot_attacks],
        ["Звёзды в атаках", summary.screenshot_attack_stars],
        ["Официальный вклад", summary.official_contribution],
        ["Совпали с API", summary.exact_api_matches],
        ["Только на скриншотах", summary.screenshot_only_attacks],
        ["Конфликты источников", summary.unresolved_conflicts]
      ].filter(([, value]) => value !== undefined && value !== null);
      rows.forEach(([label, value]) => {
        const row = document.createElement("div"); row.append(createElement("dt", "", label), createElement("dd", "", value)); metrics.append(row);
      });
      card.append(metrics, createElement("span", "neutral-indicator", coverageLabels[detail.coverage_status] || "Дополнительная детализация"));
      if (Array.isArray(detail.participants) && detail.participants.length) {
        card.append(detailList("Участники", detail.participants, (member) =>
          createElement("p", "history-row", `${position(member.war_position)} · ${safeText(member.nickname, "Без имени")}`)
        ));
      }
      if (Array.isArray(detail.attacks) && detail.attacks.length) {
        card.append(detailList("Подтверждённые атаки", detail.attacks, (attack) =>
          createElement("p", "history-row", `${position(attack.attacker_map_position)} → ${position(attack.defender_map_position)}, слот ${attack.screenshot_slot}: ${attack.stars} зв., ${attack.destruction_percentage}% · ${sourceLabels[attack.source] || "Источник не указан"}`)
        ));
      }
      if (Array.isArray(detail.source_conflicts) && detail.source_conflicts.length) {
        card.append(detailList("Конфликты источников", detail.source_conflicts, (conflict) =>
          createElement("p", "history-row", `${position(conflict.attacker_map_position)} → ${position(conflict.defender_map_position)}, ${conflict.destruction_percentage}%: API ${conflict.api_stars} зв., скриншот ${conflict.screenshot_stars} зв. · Не разрешён`)
        ));
      }
    }
    return card;
  }));
};

const renderSite = (data, config, currentWar, history) => {
  const members = [...data.members].sort((left, right) => {
    const roleDifference =
      (roleOrder[left.clan_role] ?? 99) - (roleOrder[right.clan_role] ?? 99);
    if (roleDifference) return roleDifference;

    const townHallDifference =
      (right.town_hall_level ?? -1) - (left.town_hall_level ?? -1);
    if (townHallDifference) return townHallDifference;

    return safeText(left.nickname, "").localeCompare(safeText(right.nickname, ""), "ru");
  });

  const townHallValues = members
    .map((member) => member.town_hall_level)
    .filter((value) => Number.isFinite(value));
  const averageTownHall = townHallValues.length
    ? townHallValues.reduce((sum, value) => sum + value, 0) / townHallValues.length
    : null;
  const medianTownHall = calculateMedian(townHallValues);

  document.title = `${data.clan.name} · Clan Analytics`;
  document.querySelectorAll("[data-clan-name]").forEach((element) => {
    element.textContent = data.clan.name;
  });
  document.querySelectorAll("[data-clan-level]").forEach((element) => {
    element.textContent = safeText(data.clan.level, "?");
  });
  document.querySelectorAll("[data-member-total]").forEach((element) => {
    element.textContent = data.composition.total_members;
  });
  document.querySelectorAll("[data-war-covered]").forEach((element) => {
    element.textContent = data.war_data_coverage.members_with_data;
  });
  document.querySelectorAll("[data-war-missing]").forEach((element) => {
    element.textContent = data.war_data_coverage.members_without_data;
  });
  setTextAll(
    "[data-average-th]",
    averageTownHall === null ? "–" : averageTownHall.toFixed(1)
  );
  setTextAll(
    "[data-median-th]",
    medianTownHall === null ? "–" : String(medianTownHall)
  );

  if (config.collected_at) {
    const date = new Date(config.collected_at);
    const formatted = Number.isNaN(date.getTime())
      ? config.collected_at
      : new Intl.DateTimeFormat("ru-RU", {
          dateStyle: "medium",
          timeStyle: "short"
        }).format(date);
    document.querySelectorAll("[data-collected-at]").forEach((element) => {
      element.textContent = formatted;
      if (element instanceof HTMLTimeElement) element.dateTime = config.collected_at;
    });
  } else {
    document.querySelectorAll("[data-collected-row]").forEach((element) => {
      element.hidden = true;
    });
  }

  applyClanBadge(config.badge_url);
  renderDistribution(
    data.composition.town_hall_distribution,
    data.composition.total_members
  );
  renderRoleSummary(members);

  const grid = document.querySelector("[data-player-grid]");
  const search = document.querySelector("[data-player-search]");
  const role = document.querySelector("[data-role-filter]");
  const townHall = document.querySelector("[data-th-filter]");
  const visibleCount = document.querySelector("[data-visible-count]");

  [...new Set(townHallValues)]
    .sort((left, right) => right - left)
    .forEach((level) => {
      const option = document.createElement("option");
      option.value = String(level);
      option.textContent = `TH ${level}`;
      townHall.append(option);
    });

  const cards = members.map(createPlayerCard);
  grid.replaceChildren(...cards);

  const filterCards = () => {
    const query = search.value.trim().toLocaleLowerCase("ru");
    let shown = 0;

    cards.forEach((card) => {
      const visible =
        (!query || card.dataset.name.includes(query)) &&
        (!role.value || card.dataset.role === role.value) &&
        (!townHall.value || card.dataset.th === townHall.value);
      card.hidden = !visible;
      if (visible) shown += 1;
    });

    grid.querySelector(".roster-empty")?.remove();
    if (!shown) {
      grid.append(createElement("div", "roster-empty", "По выбранным фильтрам участников не найдено."));
    }
    if (visibleCount) {
      visibleCount.textContent = `Показано: ${shown} из ${cards.length}`;
    }
  };

  search.addEventListener("input", filterCards);
  role.addEventListener("change", filterCards);
  townHall.addEventListener("change", filterCards);
  filterCards();
  renderCurrentWar(currentWar, config);
  renderWarHistory(history);

  requireElement("[data-loading]").hidden = true;
  requireElement("[data-content]").hidden = false;
};

const showError = (error) => {
  console.error("Clan Analytics render failed:", error);

  const loading = document.querySelector("[data-loading]");
  if (loading) loading.hidden = true;

  const target = document.querySelector("[data-error]");
  if (!target) {
    return;
  }

  target.hidden = false;
  target.textContent =
    `Не удалось загрузить данные сайта: ${error.message}. ` +
    "Обнови страницу с Ctrl+F5 или проверь JSON-файлы в каталоге data.";
};

window.addEventListener("DOMContentLoaded", async () => {
  try {
    const [rosterResponse, configResponse, currentWarResponse, historyResponse] = await Promise.all([
      fetch(`data/roster.json?v=${Date.now()}`, { cache: "no-store" }),
      fetch(`data/site-config.json?v=${Date.now()}`, { cache: "no-store" }),
      fetch(`data/current-war.json?v=${Date.now()}`, { cache: "no-store" }),
      fetch(`data/war-history.json?v=${Date.now()}`, { cache: "no-store" })
    ]);

    if (!rosterResponse.ok) throw new Error(`roster.json: HTTP ${rosterResponse.status}`);
    if (!configResponse.ok) throw new Error(`site-config.json: HTTP ${configResponse.status}`);
    if (!currentWarResponse.ok) {
      throw new Error(`current-war.json: HTTP ${currentWarResponse.status}`);
    }
    if (!historyResponse.ok) throw new Error(`war-history.json: HTTP ${historyResponse.status}`);

    renderSite(
      await rosterResponse.json(),
      await configResponse.json(),
      await currentWarResponse.json(),
      await historyResponse.json()
    );
  } catch (error) {
    showError(error);
  }
});

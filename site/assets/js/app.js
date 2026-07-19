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
    const metrics = createElement("dl", "player-metrics");
    const values = [
      ["Атаки", member.attacks_available === null ? "–" : `${member.attacks_used} / ${member.attacks_available}`],
      ["Звёзды", member.stars_earned ?? "–"],
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
      hasWarData ? "Военные данные доступны" : "История войн не собрана"
    )
  );
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

  document.querySelector("[data-distribution-note]").textContent =
    `${totalMembers} участников`;
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

const renderSite = (data, config) => {
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
  document.querySelector("[data-war-covered]").textContent =
    data.war_data_coverage.members_with_data;
  document.querySelector("[data-war-missing]").textContent =
    data.war_data_coverage.members_without_data;
  document.querySelector("[data-average-th]").textContent =
    averageTownHall === null ? "–" : averageTownHall.toFixed(1);
  document.querySelector("[data-median-th]").textContent =
    medianTownHall === null ? "–" : String(medianTownHall);

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
    visibleCount.textContent = `Показано: ${shown} из ${cards.length}`;
  };

  search.addEventListener("input", filterCards);
  role.addEventListener("change", filterCards);
  townHall.addEventListener("change", filterCards);
  filterCards();

  document.querySelector("[data-loading]").hidden = true;
  document.querySelector("[data-content]").hidden = false;
};

const showError = (error) => {
  document.querySelector("[data-loading]").hidden = true;
  const target = document.querySelector("[data-error]");
  target.hidden = false;
  target.textContent =
    `Не удалось загрузить локальные данные: ${error.message}. ` +
    "Открывай сайт через локальный HTTP-сервер, а не как file://.";
};

window.addEventListener("DOMContentLoaded", async () => {
  try {
    const [rosterResponse, configResponse] = await Promise.all([
      fetch(`data/roster.json?v=${Date.now()}`, { cache: "no-store" }),
      fetch(`data/site-config.json?v=${Date.now()}`, { cache: "no-store" })
    ]);

    if (!rosterResponse.ok) throw new Error(`roster.json: HTTP ${rosterResponse.status}`);
    if (!configResponse.ok) throw new Error(`site-config.json: HTTP ${configResponse.status}`);

    renderSite(await rosterResponse.json(), await configResponse.json());
  } catch (error) {
    showError(error);
  }
});

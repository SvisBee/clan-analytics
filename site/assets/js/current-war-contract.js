/* Shared, dependency-free public current-war compatibility contract. */
(() => {
  const unavailable = "–";

  const currentWarDisplay = (war) => {
    // Legacy stars_earned meant the sum of all attack results, not the
    // official clan score. Only explicit clan_stars is authoritative.
    const officialStars = Number.isFinite(war?.clan_stars) ? war.clan_stars : null;
    const attackStarsTotal = Number.isFinite(war?.attack_stars_total)
      ? war.attack_stars_total
      : (Number.isFinite(war?.stars_earned) ? war.stars_earned : null);
    const attacksUsed = Number.isFinite(war?.attacks_used) ? war.attacks_used : null;
    const averageStars = attacksUsed && attackStarsTotal !== null
      ? (attackStarsTotal / attacksUsed).toFixed(2)
      : unavailable;
    return {
      clanStars: officialStars ?? unavailable,
      attackStarsTotal,
      averageStars
    };
  };

  globalThis.ClanAnalyticsCurrentWarContract = { currentWarDisplay, unavailable };
})();

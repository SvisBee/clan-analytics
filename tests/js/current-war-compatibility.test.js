const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..", "..");
const contractSource = fs.readFileSync(path.join(root, "site/assets/js/current-war-contract.js"), "utf8");
const context = { globalThis: {} };
vm.runInNewContext(contractSource, context, { filename: "current-war-contract.js" });
const display = context.globalThis.ClanAnalyticsCurrentWarContract.currentWarDisplay;

test("new consumer reads the real legacy current-war contract without inventing a clan score", () => {
  const result = display({ stars_earned: 43, attacks_used: 18 });
  assert.equal(result.clanStars, "–");
  assert.equal(result.attackStarsTotal, 43);
  assert.equal(result.averageStars, "2.39");
});

test("new consumer reads v2 current-war JSON", () => {
  const result = display({ clan_stars: 38, attack_stars_total: 43, stars_earned: 38, attacks_used: 18 });
  assert.equal(result.clanStars, 38);
  assert.equal(result.averageStars, "2.39");
});

test("legacy consumer contract reads the transition alias", () => {
  const transitionJson = { clan_stars: 38, stars_earned: 38, attack_stars_total: 43, attacks_used: 18 };
  assert.equal(transitionJson.stars_earned, 38);
  assert.notEqual(String(transitionJson.stars_earned), "undefined");
  assert.notEqual((transitionJson.stars_earned / transitionJson.attacks_used).toFixed(2), "NaN");
});

test("missing score remains neutral rather than undefined or NaN", () => {
  const result = display({ stars_earned: 43, attacks_used: 0 });
  assert.equal(result.clanStars, "–");
  assert.equal(result.averageStars, "–");
});

test("site contract retains the player attack-stars label", () => {
  const app = fs.readFileSync(path.join(root, "site/assets/js/app.js"), "utf8");
  assert.match(app, /Звёзды в атаках/);
});

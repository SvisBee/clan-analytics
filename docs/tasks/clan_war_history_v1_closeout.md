# Clan War History v1 closeout

## Статус

completed

## Область этапа

- ordinary clan wars, current-war snapshot и completed war history;
- manual recovery первых двух войн и accumulated public player metrics;
- GitHub Pages publication и hourly updater.

## Итоговая архитектура

API history является authoritative, observations immutable. Local manual overlay отделён от API history и связывается только по internal war ID во время сборки. Player mapping выполняется только в памяти; player tags и war IDs не публикуются. Unresolved conflicts остаются unresolved. Public JSON строится штатным builder, optional overlay fail-open, а committed public JSON проходит отдельную validation guard.

## Важные исправления

- canonical-null merge crash;
- exact linking вместо tuple matching;
- официальный score старой войны;
- accumulated player metrics и roster/history consistency;
- shell wrapper prefix в public JSON;
- frontend label восстановленных данных.

## Проверенные значения

- War 01: official stars 30, participants 10, screenshot attacks 13, screenshot stars 38, official contribution 30.
- War 02: official stars 45, API attacks 18, screenshot attacks 22, exact 15, screenshot-only 4, unresolved conflicts 3, screenshot stars 58, official contribution 45.
- SvisBee: 3 wars, 5 / 6 attacks, 15 attack stars, average 3.

## Production state

- GitHub Pages: https://svisbee.github.io/clan-analytics/.
- Scheduled Task: `Clash Clan Analytics - Hourly Update`, hourly cadence.
- Codebase Memory project: `D-coc`, root `D:/coc`.

## Ограничения v1

- Manual evidence существует только для первых двух войн и не меняет canonical API history.
- `new_stars_contributed` unavailable для totals, дополненных manual evidence.
- Старые игроки вне текущего roster остаются только в history.
- Scheduled Task зависит от интерактивного Windows user context; API может вернуть 403 вне IP allowlist.
- Codebase Memory не индексирует local data, runs или secrets.

## Definition of Done

- [x] Tests green.
- [x] Git clean and commits pushed.
- [x] Pages deployed.
- [x] Hourly task enabled after closeout.
- [x] Documentation updated.
- [x] Codebase Memory refreshed.

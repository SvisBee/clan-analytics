# Clash Clan Analytics

Проект аналитики клана Clash of Clans с публикацией только подготовленной публичной части как статического сайта.

**Текущий статус:** `partial - live collection and publication operational; historical analytics accumulating`.

Официальный Clash of Clans API подключён локально через три fail-closed probe: текущий состав, current war и war log. Подготовленные публичные данные публикуются на GitHub Pages, а отключаемый Windows Scheduled Task выполняет unified updater каждый час и при входе пользователя. Raw responses, стабильные игровые tags, token и подробная внутренняя history остаются вне Git.

Текущий публичный сайт использует реальные данные состава и текущей войны. История обычных войн находится на раннем этапе накопления. Schema v2 для надёжной истории, immutable observations, reconciliation и recovery реализована offline, но реальная локальная history v1 ещё не мигрировалась. Рейтинг игроков и рекомендации состава не реализованы.

Будущие модули охватывают состав клана, пожертвования, обычные КВ, ЛВК, рейды и Игры кланов. Модуль Игр кланов принят на уровне концепции как отдельное направление активности; источник данных ещё исследуется, поэтому первоначально возможен ручной импорт. Реализация модуля не начата.

Публичный прототип развивается последовательно: информационная страница → состав клана → базовые карточки игроков → обычные КВ → метрики атак и рекомендации состава → остальные модули. Публичный сайт содержит только явно разрешённые поля. Личные и управленческие заметки остаются локально в исключённой области `D:\coc\data` и не попадают в Git, Codebase Memory, Obsidian или GitHub Pages.

## Codebase Memory

Для workspace настроен проект Codebase Memory `D-coc` с root `D:/coc`. Безопасные исключения находятся в `D:\coc\.cbmignore`; refresh выполняется только по отдельному разрешению.

Подробности: [docs/codebase_memory.md](docs/codebase_memory.md).

## Project-local skills и security readiness

Доступны `$coc-design-engineering`, `$coc-review-and-qa`, `$coc-debugging` и `$coc-code-simplification`; правила выбора и границы описаны в [docs/local_skills.md](docs/local_skills.md).

Strix исследован только на уровне readiness-документации: scaffold подготовлен, но инструмент не установлен и не запускался. Фактическое security testing отложено до появления подходящего динамического attack surface и отдельных разрешений. Подробнее: [docs/security/strix_readiness.md](docs/security/strix_readiness.md).

## Структура

- `src/clan_analytics/` – модели API, нормализация, история и public projections.
- `site/` – опубликованный статический сайт и подготовленные публичные данные.
- `tests/` – offline unit tests и fictional fixtures.
- `scripts/` – API runners, unified updater и workspace tooling.
- `docs/` – концепция, архитектура, политика данных, roadmap и журнал решений.
- `D:\coc\data` – постоянные локальные данные, базы, сырьё, ручные вводы и секреты; вне Git.
- `D:\coc\runs` – результаты отдельных запусков; вне Git.
- `D:\coc\obsidian` – человеческие заметки и решения; вне Git.
- `D:\coc\local` – локальные логи, временные файлы и настройки автоматизации; вне Git.

## Основные правила безопасности

- Git root – только `D:\coc\repo`.
- Секреты хранятся только в `D:\coc\data\secrets` и никогда не публикуются.
- API, сеть, создание ключей, commit, push, GitHub Pages и автоматизация требуют отдельных явных разрешений.
- Публичные данные отделяются от внутренних комментариев и служебных статусов.
- Игроки идентифицируются по `player_tag`; определения метрик версионируются.

## Локальный прототип сайта

Информационная страница находится в `site/index.html`; подробности и команда безопасного локального просмотра описаны в [docs/site_prototype.md](docs/site_prototype.md).

Контракт внутренней модели и публичного allowlist описан в [docs/roster_data_contract.md](docs/roster_data_contract.md). `site/data/roster.json` генерируется из live snapshot и не содержит `player_tag`.

Перед следующим исполняемым этапом необходимо собрать обратную связь клана, согласовать интерфейс и публичные поля, а затем отдельно выбрать технический источник состава. API не считается автоматически выбранным следующим этапом.

План первого раунда, вопросы и реестр предложений: [docs/clan_feedback_round_1.md](docs/clan_feedback_round_1.md).

## Публикация

- Публичный repository: [SvisBee/clan-analytics](https://github.com/SvisBee/clan-analytics).
- Публичный сайт: [https://svisbee.github.io/clan-analytics/](https://svisbee.github.io/clan-analytics/).
- GitHub Pages публикует только каталог `site`; внутренние данные и локальные каталоги не входят ни в repository, ни в Pages artifact.
- Сайт работает независимо от включённого локального компьютера.
- Дальнейшее улучшение продолжается локально; новая версия появляется только после commit и разрешённого push изменений сайта.
- Публикуются только allowlist-поля реальных игровых данных; raw и internal history остаются локально.

## Определения звёзд

- `clan_stars` – официальный общий счёт из `current war payload -> clan.stars`; именно он подписан на сайте как «Звёзды клана».
- `attack_stars_total` – техническая сумма результатов всех атак; она может быть больше официального счёта при повторных атаках по одной базе.
- Верхнеуровневый `stars_earned` в current-war public JSON временно сохранён как deprecated compatibility alias официального `clan_stars`. Новые consumers используют `clan_stars` и `attack_stars_total`.

Regression fixture сохраняет случай `38/43/18`: официальный счёт 38, сумма результатов атак 43 и 18 использованных атак. Позднее live-состояние стало 41/19, а финальный official `clan_stars` был 45; эти значения не заменяют fixture. Offline lifecycle test использует `38 -> 41 -> 45`: active score может расти, stale snapshot не уменьшает final score, включая первый противоречивый `warEnded`, который сохраняется как observation и фиксируется диагностически. Будущая live validation сравнит public output с актуальным official API snapshot, а не с жёстко заданным числом. Legacy JSON без `clan_stars` не позволяет восстановить официальный счёт: новый frontend показывает «–» и использует legacy `stars_earned` только для суммы атак.
- `stars_earned` – сумма результатов атак конкретного игрока и отображается как «Звёзды в атаках».
- `new_stars_contributed` – чистый прирост по глобальному `attack.order`; при ненадёжном порядке значение равно `null`.

Подробный контракт истории и восстановления: [docs/reliable_history_foundation.md](docs/reliable_history_foundation.md).

Подробности workflow и проверки deployment: [docs/github_pages.md](docs/github_pages.md).

## Безопасный просмотр

```powershell
Get-ChildItem -LiteralPath D:\coc -Depth 3
git -C D:\coc\repo status
git -C D:\coc\repo remote -v
```

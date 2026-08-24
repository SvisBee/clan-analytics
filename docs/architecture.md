# Архитектура

## Планируемый поток данных

```text
Clash of Clans API
→ raw JSON
→ локальная SQLite
→ расчёт показателей
→ публичные JSON/CSV
→ статический сайт
→ GitHub Pages
```

Сетевой сбор, raw run storage, public projections и публикация реализованы. Три локальных probe получают roster, current war и war log; unified updater строит allowlist-only JSON и публикует их только при изменении. SQLite пока не используется.

Точное состояние проверки официальной схемы, изолированные wire assumptions, модели и формулы: [clash_api_data_foundation.md](clash_api_data_foundation.md).

## Будущий поток состава

```text
подтверждённый источник состава
→ внутренняя нормализованная модель по player_tag
→ валидация
→ расчёт разрешённых агрегатов
→ public allowlist
→ site/data/roster.json
→ таблица состава
```

`site/data/roster.json` является генерируемым публичным артефактом и не редактируется вручную. Внутренние поля отбрасываются до записи файла, а экспорт пропускает только поля из явного allowlist. Статический сайт читает подготовленный публичный JSON и не получает доступ к локальной SQLite, raw payload или внутренней модели.

Draft-контракт модели, источников и экспорта: [roster_data_contract.md](roster_data_contract.md).

Нормализация принимает API payload вместе с `collected_at` и локальной provenance, формирует внутренние snapshots и возвращает allowlist-only dict. Unified updater генерирует `site/data/roster.json`; tags и provenance в public projection не входят.

## История обычных войн v2

Schema v2 разделяет immutable detailed observations, monotonic canonical snapshot, официальный aggregate war log и inferred lifecycle. Идентификатор записи назначается один раз, а поздние snapshots связываются по совместимым сильным timestamps и дополнительному evidence. Progressive timestamps проходят chronology guard `preparation <= start <= end`, трёхчасовое collection window и семидневное bounded game-time window; оба окна являются project heuristics, не правилами API. Неоднозначность блокирует автоматическое объединение и фиксируется диагностически.

War log может подтвердить завершение и официальный результат, но не создаёт участников, персональные атаки, defender links или map positions. Повреждённая history не заменяется пустой. Миграция v1 сохраняет единственный доступный legacy `latest` как одну observation с явной маркировкой ограничения.

Подробности: [reliable_history_foundation.md](reliable_history_foundation.md).

## Поток Игр кланов v1

```text
operator-confirmed event registry
→ active-event lookup
→ official player profile / Games Champion
→ будущая local observation SQLite
→ расчёт показателей
→ публичная безопасная выгрузка
→ статический сайт
```

Source validation подтвердила official-derived lifetime cumulative candidate `Games Champion` в official player profile. Phase 1 reusable client/normalizer реализована, но не подключена к hourly updater. Phase 2 реализовала local-only schema-v1 registry конкретных событий по пути `data/clan_games/event_registry.v1.json`. Registry создаётся и наполняется только explicit operator action, требует exact start/end и query-free HTTPS source на доказанном host `supercell.com`, не содержит recurrence или cap и не fetch-ит source URL автоматически. Production event в implementation-фазе не зарегистрирован.

`player_tag` – основной устойчивый идентификатор будущих observations. Публичные и внутренние поля формируются раздельно: identity и provenance registry не становятся public автоматически.

Event status вычисляется от timezone-aware `as_of`; `start <= as_of < end` означает active. Overlap запрещён, touching boundaries разрешены, ended records сохраняются. Explicit replacement требует validated local backup и atomic replace. Observation SQLite, collector, scheduling, public projection и frontend остаются следующими отдельными фазами.

Предыдущий CSV draft не является production authority. Актуальный registry contract:

```text
event_id,start_at_utc,end_at_utc,official_source_url,confirmed_at_utc
```

## Границы хранения

Только локально находятся API-токен, полная SQLite, исходные ответы API, ручные внутренние комментарии, резервные копии, логи и результаты запусков. Они размещаются соответственно в `D:\coc\data`, `D:\coc\runs` и `D:\coc\local`.

В Git могут попасть Python-код, тесты, техническая документация, HTML/CSS/JS и специально подготовленные публичные JSON/CSV в `site/data`. Перед добавлением экспорт проверяется по белому списку публичных полей.

На сайте публикуются только нейтральные, согласованные данные: публичные показатели, карточки игроков и агрегаты без секретов, внутренних причин, предупреждений и внутренних workflow-статусов. Нейтральное агрегированное состояние проверки допускается только через утверждённый allowlist.

## Внутренние заметки руководства

Предварительное локальное место для будущих заметок – `D:\coc\data\manual\leadership_notes` либо отдельные таблицы будущей локальной SQLite. На текущем этапе реальные заметки и файлы не создаются.

Эти данные:

- находятся только локально;
- исключены из Git всем каталогом `D:\coc\data`;
- исключены из Codebase Memory через корневой `.cbmignore`;
- не копируются в индексируемый `D:\coc\obsidian`;
- не экспортируются в `repo/site/data` и не попадают на GitHub Pages.

Публичный экспорт строится по allowlist: публикуются только явно разрешённые поля. Отсутствие поля в denylist недостаточно для публикации. Внутренний локальный интерфейс заметок, если он появится, должен быть архитектурно отделён от публичного статического сайта.

## Почему сбор происходит локально

GitHub Pages раздаёт статические файлы и не выполняет Python-код на сервере. Поэтому запросы к API, хранение токена, обновление SQLite, расчёты и подготовка экспортов должны происходить на локальном компьютере. После отдельной проверки и разрешения подготовленная публичная версия может быть отправлена в GitHub.

## Автоматизация

Unified updater и Windows Scheduled Task реализованы. Task запускается каждый час и при входе пользователя, использует mutex, пишет runs/logs, выполняет offline checks и создаёт commit/push только при изменении публичных JSON. Task можно отключать для безопасного обслуживания; изменение её состояния всегда требует отдельного разрешения.

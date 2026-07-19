# Фундамент данных Clash of Clans API

Статус: локальный probe-код подготовлен и проверен offline, но не запускался в execute-mode. Токен, реальный клановый тег и реальные ответы отсутствуют. Дата проверки официального портала: 2026-07-19.

## Граница подтверждённых сведений

Исследованы только официальные страницы `developer.clashofclans.com`:

- [главная страница портала](https://developer.clashofclans.com/);
- [Getting Started](https://developer.clashofclans.com/#/getting-started);
- [API Documentation](https://developer.clashofclans.com/#/documentation);
- [статическая страница Swagger UI](https://developer.clashofclans.com/api-docs/index.html).

Публичная часть подтверждает near real-time JSON API, доступ к поиску и профилям кланов, профилям игроков и лигам. Для каждого запроса нужен JWT API key, привязанный к разрешённым IP-адресам и rate limits. Ключ следует хранить приватно.

Страница API Documentation требует входа. Swagger UI открывается, но без сессии портал не подставляет рабочий URL определения. Поэтому точные endpoint paths, схемы, типы, обязательность, nullable и enum values на этом этапе официально не подтверждены. Авторизация, регистрация и создание ключа не выполнялись. Сторонние источники не использовались.

## Реестр сущностей

Для endpoint-specific строка `same API key` означает общую схему авторизации, подтверждённую Getting Started, а не подтверждение существования endpoint.

| Entity | Official endpoint/resource | Availability | Authorization | Relevant fields | Optional fields | Privacy | Verification |
|---|---|---|---|---|---|---|---|
| Clan | Clan Search and Clan Profiles | Публичная страница подтверждает семейство ресурсов; path недоступен | API key, IP restriction, rate limits | Точные wire-поля недоступны | Не подтверждены | Тег внутренний; публичные поля только по allowlist | `resource_confirmed`, schema `unverified` |
| Clan member | Недоступно без Swagger | Не подтверждено отдельно | same API key | Не подтверждены | Не подтверждены | Tag не публикуется без решения | `unverified` |
| Player | Player Profiles | Публичная страница подтверждает семейство ресурсов; path недоступен | API key, IP restriction, rate limits | Точные wire-поля недоступны | Не подтверждены | Профиль не делает поле автоматически публичным | `resource_confirmed`, schema `unverified` |
| Current war | Недоступно без Swagger | Не подтверждено | same API key | Не подтверждены | Не подтверждены | Участники и теги остаются внутренними до allowlist | `unverified` |
| War participant | Недоступно без Swagger | Не подтверждено | same API key | Не подтверждены | Не подтверждены | Не публиковать персональные оценки | `unverified` |
| War attack | Недоступно без Swagger | Не подтверждено | same API key | Не подтверждены | Не подтверждены | Публиковать только нейтральные агрегаты | `unverified` |
| War log entry | Недоступно без Swagger | Не подтверждено | same API key | Не подтверждены | Не подтверждены | Raw history локальна | `unverified` |
| Clan War League group | Публичная страница говорит только о leagues | CWL group отдельно не подтверждён | same API key | Не подтверждены | Не подтверждены | Состав и теги внутренние до allowlist | `unverified` |
| Clan War League war | Недоступно без Swagger | Не подтверждено | same API key | Не подтверждены | Не подтверждены | Те же границы, что для обычной войны | `unverified` |

## Изолированный wire-контракт fixtures

Следующие имена используются только в вымышленных tests fixtures. Они являются adapter assumptions, а не подтверждёнными официальными именами или признаками обязательности. До authenticated Swagger review или отдельно разрешённого probe нельзя переносить их в сетевой клиент без повторной проверки.

| Fixture entity | Wire name candidate | Fixture type | Project handling | Official requiredness | UI use | Public policy |
|---|---|---|---|---|---|---|
| Clan | `tag` | string | требуется локальным нормализатором как natural key | `unverified` | напрямую не выводится | internal only |
| Clan | `name` | string | требуется для понятного snapshot | `unverified` | идентичность клана в будущем | только после подтверждения |
| Clan | `clanLevel` | integer/null | optional | `unverified` | сейчас не используется | allowlist needed |
| Clan | `memberList` | array/missing | missing трактуется как пустой список | `unverified` | источник roster | raw internal |
| Clan member / Player | `tag` | string | требуется для устойчивой связи | `unverified` | соответствует `player_tag` | не входит в public allowlist v1 |
| Clan member / Player | `name` | string | требуется локальным нормализатором | `unverified` | `nickname` | public allowlist |
| Clan member / Player | `role` | string/null | optional | `unverified` | `clan_role` | public after display mapping |
| Clan member / Player | `townHallLevel` | integer/null | optional | `unverified` | `town_hall_level` | public allowlist |
| War | `state` | string | требуется для различения fixture state | `unverified` | summary status | neutral only |
| War | `attacksPerMember` | integer/null | optional | `unverified` | derived `attacks_available` | aggregate only |
| War | `endTime` | string/null | optional; unknown format gives null date | `unverified` | derived `last_war_date` | date allowed |
| War | `clan.members` | array/missing | missing трактуется как пустой список | `unverified` | history source | raw internal |
| War member | `tag` | string | требуется при наличии участника | `unverified` | join only | internal only |
| War member | `name` | string | требуется при наличии участника | `unverified` | internal cross-check | not exported from war raw |
| War member | `townhallLevel` | integer/null | optional; capitalization intentionally isolated | `unverified` | context only | allowlist needed |
| War member | `attacks` | array/missing | missing означает zero observed attacks | `unverified` | metrics source | raw internal |
| Attack | `attackerTag` | string | требуется при наличии attack | `unverified` | join only | internal only |
| Attack | `defenderTag` | string/null | optional | `unverified` | сейчас не используется | internal only |
| Attack | `stars` | integer 0..3 | локальный fixture invariant | `unverified` | `stars_earned`, `average_stars` | aggregate only |
| Attack | `destructionPercentage` | number/null | optional | `unverified` | сейчас не используется | aggregate only after approval |
| Attack | `order` | integer/null | optional; fallback stable tags | `unverified` | сейчас не используется | internal only |
| War log | exact fields unavailable | unknown | fixture intentionally deferred | `unverified` | none | raw internal |
| Current war no-data state | exact value unavailable | unknown | fixture intentionally deferred | `unverified` | future no-data state | neutral only |

`sourceTimestamp` в fixtures является project-owned provenance field. Значение state `fixture_completed` также принадлежит fixture-контракту и не заявляется как официальный enum. Это не утверждение о наличии таких wire-значений в официальном API.

## Mapping API-shaped fixture to UI

| UI field | Candidate source | Direct/derived | Boundary | Nullable | Fallback and calculation |
|---|---|---|---|---|---|
| `nickname` | member/player `name` | direct | public allowlist | no in normalized snapshot | malformed fixture error |
| `player_tag` | member/player `tag` | direct | internal stable key | no in normalized snapshot | malformed fixture error; omitted from public projection |
| `telegram_username` | no Clash API source | separate local value | private by default | yes | omitted unless value and explicit public consent are both supplied |
| `clan_role` | member/player `role` | direct | public allowlist | yes | null; future display mapping after schema verification |
| `town_hall_level` | member/player `townHallLevel` | direct | public allowlist | yes | null |
| `war_participations` | detailed local war snapshots joined by tag | derived | public aggregate | no | 0 with `insufficient_data` when history is absent |
| `attacks_used` | count of observed attacks | derived | public aggregate | yes | null when no participation history |
| `attacks_available` | sum of known `attacksPerMember` for participated wars | derived | public aggregate | yes | null if any participating war lacks the value |
| `stars_earned` | sum of observed attack stars | derived | public aggregate | yes | null when no participation history |
| `average_stars` | `stars_earned / attacks_used` | derived | public aggregate | yes | rounded to 2 decimals; null for zero attacks or no history |
| `last_war_date` | max parseable detailed war `endTime` | derived | public allowlist | yes | null if history/date is absent or unparseable |

War log alone is not assumed to contain per-player attacks. Historical member metrics require stored detailed `WarSnapshot` values. The code does not infer missing attacks, participation, dates or stars.

## Нормализованные модели

| Model | Natural key | Source timestamp | Collected timestamp | Nullable | Raw reference | Public projection | Internal projection |
|---|---|---|---|---|---|---|---|
| `ClanSnapshot` | `clan_tag` | caller-supplied or null | explicit caller argument | level | required local reference | name and approved aggregates only | tag, members, provenance |
| `ClanMemberSnapshot` | `player_tag` | inherited from clan snapshot | explicit caller argument | role, Town Hall | required local reference | allowlisted card fields, no tag | tag and provenance |
| `PlayerProfileSnapshot` | `player_tag` | caller-supplied or null | explicit caller argument | role, Town Hall | required local reference | allowlisted card fields | tag and provenance |
| `WarSnapshot` | future composite key after schema verification | caller-supplied or null | explicit caller argument | end time, attacks per member | required local reference | neutral aggregate summary | state, member snapshots, provenance |
| `WarMemberSnapshot` | war key plus `player_tag` | inherited from war | inherited from war | Town Hall | inherited from war | aggregates only | tag and attacks |
| `WarAttackSnapshot` | future official attack key; stable sort fallback only | inherited from war | inherited from war | defender, destruction, order | inherited from war | sums/averages only | attack details |

`leadership_note`, `review_status`, `manual_flags` and `consent_flags` belong to a separate future local internal model. Они не входят в normalized API snapshots или `site/data`. Public builder читает только отдельный consent-gated Telegram input и никогда не экспортирует сами consent flags.

## Fixtures и расчёты

Fixtures находятся в `tests/fixtures`, не в `site/data`. Все имена, теги, цели и даты очевидно вымышлены. `clan.json` содержит двух участников, `player_profiles.json` покрывает полный и неполный профиль, `war_history.json` содержит две завершённые fixture-войны, несколько атак, звёзды и одну неиспользованную доступную атаку. Fixtures для пустого war log и current-war no-data state намеренно не созданы: их точная официальная форма не подтверждена доступной схемой.

Реализованы только нейтральные формулы:

- `attacks_used`: число наблюдаемых атак участника;
- `attacks_available`: сумма известных лимитов атак для войн с участием;
- `stars_earned`: сумма звёзд наблюдаемых атак;
- `average_stars`: звёзды / использованные атаки, округление до 2 знаков;
- `war_participations`: число detailed snapshots, где найден tag игрока;
- `last_war_date`: максимальная распознанная дата окончания среди этих snapshots;
- `town_hall_distribution`: количество участников по известным уровням, уровень по убыванию;
- `members_with_limited_data`: число участников без role или Town Hall.

Игрок без истории получает `data_status: insufficient_data`; показатели, которые нельзя вывести из наблюдений, равны null. Общий рейтинг, скрытый score и выводы о качестве игрока отсутствуют.

## Следующий безопасный этап

Подготовленный CLI и safety contract описаны в [clan_roster_probe.md](clan_roster_probe.md). Endpoint template и base URL остаются обязательными ручными inputs без defaults, потому что официальная Swagger-схема не была доступна без входа.

После отдельных разрешений нужен authenticated review официальной Swagger-схемы и только затем один минимальный API probe. До него следует:

1. подтвердить endpoint paths, response schemas, types, required/nullable и enums;
2. заменить или удалить каждую `unverified` wire assumption;
3. утвердить natural key войны и правила snapshot history;
4. не создавать `site/data/roster.json`, пока источник, валидация и public allowlist не пройдут отдельное согласование.

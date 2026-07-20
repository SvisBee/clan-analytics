# Фундамент данных Clash of Clans API

Статус: контракт подготовлен, а roster, current-war и war-log probes прошли live validation и используются unified updater. Token, clan tag и raw responses остаются локально. Дата проверки официального портала: 2026-07-19.

## Граница подтверждённых сведений

На `2026-07-19` пользователь авторизованно просмотрел официальную Swagger-документацию. В проект не переносились token, cookies или session data. Краткое воспроизводимое evidence с точными endpoints, wire fields и Swagger types сохранено в [clash_api_authenticated_contract_2026-07-19.md](clash_api_authenticated_contract_2026-07-19.md).

Официально подтверждены Bearer JWT header, перечисленные endpoint paths, имена моделей, точные wire-field names, показанные Swagger types, API origin `https://api.clashofclans.com` и version prefix `/v1`. Нормализованный официальный base URL: `https://api.clashofclans.com/v1`. Requiredness, nullability, точные enum values, status codes, error mapping, clan-tag alphabet/length и конкретные rate limits остаются неподтверждёнными.

Используемые статусы: `endpoint_verified`, `field_name_verified`, `field_type_verified`, `base_url_verified`, `origin_verified`, `version_prefix_verified`, `enum_values_unverified`, `requiredness_unverified`, `nullability_unverified`.

## Реестр сущностей

| Entity | Official endpoint | Authorization | Privacy | Verification |
|---|---|---|---|---|
| Clan profile | `GET /clans/{clanTag}` | Bearer JWT | tag и raw data internal | `endpoint_verified` |
| Clan members | `GET /clans/{clanTag}/members` | Bearer JWT | текущий probe не вызывает | `endpoint_verified` |
| Current war | `GET /clans/{clanTag}/currentwar` | Bearer JWT | участники и теги internal | `endpoint_verified` |
| War log | `GET /clans/{clanTag}/warlog` | Bearer JWT | raw history local | `endpoint_verified` |
| CWL group | `GET /clans/{clanTag}/currentwar/leaguegroup` | Bearer JWT | состав internal | `endpoint_verified` |
| CWL war | `GET /clanwarleagues/wars/{warTag}` | Bearer JWT | состав internal | `endpoint_verified` |
| Capital raids | `GET /clans/{clanTag}/capitalraidseasons` | Bearer JWT | не используется текущим probe | `endpoint_verified` |

## Подтверждённый wire-контракт текущих adapters

Имена и базовые типы ниже подтверждены официальным Swagger extract: `field_name_verified`, `field_type_verified`. Их наличие в конкретном response, requiredness и nullability не подтверждены, поэтому tolerant optional handling сохраняется.

| Fixture entity | Wire name candidate | Fixture type | Project handling | Official requiredness | UI use | Public policy |
|---|---|---|---|---|---|---|
| Clan | `tag` | string | требуется локальным нормализатором как natural key | `requiredness_unverified`, `nullability_unverified` | напрямую не выводится | internal only |
| Clan | `name` | string | требуется для понятного snapshot | `requiredness_unverified`, `nullability_unverified` | идентичность клана в будущем | только после решения |
| Clan | `clanLevel` | integer | optional project handling | `requiredness_unverified`, `nullability_unverified` | сейчас не используется | allowlist needed |
| Clan | `memberList` | `ClanMemberList` | missing трактуется как пустой список | `requiredness_unverified`, `nullability_unverified` | источник roster | raw internal |
| Clan member | `tag`, `name`, `role` | string, string, string enum | tag natural key; role optional project handling | `enum_values_unverified`, `requiredness_unverified`, `nullability_unverified` | name/role mapping | tag internal |
| Clan member | `townHallLevel` | integer | optional project handling | `requiredness_unverified`, `nullability_unverified` | `town_hall_level` | public allowlist |
| Clan member | `expLevel`, `clanRank`, `previousClanRank`, `donations`, `donationsReceived`, `trophies`, `builderBaseTrophies` | integer | optional internal snapshot fields | `requiredness_unverified`, `nullability_unverified` | none | internal only |
| War | `state`, `startTime`, `endTime`, `preparationStartTime` | string enum, string, string, string | state required only by project fixture | `enum_values_unverified`, `requiredness_unverified`, `nullability_unverified` | neutral summary | raw internal |
| War | `attacksPerMember` | integer | optional project handling | `requiredness_unverified`, `nullability_unverified` | derived `attacks_available` | aggregate only |
| War clan | `members`, `stars`, `attacks`, `destructionPercentage` | `ClanWarMemberList`, integer, integer, Float | normalization uses members; other fields remain deferred | `requiredness_unverified`, `nullability_unverified` | neutral aggregates only | raw internal |
| War member | `tag`, `name`, `townhallLevel`, `mapPosition`, `opponentAttacks` | string, string, integer, integer, integer | capitalization of `townhallLevel` is exact | `requiredness_unverified`, `nullability_unverified` | join/context | internal only |
| Attack | `attackerTag`, `defenderTag` | string | project handles defender as optional | `requiredness_unverified`, `nullability_unverified` | join only | internal only |
| Attack | `stars`, `destructionPercentage`, `order`, `duration` | integer | stars 0..3 is a project invariant; duration not normalized | `requiredness_unverified`, `nullability_unverified` | neutral aggregates only | internal only |
| War log | `clan`, `teamSize`, `attacksPerMember`, `battleModifier`, `opponent`, `endTime`, `result` | verified Swagger types in evidence | normalization intentionally deferred | enum/requiredness/nullability unverified | none | raw internal |
| Current war no-data state | exact response value unavailable | unknown | fixture intentionally deferred | `unverified` | future no-data state | neutral only |

`sourceTimestamp` в fixtures является project-owned provenance field. Значение state `fixture_completed` также принадлежит fixture-контракту и не заявляется как официальный enum. Это не утверждение о наличии таких wire-значений в официальном API.

## Mapping official-shaped fixture to UI

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

Новые `ClanMemberSnapshot` поля `exp_level`, `clan_rank`, `previous_clan_rank`, `donations`, `donations_received`, `trophies` и `builder_base_trophies` остаются internal-only и не входят в public roster allowlist.

## Следующий безопасный этап

Подготовленный CLI и safety contract описаны в [clan_roster_probe.md](clan_roster_probe.md). Официальный clan profile path подтверждён, но `--endpoint-template` остаётся обязательным и execute принимает только `/clans/{clan_tag}`. `--base-url` также остаётся обязательным без default; execute принимает только подтверждённое нормализованное значение `https://api.clashofclans.com/v1` со статусом `base_url_verified`.

Итоговый roster URL формируется как `https://api.clashofclans.com/v1/clans/{encoded_clan_tag}`. Официальный Swagger parameter `{clanTag}` соответствует внутреннему placeholder `{clan_tag}`; начальный `#` кодируется как `%23`.

Authenticated Swagger review и минимальные live probes завершены. Следующий безопасный этап:

1. отдельно подтвердить required/nullable, enum values, status codes и error mapping;
2. проверить первый разрешённый response без ослабления tolerant normalization;
3. утвердить natural key войны и правила snapshot history;
4. накапливать detailed war observations и не вводить ratings до достаточного объёма истории.

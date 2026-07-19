# Authenticated Clash API contract evidence

Проверено: `2026-07-19`.

Источник: authenticated official Swagger documentation Clash of Clans API. В проект не сохранялись token, JWT, cookies или session data. Безопасное представление подтверждённой схемы авторизации:

```text
Authorization: Bearer [REDACTED]
```

## Подтверждённые endpoints

```text
GET /clans/{clanTag}
GET /clans/{clanTag}/members
GET /clans/{clanTag}/currentwar
GET /clans/{clanTag}/warlog
GET /clans/{clanTag}/currentwar/leaguegroup
GET /clanwarleagues/wars/{warTag}
GET /clans/{clanTag}/capitalraidseasons
```

Текущий roster probe использует только `GET /clans/{clanTag}` и читает состав из `Clan.memberList`. Внутренний CLI placeholder `{clan_tag}` соответствует официальному параметру `{clanTag}`.

## Подтверждённые модели и wire fields

Статусы всех перечисленных имён и типов: `field_name_verified`, `field_type_verified`. Статусы обязательности и nullable отдельно не подтверждены.

- `Clan`: `memberList: ClanMemberList`, `warLeague: WarLeague`, `capitalLeague: CapitalLeague`, `tag: string`, `requiredTownhallLevel: integer`, `isFamilyFriendly: boolean`, `warWinStreak: integer`, `warWins: integer`, `warTies: integer`, `warLosses: integer`, `clanPoints: integer`, `chatLanguage: Language`, `requiredLeagueTier: LeagueTier`, `warFrequency: string enum`, `isWarLogPublic: boolean`, `clanBuilderBasePoints: integer`, `clanCapitalPoints: integer`, `requiredTrophies: integer`, `requiredBuilderBaseTrophies: integer`, `clanLevel: integer`, `labels: LabelList`, `name: string`, `location: Location`, `type: string enum`, `members: integer`, `description: string`, `clanCapital: ClanCapital`, `badgeUrls: object`.
- `ClanMember`: `league: League`, `leagueTier: LeagueTier`, `builderBaseLeague: BuilderBaseLeague`, `tag: string`, `name: string`, `role: string enum`, `townHallLevel: integer`, `expLevel: integer`, `clanRank: integer`, `previousClanRank: integer`, `donations: integer`, `donationsReceived: integer`, `trophies: integer`, `builderBaseTrophies: integer`, `playerHouse: PlayerHouse`.
- `ClanWar`: `clan: WarClan`, `teamSize: integer`, `attacksPerMember: integer`, `battleModifier: string enum`, `opponent: WarClan`, `startTime: string`, `state: string enum`, `endTime: string`, `preparationStartTime: string`.
- `WarClan`: `destructionPercentage: Float`, `tag: string`, `name: string`, `badgeUrls: object`, `clanLevel: integer`, `attacks: integer`, `stars: integer`, `expEarned: integer`, `members: ClanWarMemberList`.
- `ClanWarMember`: `tag: string`, `name: string`, `mapPosition: integer`, `townhallLevel: integer`, `opponentAttacks: integer`, `bestOpponentAttack: ClanWarAttack`, `attacks: ClanWarAttackList`.
- `ClanWarAttack`: `order: integer`, `attackerTag: string`, `defenderTag: string`, `stars: integer`, `destructionPercentage: integer`, `duration: integer`.
- `ClanWarLogEntry`: `clan: WarClan`, `teamSize: integer`, `attacksPerMember: integer`, `battleModifier: string enum`, `opponent: WarClan`, `endTime: string`, `result: string enum`.

`ClanMember.townHallLevel` и `ClanWarMember.townhallLevel` являются разными точными wire-field names.

## Неподтверждённые сведения

Следующие статусы остаются явными: `enum_values_unverified`, `requiredness_unverified`, `nullability_unverified`, `base_url_unverified`.

Также не подтверждены API version prefix, status codes, endpoint error mapping, официальный alphabet и length clan tag и конкретные rate limits. Эти сведения нельзя выводить из количества enum-вариантов или добавлять по памяти.

# Clan Games v1

Статус: source validation пройдён; Phase 1 player source client/normalizer реализована; operator-confirmed event registry, storage, collector, public projection и frontend остаются pending.

## Подтверждённый источник

Прямой официальный endpoint результатов Игр кланов не найден. Phase 1 использует официальный player endpoint:

```text
GET /v1/players/{playerTag}
```

В `achievements[]` ровно один элемент с точным именем `Games Champion` рассматривается как candidate lifetime cumulative source. Lookup не зависит от позиции в массиве, не использует fuzzy matching и не исправляет регистр имени автоматически.

Поле `value` должно быть integer, не `bool`, и не меньше нуля. Оно не является готовыми очками текущего события. Будущая Phase 6 сможет получить подтверждённые event points только как разницу между текущим cumulative value и валидным pre-event baseline.

Поле `target` валидируется как nonnegative integer и сохраняется только как source metadata. Это не cap текущего события, и derivation не должна зависеть от него. Поля `stars`, `info` и `completionInfo` проверяются как часть подтверждённой live-схемы, но в normalized authority не сохраняются, потому что не нужны будущей delta/storage semantics.

## Production APIs Phase 1

Модуль `src/clan_analytics/api/clan_games.py` предоставляет:

```python
normalize_games_champion_profile(
    profile,
    *,
    player_tag_internal,
    observed_at_utc,
) -> GamesChampionSnapshot

fetch_games_champion(
    player_tag_internal,
    *,
    token,
    timeout_seconds=15,
    transport=None,
    clock=None,
) -> tuple[GamesChampionSnapshot, GamesChampionSafeResult]
```

Network и pure normalization разделены. Default client переиспользует существующий `UrllibTransport`, официальный base URL, Bearer handling, запрет redirect/retry, timeout, лимит ответа, origin/content-type проверки, JSON parsing и token-echo guard.

Один вызов `fetch_games_champion` выполняет не больше одного HTTP request. Retry, batch, concurrency и event lifecycle не принадлежат Phase 1 и остаются ответственностью будущего bounded collector.

## Внутренняя модель

`GamesChampionSnapshot` является immutable internal-only dataclass:

- `player_tag_internal` – стабильная приватная identity, скрытая из `repr`;
- `value` – lifetime cumulative candidate;
- `target` – achievement progression metadata, не event cap;
- `observed_at_utc` – fixed-width UTC timestamp `YYYY-MM-DDTHH:MM:SS.ffffffZ`;
- `source_kind = official_player_profile`;
- `schema_version = 1`;
- `normalization_version = games_champion_v1`.

Network boundary фиксирует response-received time. Pure normalizer принимает explicit timezone-aware `datetime`, переводит его в UTC и отклоняет naive timestamps.

## Error taxonomy

| Result code | Значение |
|---|---|
| `success` | Response и `Games Champion` нормализованы. |
| `api_http_403` | Systemic auth/VPN candidate; safe hint `enable_approved_vpn`. |
| `api_http_other` | Другой HTTP status; переиспользует terminology collection health. |
| `api_transport_failure` | Ответ не получен из-за transport failure. |
| `timeout` | Истёк bounded request timeout. |
| `invalid_json` | Ответ не является валидным UTF-8 JSON. |
| `invalid_player_schema` | Root, origin, content type, identity или achievements collection нарушают контракт. |
| `games_champion_missing` | Exact achievement отсутствует. |
| `games_champion_invalid` | Matching achievement имеет невалидные required fields. |
| `unexpected_error` | Непредвиденный безопасно редактированный сбой Phase 1 boundary. |

`api_http_other` и `api_transport_failure` выбраны вместо новых синонимов `api_http_error` и `transport_error`, поскольку это уже используемые project result codes. Batch-stop для 403 будет реализован только в Phase 5; Phase 1 уже возвращает совместимый code и operator hint.

## Privacy и persistence

Raw player profile существует только в памяти до normalization. Модуль не имеет output path, debug dump или filesystem write surface. Он не пишет raw response в `runs`, `local`, Git или public files.

Safe result содержит только operational поля: status, result code, observation time, duration, HTTP status, validation statuses, normalization version и optional operator hint. В нём отсутствуют player tag, player name, raw profile, другие achievements, token и Authorization.

`player_tag_internal` остаётся только во внутреннем snapshot. Он не должен передаваться в safe logger или public serializer.

## Что ещё не реализовано

- operator-confirmed timezone-aware event registry с official source URL;
- отдельная local SQLite authority;
- event-only bounded collector;
- health aggregation и partial batch;
- production cumulative delta model;
- public projection и frontend;
- real-event baseline/intermediate/final validation.

Future cadence recommendation остаётся: только во время подтверждённого event каждые 6 часов, обязательный pre-event baseline и post-event final scan. Recurring dates и cap нельзя угадывать или hardcode.

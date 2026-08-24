# Clan Games v1

Статус: source validation пройдён; Phase 1 player source client/normalizer и Phase 2 operator-confirmed event registry реализованы; observation storage, collector, public projection и frontend остаются pending.

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

## Local event registry Phase 2

Event registry хранится как local operational/domain authority по логическому пути:

```text
data/clan_games/event_registry.v1.json
```

Фактический production path в этом workspace: `D:\coc\data\clan_games\event_registry.v1.json`. Он находится вне Git, Pages и Codebase Memory, сохраняется между запусками и не создаёт dirty-Git blocker. Import модуля, player client и hourly updater не создают registry автоматически. В Phase 2 production registry и production event record не создавались: для этого требуется отдельное explicit operator action с подтверждёнными boundaries и official source.

Schema v1 имеет строгий вид:

```json
{
  "schema_version": 1,
  "events": [
    {
      "event_id": "fictional-event",
      "start_at_utc": "2026-09-10T06:00:00.000000Z",
      "end_at_utc": "2026-09-16T06:00:00.000000Z",
      "official_source_url": "https://supercell.com/fictional-evidence/",
      "confirmed_at_utc": "2026-08-20T12:00:00.000000Z"
    }
  ]
}
```

Пример только показывает schema и не является production event или предположением о датах. Registry не содержит cap, recurrence, mutable `active`, player identity или points.

`event_id` задаётся оператором и является immutable identity. Допускаются 1–64 ASCII-символа: lowercase letters, digits, `-` и `_`; первый символ должен быть буквой или цифрой. ID не выводится из URL и не обязан иметь календарный формат.

Все timestamps принимаются только с timezone, нормализуются в fixed-width UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`; naive timestamps отклоняются. Обязательно `start_at_utc < end_at_utc`. Local Moscow timezone и recurring monthly dates не зашиты.

Source research использовал query-free официальные страницы на `supercell.com`, поэтому v1 allowlist допускает только exact host `supercell.com` по HTTPS без userinfo, custom port, query или fragment. `www.supercell.com`, arbitrary domains, localhost и IP literals fail closed. Registry не fetch-ит URL при записи: оператор подтверждает provenance, а transient web availability не влияет на локальную регистрацию.

Events сериализуются детерминированно по `start_at_utc`, `end_at_utc`, `event_id`. IDs должны быть unique; overlapping windows запрещены. Касание boundaries допустимо. Обычный `register` идемпотентен только для exact canonical retry; тот же ID с другими полями является conflict.

Correction выполняется только отдельным explicit replace. Existing ID обязателен, candidate полностью валидируется, overlap проверяется до записи, старый registry сохраняется byte-for-byte как validated local backup в `data/clan_games/backups/event_registry/`, затем candidate публикуется atomic replace. Backup не перезаписывается. Generic delete отсутствует.

Статус не хранится. Он вычисляется от timezone-aware `as_of`:

- `upcoming`, если `as_of < start_at_utc`;
- `active`, если `start_at_utc <= as_of < end_at_utc`;
- `ended`, если `as_of >= end_at_utc`.

Ended records сохраняются как provenance/history operator confirmations. `get_upcoming_event` возвращает ближайший event со `start_at_utc > as_of`; отсутствие active/upcoming event возвращает `None`, а не ошибку.

## Operator CLI Phase 2

CLI: `scripts/clan_games/manage_event_registry.py`.

Commands:

- `init` – explicit atomic initialization пустой schema v1;
- `validate` – read-only strict validation;
- `list` – deterministic event metadata;
- `status [--as-of ...]` – derived active/upcoming/ended state;
- `register --event-id ... --start ... --end ... --official-source-url ...`;
- `replace ... --confirm-replace` – explicit correction с validated backup.

Production mode использует только фиксированный logical path. Arbitrary production `--path` отсутствует. `--test-registry` разрешён только для файла `event_registry.v1.json` внутри системного temp и предназначен для offline tests. CLI не выводит absolute registry path; для production показывается `data/clan_games/event_registry.v1.json`.

Future collector contract:

```python
registry = load_event_registry(path)
event = get_active_event(registry, now)
```

Если `event is None`, будущий collector должен вернуть `no_active_event` без player requests. Если event активен, отдельная будущая фаза сможет выполнить bounded scan. Registry уже предоставляет точные start/end boundaries, но не хранит `baseline_done` или `final_done`: это состояние будущего observation storage/collector.

## Что ещё не реализовано

- отдельная local SQLite authority;
- event-only bounded collector;
- health aggregation и partial batch;
- production cumulative delta model;
- public projection и frontend;
- real-event baseline/intermediate/final validation.

Future cadence recommendation остаётся: только во время подтверждённого event каждые 6 часов, обязательный pre-event baseline и post-event final scan. Recurring dates и cap нельзя угадывать или hardcode.

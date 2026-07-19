# Безопасный probe состава клана

Статус: код подготовлен и проверен только offline. Реальный API request не выполнялся.

## Назначение и границы

`scripts/api/probe_clan_roster.py` готовит один явный read-only request к официально подтверждённому `GET /clans/{clanTag}` и получает roster из `Clan.memberList`. Probe использует только Python standard library, не выполняет retry, pagination, fallback, запросы профилей игроков или другие endpoints.

Официальное имя path-параметра Swagger: `{clanTag}`. Внутренний project template использует `{clan_tag}`, поэтому разрешённый execute template равен `/clans/{clan_tag}` и после URL encoding формирует официальный path. Подтверждённый origin: `https://api.clashofclans.com`; version prefix: `/v1`; нормализованный base URL: `https://api.clashofclans.com/v1`. Execute дополнительно требует `--confirm-api-contract`; флаг не заменяет разрешение на сеть, token, clan tag или сам запуск.

Итоговый roster URL pattern: `https://api.clashofclans.com/v1/clans/{encoded_clan_tag}`. Начальный `#` в tag кодируется как `%23`. Реальный clan tag в repository не хранится.

Отдельный `GET /clans/{clanTag}/members` также официально подтверждён, но текущий probe его не вызывает: второй request нарушил бы зафиксированную single-request policy.

## CLI contract

Обязательные параметры:

- `--clan-tag`: project-validated tag; пробелы удаляются по краям, буквы переводятся в верхний регистр, отсутствующий `#` добавляется;
- `--token-env`: имя environment variable, но не значение token;
- `--output-dir`: абсолютный run-specific path внутри `D:\coc\runs\api_probe`;
- `--timeout-seconds`: значение от 1 до 60;
- `--base-url`: обязательный параметр без default; execute принимает только `https://api.clashofclans.com/v1`;
- `--endpoint-template`: обязательный project path с одним `{clan_tag}`; execute принимает только `/clans/{clan_tag}`.

Режимы и safety flags:

- `--dry-run`: валидирует plan без чтения environment, сети и файлов output;
- `--confirm-api-contract`: обязателен только для execute и подтверждает ручную проверку contract;
- `--overwrite`: явно разрешает транзакционную замену существующего output directory; без флага существующий каталог блокирует probe до network request.

Синтаксическая проверка tag не утверждает официальный alphabet или length. Эти ограничения остаются `unverified` до Swagger review. В URL символ `#` кодируется как `%23`.

Execute отклоняет base URL, отличный от `https://api.clashofclans.com/v1`, endpoint template, отличный от `/clans/{clan_tag}`, а также placeholder/unverified contract values даже при `--confirm-api-contract`. Все contract guards выполняются до чтения environment и network. Dry-run сохраняет возможность проверить очевидный `UNVERIFIED` placeholder без environment, сети и output.

## Dry-run

Следующая команда безопасна: host, endpoint и tag являются очевидными placeholders, environment не читается, сеть и запись не выполняются.

```powershell
python D:\coc\repo\scripts\api\probe_clan_roster.py `
  --clan-tag '#DEMOCLAN' `
  --token-env COC_API_TOKEN `
  --output-dir 'D:\coc\runs\api_probe\clan_roster\dry-run-verification' `
  --timeout-seconds 15 `
  --base-url 'https://api-placeholder.clashofclans.com' `
  --endpoint-template '/UNVERIFIED/clans/{clan_tag}' `
  --dry-run
```

Dry-run выводит method `GET`, один planned request, redacted token value, target host, endpoint template, encoded request URL, output path, timeout и `Network executed: no`.

## Execute template

DO NOT RUN UNTIL:

- token created and stored only in the current local process environment;
- public IP allowlisted;
- clan tag approved;
- exact output path approved;
- explicit execution permission received.

```powershell
$env:COC_API_TOKEN = '<set locally, do not paste into docs>'

python D:\coc\repo\scripts\api\probe_clan_roster.py `
  --clan-tag '#APPROVED_PLACEHOLDER' `
  --token-env COC_API_TOKEN `
  --output-dir 'D:\coc\runs\api_probe\clan_roster\<timestamp>' `
  --timeout-seconds 15 `
  --base-url 'https://api.clashofclans.com/v1' `
  --endpoint-template '/clans/{clan_tag}' `
  --confirm-api-contract
```

Token передаётся только через имя environment variable. Значение не принимается в CLI, не выводится, не включается в metadata и не записывается. Официальный Swagger review подтвердил Bearer JWT header; клиент сохраняет `Authorization: Bearer <token>`. Authorization header не входит в diagnostics. Environment не читается в dry-run. Настоящий execute-mode всё ещё не запускался.

## Request policy

- ровно один GET request;
- User-Agent `ClashClanAnalytics-Probe/0.1` без персональных данных;
- timeout обязателен;
- redirect не выполняется; response с другим final host также отклоняется;
- response status должен быть `200`;
- Content-Type должен быть JSON;
- maximum response size: 2 MiB;
- body должен быть UTF-8 JSON object;
- token echo в response блокирует сохранение;
- любая ошибка даёт ненулевой exit code без retry.

## Output contract

Будущий успешный execute создаёт только локальный каталог под `D:\coc\runs\api_probe`. Repository и `site` отклоняются. Реальный output в этой задаче не создавался.

| File | Boundary | Content |
|---|---|---|
| `raw_clan_response.json` | local internal | validated raw response bytes |
| `probe_metadata.json` | local technical | timestamp, method, host, endpoint template, request URL, timeout, status, content type, byte count, zero redirects |
| `normalized_clan.json` | local internal | existing `normalize_clan` result including internal player tags and provenance |
| `public_roster_preview.json` | local safe preview | existing `build_public_roster` plus `build_composition_summary`, without player tags or private fields |

Raw response, normalized output and metadata remain outside Git and `site`. Public preview is not automatically published and requires a separate allowlist review before any future copy to `site/data`.

### Транзакционная публикация output

Четыре файла публикуются как единый application-level run. Сначала JSON-представления формируются в памяти, затем все файлы эксклюзивно создаются во временном staging-каталоге рядом с target на том же filesystem. Каждый файл полностью записывается, получает `flush()` и `os.fsync()`, после чего закрывается.

Перед публикацией staging повторно проверяется: разрешены ровно четыре ожидаемых обычных файла, каждый файл должен содержать JSON, raw response должен соответствовать уже проверенному object, public preview снова проходит проверку private fields, а metadata должна фиксировать один request и ноль redirects. Повторная normalization не выполняется.

Target не появляется до полной подготовки и проверки staging. При ошибке подготовки staging удаляется, а target остаётся отсутствующим. Если target уже существует без `--overwrite`, probe отказывает до network request и не создаёт staging.

При `--overwrite` полностью подготовленный staging создаётся до изменения старого target. Затем старый target переименовывается во временный sibling backup, staging переименовывается в target, а backup удаляется только после успешного переключения. Если финальное переименование staging завершается ошибкой, старый target восстанавливается из backup, staging очищается, операция завершается с ошибкой и второй request не выполняется.

Имена staging и backup генерируются стандартной библиотекой, не содержат token, clan tag или другие входные значения и не считаются успешными runs. Рекурсивное удаление применяется только к созданным probe sibling-путям после строгой проверки их имени и расположения.

Если новый target уже опубликован, но backup удалить не удалось, операция возвращает ненулевой статус, сохраняет валидный новый target и оставляет backup для ручной проверки. Если rollback не удался, ошибка явно сообщает `output recovery failed`, backup сохраняется, а результат не считается успешным. Ошибка cleanup добавляется к исходной безопасной причине и не раскрывает содержимое файлов или credentials.

Эта модель обеспечивает application-level all-or-nothing publication при обычных обработанных ошибках. Она не обещает полную crash-consistency при отключении питания или аварии операционной системы на любом этапе. Настоящий execute-mode по-прежнему не запускался.

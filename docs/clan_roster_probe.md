# Безопасный probe состава клана

Статус: код подготовлен и проверен только offline. Реальный API request не выполнялся.

## Назначение и границы

`scripts/api/probe_clan_roster.py` готовит один явный read-only GET request к будущему подтверждённому endpoint профиля клана. Probe использует только Python standard library, не выполняет retry, pagination, fallback, запросы профилей игроков или другие endpoints.

Точный API base URL и endpoint path не подтверждены доступной публичной Swagger-схемой. Поэтому они не имеют defaults, передаются отдельными параметрами и в execute-mode дополнительно требуют флаг `--confirm-api-contract`. Этот флаг означает только ручную проверку base URL и endpoint перед конкретным запуском; он не заменяет разрешение на сеть, API token, clan tag или сам запуск.

## CLI contract

Обязательные параметры:

- `--clan-tag`: project-validated tag; пробелы удаляются по краям, буквы переводятся в верхний регистр, отсутствующий `#` добавляется;
- `--token-env`: имя environment variable, но не значение token;
- `--output-dir`: абсолютный run-specific path внутри `D:\coc\runs\api_probe`;
- `--timeout-seconds`: значение от 1 до 60;
- `--base-url`: вручную подтверждённый HTTPS origin в домене `clashofclans.com`;
- `--endpoint-template`: вручную подтверждённый absolute path с одним `{clan_tag}`.

Режимы и safety flags:

- `--dry-run`: валидирует plan без чтения environment, сети и файлов output;
- `--confirm-api-contract`: обязателен только для execute и подтверждает ручную проверку contract;
- `--overwrite`: явно разрешает замену четырёх ожидаемых файлов в существующем output directory; без флага существующий каталог блокирует probe.

Синтаксическая проверка tag не утверждает официальный alphabet или length. Эти ограничения остаются `unverified` до Swagger review. В URL символ `#` кодируется как `%23`.

Execute дополнительно отклоняет значения, содержащие `placeholder` или `UNVERIFIED`, даже если передан `--confirm-api-contract`.

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

- official base URL and endpoint confirmed;
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
  --base-url '<confirmed official HTTPS origin>' `
  --endpoint-template '<confirmed absolute path containing {clan_tag}>' `
  --confirm-api-contract
```

Token передаётся только через имя environment variable. Значение не принимается в CLI, не выводится, не включается в metadata и не записывается. Authorization header не входит в diagnostics. Environment не читается в dry-run.

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

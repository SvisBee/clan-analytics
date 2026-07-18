# Локальные skills проекта

Repository-scoped skills находятся в `.agents/skills`, поэтому Codex обнаруживает их в контексте этого repository, а не как глобальные пользовательские настройки. Они дополняют, но не заменяют `AGENTS.md`, не содержат scripts или dependencies и сами по себе не разрешают изменение файлов, запуск команд, сеть, Git, API или публикацию.

## Каталог

| Skill | Назначение | Основные non-goals |
|---|---|---|
| `coc-design-engineering` | UI review и планирование для HTML, CSS и vanilla JavaScript, responsive, accessibility и motion | Данные, метрики, API, архитектура и security policy |
| `coc-review-and-qa` | Проверка diff, acceptance criteria, regression risk, public export и завершённости | Автоматическое исправление findings и неразрешённые исполняемые проверки |
| `coc-debugging` | Воспроизводимая диагностика, локализация и root cause | Guess-and-check, автоматические retries, production и массовый refactoring |
| `coc-code-simplification` | Удаление понятой лишней сложности с сохранением поведения | Feature changes, public contracts, data model и business logic |

Явный вызов выполняется именем `$coc-design-engineering`, `$coc-review-and-qa`, `$coc-debugging` или `$coc-code-simplification`. Codex также может выбрать skill неявно, когда запрос соответствует его `description`; это не расширяет разрешения задачи.

## Порядок применения

1. Сначала прочитать `AGENTS.md`, задачу и релевантную проектную документацию.
2. Выбрать только skill, соответствующий текущей работе; не применять все skills автоматически.
3. Использовать текущие файлы проекта как источник истины. Codebase Memory может быть устаревшим и годится только как дополнительный read-only источник.
4. При конфликте следовать `AGENTS.md` и более узким ограничениям пользователя.
5. До любой исполняемой проверки сверить отдельное разрешение. Если его нет, описать проверку как не выполненную.
6. Сохранять минимальный diff, переиспользовать существующие решения и не менять unrelated files.

Сведения об адаптированных upstream-источниках и лицензиях находятся в [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Обновление

Upstream-изменения не применяются автоматически. Перед обновлением нужно снова зафиксировать актуальный repository, revision/release, license и source paths, просмотреть upstream diff, отобрать только релевантные правила, сохранить project-specific ограничения и обновить third-party notice. Установка upstream skill, запуск его scripts или перенос глобальной конфигурации не являются частью обновления.

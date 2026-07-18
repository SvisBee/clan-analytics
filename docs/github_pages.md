# GitHub Pages

Публичный repository проекта: [SvisBee/clan-analytics](https://github.com/SvisBee/clan-analytics). Repository имеет visibility `public`.

Подтверждённый адрес сайта: [https://svisbee.github.io/clan-analytics/](https://svisbee.github.io/clan-analytics/).

## Схема публикации

- Workflow: `.github/workflows/pages.yml`.
- Source artifact: только каталог `site`.
- Deployment запускается автоматически после push в `main`, если изменён `site/**` или сам workflow.
- Workflow можно запустить вручную через `workflow_dispatch`, но только после отдельного разрешения.
- Текущая опубликованная версия работает на GitHub Pages независимо от локального компьютера и остаётся доступной между обновлениями.
- Локальные изменения не публикуются до commit и разрешённого push.
- После разрешённого push изменений сайта новая версия разворачивается автоматически.

Каталоги `docs`, `scripts`, `tests`, локальные `data`, `runs`, `local` и Obsidian не входят в Pages artifact. В repository и artifact нет внутренних данных руководства. Clash of Clans API, backend и реальные игровые данные не подключены.

## Проверка и управление

Последний deployment проверяется на вкладке Actions repository в workflow `Deploy site to GitHub Pages` и на странице Settings > Pages. Успешный run должен относиться к нужному commit ветки `main`, завершаться с conclusion `success` и возвращать указанный Pages URL.

Для ручного запуска после отдельного разрешения используется действие Run workflow на вкладке Actions. Для временного отключения Pages нужно удалить сайт через Settings > Pages либо официальный Pages API; это отдельное внешнее изменение и требует отдельного разрешения.

Custom domain не настроен. HTTPS включён для стандартного домена `github.io`.

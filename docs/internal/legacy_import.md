# Legacy Import

`import-legacy` — внутренняя Flask-команда для миграции старых данных в новую web-only БД.

## Назначение

Команда нужна только для dev/admin-задач и не является частью пользовательского продукта.

## Запуск

```bash
flask --app ies_bot_skeleton.web.app:create_app import-legacy --session <session_id>
```

Дополнительные параметры:
- `--state <path>`
- `--lots-dir <path>`

## Источники данных

По умолчанию используются файлы из `ies_bot_skeleton/resources/legacy_import/`.

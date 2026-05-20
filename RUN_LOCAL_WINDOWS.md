# Запуск бота на своём ПК (вместо Railway)

Если на Railway всё уходит в **Спам**, а в другом софте с **теми же** почтами и прокси — лучше, проверь бота **локально на Windows**. SMTP тогда идёт с твоего ПК через SOCKS (как Thunderbird), а не из контейнера Railway.

## Шаги

1. Установи Python 3.11+ и зависимости:
   ```powershell
   cd C:\Users\user\Desktop\finland-bot
   pip install -r requirements.txt
   ```

2. Создай `.env` (скопируй с Railway Variables):
   - `BOT_TOKEN`
   - `DATABASE_URL` (тот же Postgres с Railway — бот увидит те же аккаунты/прокси)
   - `VALIDEMAIL_API_KEY_1` и т.д.

3. **Останови** деплой на Railway (или смени `BOT_TOKEN` на тестовый), чтобы не было двух процессов на одном токене.

4. Запуск:
   ```powershell
   python bot.py
   ```

5. В Telegram: **Тест маил** на свой ящик → смотри Inbox / Спам.

- **Локально Inbox, на Railway спам** → проблема окружения Railway (EHLO/контейнер). Дальше: VPS или только локальный запуск.
- **Везде спам** → дело не в Railway, а в ящиках/прокси/шаблоне (как в любом софте с холодной рассылкой).

## Переменные рассылки (по умолчанию включены)

| Переменная | По умолчанию | Смысл |
|------------|--------------|--------|
| `MAILING_PLAIN_ONLY` | 1 | Только plain text |
| `MAILING_MINIMAL_HEADERS` | 1 | From=email, без Reply-To |
| `MAILING_STRIP_LINK` | 1 | Без {{LINK}} в письме |
| `MAILING_FIXED_PRESET` | 1 | Один пресет, не random |

Отключить: `MAILING_STRIP_LINK=0` и т.д. в `.env`.

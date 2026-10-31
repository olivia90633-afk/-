# 上帝 Trading Signals — Stage 1

Stage 1 includes Telegram onboarding, automatic user ID registration, an admin-only broadcast command, and a styled market-selection menu.

## Railway variables
- `BOT_TOKEN`: token from @BotFather
- `ADMIN_IDS`: your Telegram numeric user ID

Do not put secrets in GitHub.

## Start command
`python main.py`

## Commands
- `/start`
- `/help`
- `/status`
- `/admin`
- `/broadcast Your message`

## User registry
Users are saved in SQLite when they send `/start`. Broadcast sends to users who have started the bot and have not been marked blocked. Telegram does not let bots message users who have never started the bot, and users can block the bot.

## Important deployment note
For durable production storage, attach a Railway persistent volume and set `DATABASE_PATH` to a file path inside that volume, for example `/data/shangdi_users.db`. Without persistent storage, a redeploy or container replacement can erase the SQLite database. A later stage will migrate the project to PostgreSQL.

## Current limitations
This stage does not generate live signals yet. The buttons are menu scaffolding. Automatic signal scheduling, market feeds, accuracy tracking, subscriptions, AI analysis, backtesting and alerts are added in later stages.

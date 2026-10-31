from market_data import get_price
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from config import BOT_TOKEN, ADMIN_IDS, validate_config
from database.db import init_db, save_user, get_all_user_ids, count_users

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("shangdi")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ New Signal", callback_data="new_signal")],
        [
            InlineKeyboardButton("₿ Crypto", callback_data="market_crypto"),
            InlineKeyboardButton("📈 Forex", callback_data="market_forex"),
        ],
        [
            InlineKeyboardButton("📊 Indices", callback_data="market_indices"),
            InlineKeyboardButton("💹 Binary Options", callback_data="market_binary"),
        ],
        [
            InlineKeyboardButton("📚 Signal History", callback_data="history"),
            InlineKeyboardButton("👤 My Account", callback_data="account"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    # Every user who starts the bot is saved for future admin broadcasts.
    save_user(
        telegram_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )

    role = "ADMINISTRATOR" if is_admin(user.id) else "TRADER"
    await update.message.reply_text(
        "╔════════════════════╗\n"
        "       上帝  |  TRADING\n"
        "╚════════════════════╝\n\n"
        f"Welcome, {user.first_name}!\n"
        f"Account: {role}\n\n"
        "Your trading signal assistant is ready.\n"
        "Automatic signal delivery will work without pressing any button.\n\n"
        "Choose a market below whenever you want to explore it.",
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "✦ 上帝 — COMMAND CENTER ✦\n\n"
        "/start — Open the main menu\n"
        "/help — Show commands\n"
        "/status — Check bot status\n"
        "/admin — Administrator panel\n"
        "/broadcast Your message — Message all registered users\n\n"
        "Automatic signals will be delivered when valid setups are available. "
        "No signal is guaranteed to be profitable."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "✦ 上帝 SYSTEM STATUS ✦\n\n"
        "🟢 Bot service: ONLINE\n"
        "🟡 Signal engine: Coming in a later stage\n"
        "🟡 Automatic scheduler: Coming in a later stage\n"
        "👥 Registered users: " + str(count_users())
    )


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    user = update.effective_user
    if user is None or not is_admin(user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    await update.message.reply_text(
        "✦ 上帝 ADMIN COMMAND CENTER ✦\n\n"
        f"👥 Registered users: {count_users()}\n"
        "📣 Broadcast: /broadcast Your message\n"
        "🔐 Admin access: VERIFIED"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    user = update.effective_user
    if user is None or not is_admin(user.id):
        await update.message.reply_text("⛔ Only the administrator can broadcast.")
        return

    message_text = " ".join(context.args).strip()
    if not message_text:
        await update.message.reply_text(
            "Usage:\n/broadcast Your announcement here"
        )
        return

    user_ids = get_all_user_ids()
    sent = 0
    failed = 0
    for telegram_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text="✦ 上帝 OFFICIAL ANNOUNCEMENT ✦\n\n" + message_text,
            )
            sent += 1
        except Exception as exc:
            logger.warning("Broadcast failed for %s: %s", telegram_id, exc)
            failed += 1

    await update.message.reply_text(
        "📣 BROADCAST REPORT\n\n"
        f"👥 Registered: {len(user_ids)}\n"
        f"✅ Sent: {sent}\n"
        f"⚠️ Failed: {failed}"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    labels = {
        "market_crypto": "CRYPTO",
        "market_forex": "FOREX",
        "market_indices": "INDICES",
        "market_binary": "BINARY OPTIONS",
    }
    if query.data == "new_signal":
        await query.edit_message_text(
            "⚡ NEW SIGNAL\n\nChoose the market you want to view:",
            reply_markup=main_keyboard(),
        )
    elif query.data in labels:
        await query.edit_message_text(
            f"📊 {labels[query.data]} SELECTED\n\n"
            "Market selection saved for this request. "
            "Live signal generation will be connected in a later stage.\n\n"
            "Automatic signal delivery will not depend on this button.",
            reply_markup=main_keyboard(),
        )
    elif query.data == "history":
        await query.edit_message_text(
            "📚 SIGNAL HISTORY\n\nHistory tracking will be added in a later stage.",
            reply_markup=main_keyboard(),
        )
    elif query.data == "account":
        await query.edit_message_text(
            "👤 MY ACCOUNT\n\nYour Telegram account is registered with 上帝.",
            reply_markup=main_keyboard(),
        )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is not None:
        await update.message.reply_text("Command not recognized. Send /help.")


async def post_init(application: Application) -> None:
    me = await application.bot.get_me()
    logger.info("Connected as @%s", me.username)


def main() -> None:
    validate_config()
    init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbol = "BTC"

    if context.args:
        symbol = context.args[0].upper()

    try:
        data = await get_price(symbol)

        await update.message.reply_text(
            f"𝑮𝑶𝑫 𝑪𝑹𝒀𝑷𝑻𝑶 𝑫𝑬𝑺𝑲\n\n"
            f"Coin: {data['symbol']}\n"
            f"Pair: {data['pair']}\n"
            f"Live Price: ${data['price']:,.4f}\n\n"
            f"Source: Binance"
        )

    except Exception as error:
        logger.exception("Price request failed")
        await update.message.reply_text(
            "Unable to fetch the price right now. Please try again."
        )
        
    logger.info("Starting 上帝...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

import asyncio
from datetime import timedelta
import logging
import math
import sqlite3
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_IDS, DATABASE_PATH, validate_config
from market_data import get_price, get_candles


# =========================================================
#  GOD TRADING BOT — CONFIGURATION
# =========================================================

BOT_NAME = "上帝"
BOT_TITLE = "𝑮𝑶𝑫 𝑻𝑹𝑨𝑫𝑰𝑵𝑮"
BOT_SUBTITLE = "𝑨𝑰 𝑴𝑨𝑹𝑲𝑬𝑻 𝑰𝑵𝑻𝑬𝑳𝑳𝑰𝑮𝑬𝑵𝑪𝑬"

CRYPTO_COINS = ["BTC", "ETH", "BNB", "SOL"]
TIMEFRAMES = ["15m", "1h", "4h"]

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("shangdi")


# =========================================================
#  DATABASE
# =========================================================

def db_connection():
    return sqlite3.connect(DATABASE_PATH)


def init_database():
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT,
                last_seen TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                symbol TEXT,
                timeframe TEXT,
                direction TEXT,
                entry REAL,
                stop_loss REAL,
                take_profit REAL,
                created_at TEXT
            )
        """)


def register_user(user):
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        conn.execute("""
            INSERT INTO bot_users
                (user_id, username, first_name, joined_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            now,
            now,
        ))


def get_user_count():
    with db_connection() as conn:
        result = conn.execute(
            "SELECT COUNT(*) FROM bot_users"
        ).fetchone()
    return result[0]


def get_all_user_ids():
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT user_id FROM bot_users"
        ).fetchall()
    return [row[0] for row in rows]


def save_signal(user_id, symbol, timeframe, direction,
                entry, stop_loss, take_profit):
    with db_connection() as conn:
        conn.execute("""
            INSERT INTO signal_history (
                user_id, symbol, timeframe, direction,
                entry, stop_loss, take_profit, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            symbol,
            timeframe,
            direction,
            entry,
            stop_loss,
            take_profit,
            datetime.now(timezone.utc).isoformat(),
        ))

def has_recent_signal(symbol, timeframe, minutes=15):
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(minutes=minutes)
    ).isoformat()

    with db_connection() as conn:
        result = conn.execute("""
            SELECT id
            FROM signal_history
            WHERE symbol = ?
              AND timeframe = ?
              AND direction IN ('BUY', 'SELL')
              AND created_at >= ?
            LIMIT 1
        """, (symbol, timeframe, cutoff)).fetchone()

    return result is not None
# =========================================================
#  MATHEMATICAL INDICATORS
# =========================================================

def calculate_ema(values, period):
    if len(values) < period:
        raise ValueError("Not enough candles for EMA.")

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for value in values[period:]:
        ema = (value * multiplier) + (ema * (1 - multiplier))

    return ema


def calculate_rsi(values, period=14):
    if len(values) < period + 1:
        raise ValueError("Not enough candles for RSI.")

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [abs(min(change, 0)) for change in changes]

    average_gain = sum(gains[-period:]) / period
    average_loss = sum(losses[-period:]) / period

    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0

    relative_strength = average_gain / average_loss

    return 100 - (100 / (1 + relative_strength))


def calculate_macd(values):
    if len(values) < 35:
        raise ValueError("Not enough candles for MACD.")

    ema12 = calculate_ema(values, 12)
    ema26 = calculate_ema(values, 26)

    macd_line = ema12 - ema26

    # Calculate the MACD history for its signal line.
    macd_history = []

    for index in range(26, len(values) + 1):
        section = values[:index]

        if len(section) >= 26:
            macd_history.append(
                calculate_ema(section, 12)
                - calculate_ema(section, 26)
            )

    signal_line = calculate_ema(macd_history, 9)

    return macd_line, signal_line


def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        raise ValueError("Not enough candles for ATR.")

    true_ranges = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        true_range = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"]),
        )

        true_ranges.append(true_range)

    return sum(true_ranges[-period:]) / period


def calculate_support_resistance(candles, period=20):
    recent = candles[-period:]

    support = min(candle["low"] for candle in recent)
    resistance = max(candle["high"] for candle in recent)

    return support, resistance


# =========================================================
#  MARKET ANALYSIS ENGINE
# =========================================================

async def analyse_market(symbol, timeframe):
    candles = await get_candles(
        symbol=symbol,
        interval=timeframe,
        limit=100,
    )

    if len(candles) < 50:
        raise ValueError("Not enough market data.")

    closes = [candle["close"] for candle in candles]

    current_price = closes[-1]

    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)

    rsi = calculate_rsi(closes, 14)

    macd, macd_signal = calculate_macd(closes)

    atr = calculate_atr(candles, 14)

    support, resistance = calculate_support_resistance(
        candles,
        20,
    )

    buy_score = 0
    sell_score = 0

    # EMA trend calculation.
    if ema9 > ema21:
        buy_score += 1
    elif ema9 < ema21:
        sell_score += 1

    # MACD momentum calculation.
    if macd > macd_signal:
        buy_score += 1
    elif macd < macd_signal:
        sell_score += 1

    # RSI momentum calculation.
    if 50 <= rsi <= 70:
        buy_score += 1
    elif 30 <= rsi < 50:
        sell_score += 1

    # Price position relative to EMA.
    if current_price > ema9:
        buy_score += 1
    elif current_price < ema9:
        sell_score += 1

    # Require agreement between multiple indicators.
    direction = "NO SIGNAL"

    if buy_score >= 3 and buy_score > sell_score:
        direction = "BUY"

    elif sell_score >= 3 and sell_score > buy_score:
        direction = "SELL"

    entry = current_price

    stop_loss = None
    take_profit = None

    if direction == "BUY":
        stop_loss = entry - (1.5 * atr)
        take_profit = entry + (3 * atr)

    elif direction == "SELL":
        stop_loss = entry + (1.5 * atr)
        take_profit = entry - (3 * atr)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "atr": atr,
        "support": support,
        "resistance": resistance,
        "buy_score": buy_score,
        "sell_score": sell_score,
    }


# =========================================================
#  TELEGRAM KEYBOARD
# =========================================================

def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                "📊 𝑵𝑬𝑾 𝑺𝑰𝑮𝑵𝑨𝑳",
                callback_data="new_signal",
            ),
        ],
        [
            InlineKeyboardButton("₿ 𝑪𝑹𝒀𝑷𝑻𝑶", callback_data="crypto"),
            InlineKeyboardButton("📈 𝑭𝑶𝑹𝑬𝑿", callback_data="forex"),
        ],
        [
            InlineKeyboardButton("📉 𝑰𝑵𝑫𝑰𝑪𝑬𝑺", callback_data="indices"),
            InlineKeyboardButton("⚡ 𝑩𝑰𝑵𝑨𝑹𝒀", callback_data="binary"),
        ],
        [
            InlineKeyboardButton(
                "📚 𝑺𝑰𝑮𝑵𝑨𝑳 𝑯𝑰𝑺𝑻𝑶𝑹𝒀",
                callback_data="history",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 𝑴𝒀 𝑨𝑪𝑪𝑶𝑼𝑵𝑻",
                callback_data="account",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def crypto_keyboard():
    keyboard = []

    for coin in CRYPTO_COINS:
        keyboard.append([
            InlineKeyboardButton(
                f"🪙 {coin}/USDT",
                callback_data=f"coin_{coin}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ 𝑴𝑬𝑵𝑼", callback_data="home")
    ])

    return InlineKeyboardMarkup(keyboard)


def timeframe_keyboard(symbol):
    keyboard = [
        [
            InlineKeyboardButton(
                timeframe,
                callback_data=f"analyse_{symbol}_{timeframe}",
            )
            for timeframe in TIMEFRAMES
        ],
        [
            InlineKeyboardButton(
                "⬅️ 𝑪𝑶𝑰𝑵𝑺",
                callback_data="crypto",
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
#  START AND HELP COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    register_user(user)

    message = (
        f"上帝 | {BOT_TITLE}\n"
        f"{BOT_SUBTITLE}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Welcome to the trading intelligence desk.\n\n"
        "📊 Live Binance crypto market data\n"
        "🧮 Mathematical technical analysis\n"
        "📈 EMA • RSI • MACD • ATR\n"
        "🎯 BUY / SELL / NO SIGNAL\n\n"
        "Choose an option below to begin.\n\n"
        "⚠️ Signals are analysis, not guaranteed outcomes."
    )

    await update.message.reply_text(
        message,
        reply_markup=main_keyboard(),
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = (
        f"{BOT_TITLE} — 𝑪𝑶𝑴𝑴𝑨𝑵𝑫𝑺\n\n"
        "/start — Open main menu\n"
        "/help — Show commands\n"
        "/status — Bot status\n"
        "/price BTC — Live crypto price\n"
        "/signal BTC 15m — Market analysis\n"
        "/admin — Admin dashboard\n"
        "/broadcast message — Admin broadcast\n\n"
        "Supported coins: BTC, ETH, BNB, SOL\n"
        "Timeframes: 15m, 1h, 4h"
    )

    await update.message.reply_text(message)


# =========================================================
#  LIVE PRICE COMMAND
# =========================================================

async def price_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    symbol = "BTC"

    if context.args:
        symbol = context.args[0].upper()

    try:
        data = await get_price(symbol)

        message = (
            f"上帝 | 𝑳𝑰𝑽𝑬 𝑪𝑹𝒀𝑷𝑻𝑶 𝑫𝑬𝑺𝑲\n\n"
            f"Coin: {data['symbol']}\n"
            f"Pair: {data['pair']}\n"
            f"Live price: ${data['price']:,.4f}\n\n"
            "Source: Binance public market data"
        )

        await update.effective_message.reply_text(message)

    except Exception:
        logger.exception("Price request failed")

        await update.effective_message.reply_text(
            "Could not fetch the live price. Please try again."
        )


# =========================================================
#  SIGNAL COMMAND AND DISPLAY
# =========================================================

def format_signal(result):
    direction = result["direction"]

    if direction == "BUY":
        heading = "🟢 𝑩𝑼𝒀 𝑺𝑰𝑮𝑵𝑨𝑳"
    elif direction == "SELL":
        heading = "🔴 𝑺𝑬𝑳𝑳 𝑺𝑰𝑮𝑵𝑨𝑳"
    else:
        heading = "🟡 𝑵𝑶 𝑺𝑰𝑮𝑵𝑨𝑳"

    message = (
        f"上帝 | {BOT_TITLE}\n"
        f"𝑴𝑨𝑹𝑲𝑬𝑻 𝑨𝑵𝑨𝑳𝒀𝑺𝑰𝑺\n\n"
        f"{heading}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Asset: {result['symbol']}/USDT\n"
        f"Timeframe: {result['timeframe']}\n"
        f"Entry reference: ${result['entry']:,.4f}\n\n"
        "𝑴𝑨𝑻𝑯𝑬𝑴𝑨𝑻𝑰𝑪𝑨𝑳 𝑹𝑬𝑨𝑫𝑰𝑵𝑮𝑺\n"
        f"EMA 9: ${result['ema9']:,.4f}\n"
        f"EMA 21: ${result['ema21']:,.4f}\n"
        f"RSI 14: {result['rsi']:.2f}\n"
        f"MACD: {result['macd']:.6f}\n"
        f"MACD Signal: {result['macd_signal']:.6f}\n"
        f"ATR 14: ${result['atr']:,.4f}\n"
        f"Support: ${result['support']:,.4f}\n"
        f"Resistance: ${result['resistance']:,.4f}\n\n"
        f"BUY score: {result['buy_score']}/4\n"
        f"SELL score: {result['sell_score']}/4\n"
    )

    if direction in ("BUY", "SELL"):
        message += (
            "\n𝑻𝑹𝑨𝑫𝑬 𝑳𝑬𝑽𝑬𝑳𝑺\n"
            f"Stop loss: ${result['stop_loss']:,.4f}\n"
            f"Target: ${result['take_profit']:,.4f}\n"
        )
    else:
        message += (
            "\nThe indicators do not agree strongly enough.\n"
            "No trade setup is recommended by this engine.\n"
        )

    message += (
        "\n━━━━━━━━━━━━━━━━━━\n"
        "Source: Binance\n"
        "Method: Mathematical technical analysis\n"
        "⚠️ Educational analysis only. Not financial advice."
    )

    return message


async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    symbol = "BTC"
    timeframe = "15m"

    if len(context.args) >= 1:
        symbol = context.args[0].upper()

    if len(context.args) >= 2:
        timeframe = context.args[1].lower()

    if symbol not in CRYPTO_COINS:
        await update.effective_message.reply_text(
            "Unsupported coin. Choose BTC, ETH, BNB or SOL."
        )
        return

    if timeframe not in TIMEFRAMES:
        await update.effective_message.reply_text(
            "Unsupported timeframe. Choose 15m, 1h or 4h."
        )
        return

    status_message = await update.effective_message.reply_text(
        "🧮 𝑨𝑵𝑨𝑳𝒀𝒁𝑰𝑵𝑮 𝑴𝑨𝑹𝑲𝑬𝑻...\n"
        "Fetching Binance candles and calculating indicators."
    )

    try:
        result = await analyse_market(symbol, timeframe)

        if result["direction"] in ("BUY", "SELL"):
            save_signal(
                update.effective_user.id,
                symbol,
                timeframe,
                result["direction"],
                result["entry"],
                result["stop_loss"],
                result["take_profit"],
            )

        await status_message.edit_text(
            format_signal(result)
        )

    except Exception:
        logger.exception("Signal analysis failed")

        await status_message.edit_text(
            "Market analysis is temporarily unavailable.\n"
            "Please try again later."
        )


# =========================================================
#  STATUS AND ADMIN COMMANDS
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        f"上帝 | 𝑺𝒀𝑺𝑻𝑬𝑴 𝑺𝑻𝑨𝑻𝑼𝑺\n\n"
        "Bot: Online\n"
        "Crypto data: Binance public API\n"
        "Analysis: Mathematical engine\n"
        "Supported coins: BTC, ETH, BNB, SOL\n"
        "API key: Not required"
    )


async def admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "⛔ Access denied."
        )
        return

    total_users = get_user_count()

    message = (
        f"上帝 | 𝑨𝑫𝑴𝑰𝑵 𝑫𝑨𝑺𝑯𝑩𝑶𝑨𝑹𝑫\n\n"
        f"Registered users: {total_users}\n"
        "Bot status: Online\n"
        "Market data: Binance\n\n"
        "Admin commands:\n"
        "/broadcast Your message"
    )

    await update.effective_message.reply_text(message)


async def broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "⛔ Access denied."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /broadcast Your message"
        )
        return

    announcement = " ".join(context.args)
    user_ids = get_all_user_ids()

    sent = 0
    failed = 0

    for target_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=announcement,
            )
            sent += 1

        except Exception:
            failed += 1
            logger.exception(
                "Broadcast failed for user %s",
                target_id,
            )

    await update.effective_message.reply_text(
        f"𝑩𝑹𝑶𝑨𝑫𝑪𝑨𝑺𝑻 𝑹𝑬𝑺𝑼𝑳𝑻\n\n"
        f"Sent: {sent}\n"
        f"Failed: {failed}"
    )


# =========================================================
#  BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "home":
        await query.edit_message_text(
            f"上帝 | {BOT_TITLE}\n"
            "Choose an option:",
            reply_markup=main_keyboard(),
        )

    elif data == "new_signal" or data == "crypto":
        await query.edit_message_text(
            "₿ 𝑪𝑹𝒀𝑷𝑻𝑶 𝑴𝑨𝑹𝑲𝑬𝑻\n\n"
            "Choose a cryptocurrency:",
            reply_markup=crypto_keyboard(),
        )

    elif data.startswith("coin_"):
        symbol = data.replace("coin_", "")

        await query.edit_message_text(
            f"Selected asset: {symbol}/USDT\n\n"
            "Choose your analysis timeframe:",
            reply_markup=timeframe_keyboard(symbol),
        )

    elif data.startswith("analyse_"):
        parts = data.split("_")

        if len(parts) == 3:
            symbol = parts[1]
            timeframe = parts[2]

            # Run the same analysis as /signal.
            try:
                await query.edit_message_text(
                    "🧮 𝑨𝑵𝑨𝑳𝒀𝒁𝑰𝑵𝑮..."
                )

                result = await analyse_market(symbol, timeframe)

                if result["direction"] in ("BUY", "SELL"):
                    save_signal(
                        query.from_user.id,
                        symbol,
                        timeframe,
                        result["direction"],
                        result["entry"],
                        result["stop_loss"],
                        result["take_profit"],
                    )

                await query.edit_message_text(
                    format_signal(result)
                )

            except Exception:
                logger.exception("Button analysis failed")

                await query.edit_message_text(
                    "Market analysis is unavailable right now."
                )

    elif data == "forex":
        await query.edit_message_text(
            "𝑭𝑶𝑹𝑬𝑿 𝑴𝑨𝑹𝑲𝑬𝑻\n\n"
            "Forex data connection has not been added yet.\n"
            "Crypto is currently the active market.",
            reply_markup=main_keyboard(),
        )

    elif data == "indices":
        await query.edit_message_text(
            "𝑰𝑵𝑫𝑰𝑪𝑬𝑺\n\n"
            "Index market data has not been connected yet.",
            reply_markup=main_keyboard(),
        )

    elif data == "binary":
        await query.edit_message_text(
            "𝑩𝑰𝑵𝑨𝑹𝒀 𝑨𝑵𝑨𝑳𝒀𝑺𝑰𝑺\n\n"
            "Binary UP/DOWN analysis is not active yet.",
            reply_markup=main_keyboard(),
        )

    elif data == "history":
        await query.edit_message_text(
            "𝑺𝑰𝑮𝑵𝑨𝑳 𝑯𝑰𝑺𝑻𝑶𝑹𝒀\n\n"
            "Signal history display will be added in the next stage.",
            reply_markup=main_keyboard(),
        )

    elif data == "account":
        await query.edit_message_text(
            f"𝑴𝒀 𝑨𝑪𝑪𝑶𝑼𝑵𝑻\n\n"
            f"Telegram ID: {query.from_user.id}\n"
            "Account: Registered",
            reply_markup=main_keyboard(),
        )


# =========================================================
#  UNKNOWN COMMANDS
# =========================================================

async def unknown_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "Unknown command. Use /help to see available commands."
    )


# =========================================================
#  STARTUP
# =========================================================

async def post_init(application: Application):
    me = await application.bot.get_me()
    logger.info("Connected as @%s", me.username)


def main():
    validate_config()
    init_database()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("broadcast", broadcast))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(
        MessageHandler(filters.COMMAND, unknown_command)
    )

    logger.info("Starting 上帝 trading intelligence bot...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
    

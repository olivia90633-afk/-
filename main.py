import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

from config import BOT_TOKEN, ADMIN_IDS, DATABASE_PATH, validate_config
from market_data import get_price, get_candles

# ============================================================
# 上帝 | GOD TRADING — COMPLETE MAIN BOT
# Binance public market data only. No API key. No auto-trading.
# ============================================================
BOT_TITLE = "𝑮𝑶𝑫 𝑻𝑹𝑨𝑫𝑰𝑵𝑮"
BOT_SUBTITLE = "𝑨𝑰 𝑴𝑨𝑹𝑲𝑬𝑻 𝑰𝑵𝑻𝑬𝑳𝑳𝑰𝑮𝑬𝑵𝑪𝑬"
CRYPTO_COINS = ["BTC", "ETH", "BNB", "SOL"]
TIMEFRAMES = ["15m", "1h", "4h"]
AUTO_TIMEFRAME = "15m"
AUTO_INTERVAL_SECONDS = 15 * 60
SIGNAL_ACTIVE_MINUTES = 15

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("shangdi")


# ------------------------- DATABASE -------------------------
def db_connection():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_database():
    with db_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            joined_at TEXT, last_seen TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            symbol TEXT, timeframe TEXT, direction TEXT, entry REAL,
            stop_loss REAL, take_profit REAL, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS auto_scan_history (
            symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
            candle_close_time INTEGER NOT NULL, direction TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            PRIMARY KEY (symbol, timeframe, candle_close_time))""")


def register_user(user):
    now = datetime.now(timezone.utc).isoformat()
    with db_connection() as conn:
        conn.execute("""INSERT INTO bot_users
            (user_id, username, first_name, joined_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username, first_name=excluded.first_name,
            last_seen=excluded.last_seen""",
            (user.id, user.username or "", user.first_name or "", now, now))


def get_user_count():
    with db_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM bot_users").fetchone()
    return row[0] if row else 0


def get_all_user_ids():
    with db_connection() as conn:
        rows = conn.execute("SELECT user_id FROM bot_users").fetchall()
    return [row[0] for row in rows]


def save_signal(user_id, symbol, timeframe, direction, entry, stop_loss, take_profit):
    with db_connection() as conn:
        conn.execute("""INSERT INTO signal_history
            (user_id, symbol, timeframe, direction, entry, stop_loss,
             take_profit, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, symbol, timeframe, direction, entry, stop_loss,
             take_profit, datetime.now(timezone.utc).isoformat()))


def has_active_signal(symbol, timeframe, minutes=SIGNAL_ACTIVE_MINUTES):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with db_connection() as conn:
        row = conn.execute("""SELECT id FROM signal_history
            WHERE symbol=? AND timeframe=? AND direction IN ('BUY','SELL')
            AND created_at>=? LIMIT 1""", (symbol, timeframe, cutoff)).fetchone()
    return row is not None


def was_candle_scanned(symbol, timeframe, close_time):
    with db_connection() as conn:
        row = conn.execute("""SELECT 1 FROM auto_scan_history
            WHERE symbol=? AND timeframe=? AND candle_close_time=?""",
            (symbol, timeframe, close_time)).fetchone()
    return row is not None


def record_candle_scan(symbol, timeframe, close_time, direction):
    with db_connection() as conn:
        conn.execute("""INSERT OR IGNORE INTO auto_scan_history
            (symbol, timeframe, candle_close_time, direction, scanned_at)
            VALUES (?, ?, ?, ?, ?)""",
            (symbol, timeframe, close_time, direction,
             datetime.now(timezone.utc).isoformat()))


def get_recent_signals(limit=10):
    with db_connection() as conn:
        return conn.execute("""SELECT symbol, timeframe, direction, entry, created_at
            FROM signal_history ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()


# ------------------- MATHEMATICAL INDICATORS ----------------
def calculate_ema(values, period):
    if len(values) < period:
        raise ValueError(f"Not enough candles for EMA {period}.")
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = value * multiplier + ema * (1 - multiplier)
    return ema


def calculate_rsi(values, period=14):
    if len(values) < period + 1:
        raise ValueError("Not enough candles for RSI.")
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0) for change in changes]
    losses = [abs(min(change, 0)) for change in changes]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_macd(values):
    if len(values) < 35:
        raise ValueError("Not enough candles for MACD.")
    history = []
    for end in range(26, len(values) + 1):
        section = values[:end]
        history.append(calculate_ema(section, 12) - calculate_ema(section, 26))
    return history[-1], calculate_ema(history, 9)


def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        raise ValueError("Not enough candles for ATR.")
    ranges = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]
        ranges.append(max(cur["high"] - cur["low"],
                          abs(cur["high"] - prev["close"]),
                          abs(cur["low"] - prev["close"])))
    return sum(ranges[-period:]) / period


def calculate_support_resistance(candles, period=20):
    recent = candles[-period:]
    return (min(c["low"] for c in recent),
            max(c["high"] for c in recent))


# ------------------------- ANALYSIS -------------------------
async def analyse_market(symbol, timeframe):
    if symbol not in CRYPTO_COINS or timeframe not in TIMEFRAMES:
        raise ValueError("Unsupported coin or timeframe.")
    candles = await get_candles(symbol=symbol, interval=timeframe, limit=120)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    # Exclude the currently forming candle: analyse completed candles only.
    candles = [c for c in candles if c["close_time"] < now_ms]
    if len(candles) < 50:
        raise ValueError("Not enough completed Binance candles.")

    closes = [c["close"] for c in candles]
    price = closes[-1]
    ema9, ema21 = calculate_ema(closes, 9), calculate_ema(closes, 21)
    rsi = calculate_rsi(closes, 14)
    macd, macd_signal = calculate_macd(closes)
    atr = calculate_atr(candles, 14)
    support, resistance = calculate_support_resistance(candles, 20)

    buy_score = sell_score = 0
    if ema9 > ema21:
        buy_score += 1
    elif ema9 < ema21:
        sell_score += 1
    if macd > macd_signal:
        buy_score += 1
    elif macd < macd_signal:
        sell_score += 1
    if 50 <= rsi <= 70:
        buy_score += 1
    elif 30 <= rsi < 50:
        sell_score += 1
    if price > ema9:
        buy_score += 1
    elif price < ema9:
        sell_score += 1

    direction = "NO SIGNAL"
    if buy_score >= 3 and buy_score > sell_score:
        direction = "BUY"
    elif sell_score >= 3 and sell_score > buy_score:
        direction = "SELL"

    stop_loss = take_profit = None
    if direction == "BUY":
        stop_loss, take_profit = price - 1.5 * atr, price + 3 * atr
    elif direction == "SELL":
        stop_loss, take_profit = price + 1.5 * atr, price - 3 * atr

    return {
        "symbol": symbol, "timeframe": timeframe, "direction": direction,
        "entry": price, "stop_loss": stop_loss, "take_profit": take_profit,
        "ema9": ema9, "ema21": ema21, "rsi": rsi, "macd": macd,
        "macd_signal": macd_signal, "atr": atr, "support": support,
        "resistance": resistance, "buy_score": buy_score,
        "sell_score": sell_score, "candle_close_time": candles[-1]["close_time"],
    }


# --------------------------- MENUS --------------------------
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 𝑵𝑬𝑾 𝑺𝑰𝑮𝑵𝑨𝑳", callback_data="crypto")],
        [InlineKeyboardButton("₿ 𝑪𝑹𝒀𝑷𝑻𝑶", callback_data="crypto"),
         InlineKeyboardButton("📈 𝑭𝑶𝑹𝑬𝑿", callback_data="forex")],
        [InlineKeyboardButton("📉 𝑰𝑵𝑫𝑰𝑪𝑬𝑺", callback_data="indices"),
         InlineKeyboardButton("⚡ 𝑩𝑰𝑵𝑨𝑹𝒀", callback_data="binary")],
        [InlineKeyboardButton("📚 𝑺𝑰𝑮𝑵𝑨𝑳 𝑯𝑰𝑺𝑻𝑶𝑹𝒀", callback_data="history")],
        [InlineKeyboardButton("👤 𝑴𝒀 𝑨𝑪𝑪𝑶𝑼𝑵𝑻", callback_data="account")],
    ])


def crypto_keyboard():
    rows = [[InlineKeyboardButton(f"🪙 {coin}/USDT", callback_data=f"coin_{coin}")]
            for coin in CRYPTO_COINS]
    rows.append([InlineKeyboardButton("⬅️ 𝑴𝑬𝑵𝑼", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def timeframe_keyboard(symbol):
    rows = [[InlineKeyboardButton(tf, callback_data=f"analyse_{symbol}_{tf}")
             for tf in TIMEFRAMES]]
    rows.append([InlineKeyboardButton("⬅️ 𝑪𝑶𝑰𝑵𝑺", callback_data="crypto")])
    return InlineKeyboardMarkup(rows)


# ---------------------- SIGNAL FORMATTING -------------------
def format_signal(result, automatic=False):
    direction = result["direction"]
    heading = {"BUY": "🟢 𝑩𝑼𝒀 𝑺𝑰𝑮𝑵𝑨𝑳",
               "SELL": "🔴 𝑺𝑬𝑳𝑳 𝑺𝑰𝑮𝑵𝑨𝑳"}.get(direction, "🟡 𝑵𝑶 𝑺𝑰𝑮𝑵𝑨𝑳")
    prefix = "🔔 𝑨𝑼𝑻𝑶 𝑺𝑰𝑮𝑵𝑨𝑳\n" if automatic else ""
    msg = (f"{prefix}上帝 | {BOT_TITLE}\n𝑴𝑨𝑹𝑲𝑬𝑻 𝑨𝑵𝑨𝑳𝒀𝑺𝑰𝑺\n\n"
           f"{heading}\n━━━━━━━━━━━━━━━━━━\n"
           f"Asset: {result['symbol']}/USDT\nTimeframe: {result['timeframe']}\n"
           f"Entry reference: ${result['entry']:,.4f}\n\n"
           "𝑴𝑨𝑻𝑯𝑬𝑴𝑨𝑻𝑰𝑪𝑨𝑳 𝑹𝑬𝑨𝑫𝑰𝑵𝑮𝑺\n"
           f"EMA 9: ${result['ema9']:,.4f}\nEMA 21: ${result['ema21']:,.4f}\n"
           f"RSI 14: {result['rsi']:.2f}\nMACD: {result['macd']:.6f}\n"
           f"MACD Signal: {result['macd_signal']:.6f}\nATR 14: ${result['atr']:,.4f}\n"
           f"Support: ${result['support']:,.4f}\nResistance: ${result['resistance']:,.4f}\n\n"
           f"BUY score: {result['buy_score']}/4\nSELL score: {result['sell_score']}/4\n")
    if direction in ("BUY", "SELL"):
        msg += (f"\n𝑻𝑹𝑨𝑫𝑬 𝑳𝑬𝑽𝑬𝑳𝑺\nStop loss: ${result['stop_loss']:,.4f}\n"
                f"Target: ${result['take_profit']:,.4f}\n")
    else:
        msg += "\nIndicators do not agree strongly enough. No trade setup is recommended.\n"
    return msg + ("\n━━━━━━━━━━━━━━━━━━\nSource: Binance public market data\n"
                  "Method: Mathematical technical analysis\n"
                  "⚠️ Experimental analysis, not financial advice.")


# ------------------------- USER COMMANDS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await update.effective_message.reply_text(
        f"上帝 | {BOT_TITLE}\n{BOT_SUBTITLE}\n\n━━━━━━━━━━━━━━━━━━\n"
        "Welcome to the trading intelligence desk.\n\n"
        "📊 Binance live crypto market data\n🧮 EMA • RSI • MACD • ATR\n"
        "🎯 BUY / SELL / NO SIGNAL\n⏰ Automatic 15-minute market scans\n\n"
        "Choose an option below.\n\n⚠️ Signals are experimental, not guaranteed.",
        reply_markup=main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"{BOT_TITLE} — 𝑪𝑶𝑴𝑴𝑨𝑵𝑫𝑺\n\n/start — Main menu\n/help — Commands\n"
        "/status — Bot status\n/price BTC — Live price\n/signal BTC 15m — Analyse market\n"
        "/history — Recent signals\n/admin — Admin dashboard\n/broadcast message — Admin broadcast\n\n"
        "Coins: BTC, ETH, BNB, SOL\nTimeframes: 15m, 1h, 4h")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"上帝 | 𝑺𝒀𝑺𝑻𝑬𝑴 𝑺𝑻𝑨𝑻𝑼𝑺\n\nBot: Online\n"
        "Crypto data: Binance public endpoints\nAPI key: Not required\n"
        "Analysis: Mathematical indicators\nAutomatic scan: Every 15 minutes\nActive market: Crypto")


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTC"
    try:
        data = await get_price(symbol)
        await update.effective_message.reply_text(
            f"上帝 | 𝑳𝑰𝑽𝑬 𝑪𝑹𝒀𝑷𝑻𝑶 𝑫𝑬𝑺𝑲\n\nCoin: {data['symbol']}\n"
            f"Pair: {data['pair']}\nLive price: ${data['price']:,.4f}\n\nSource: Binance")
    except Exception:
        logger.exception("Price request failed")
        await update.effective_message.reply_text(
            "Could not fetch the live price. Check the coin symbol and try again.")


async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTC"
    timeframe = context.args[1].lower() if len(context.args) > 1 else "15m"
    if symbol not in CRYPTO_COINS:
        await update.effective_message.reply_text("Unsupported coin. Choose BTC, ETH, BNB or SOL.")
        return
    if timeframe not in TIMEFRAMES:
        await update.effective_message.reply_text("Unsupported timeframe. Choose 15m, 1h or 4h.")
        return
    status_msg = await update.effective_message.reply_text("🧮 𝑨𝑵𝑨𝑳𝒀𝒁𝑰𝑵𝑮 𝑴𝑨𝑹𝑲𝑬𝑻...")
    try:
        result = await analyse_market(symbol, timeframe)
        if result["direction"] in ("BUY", "SELL"):
            save_signal(update.effective_user.id, symbol, timeframe, result["direction"],
                        result["entry"], result["stop_loss"], result["take_profit"])
        await status_msg.edit_text(format_signal(result))
    except Exception:
        logger.exception("Manual signal analysis failed")
        await status_msg.edit_text("Market analysis is temporarily unavailable. Please try again.")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_recent_signals(10)
    if not rows:
        await update.effective_message.reply_text("No signals have been recorded yet.")
        return
    lines = ["📚 𝑹𝑬𝑪𝑬𝑵𝑻 𝑺𝑰𝑮𝑵𝑨𝑳𝑺\n"]
    for symbol, timeframe, direction, entry, created_at in rows:
        lines.append(f"{symbol}/USDT | {timeframe} | {direction}\nEntry: ${entry:,.4f}\n"
                     f"{created_at[:16].replace('T', ' ')} UTC\n")
    await update.effective_message.reply_text("\n".join(lines))


# -------------------------- ADMIN ---------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text("⛔ Access denied.")
        return
    await update.effective_message.reply_text(
        f"上帝 | 𝑨𝑫𝑴𝑰𝑵 𝑫𝑨𝑺𝑯𝑩𝑶𝑨𝑹𝑫\n\nRegistered users: {get_user_count()}\n"
        "Bot status: Online\nMarket data: Binance\nAutomatic scanner: 15 minutes\n\n"
        "Use: /broadcast Your message")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text("⛔ Access denied.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /broadcast Your message")
        return
    message = " ".join(context.args)
    sent = failed = 0
    for user_id in get_all_user_ids():
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Broadcast failed for user %s", user_id)
    await update.effective_message.reply_text(
        f"𝑩𝑹𝑶𝑨𝑫𝑪𝑨𝑺𝑻 𝑹𝑬𝑺𝑼𝑳𝑻\n\nSent: {sent}\nFailed: {failed}")


# ------------------- AUTOMATIC 15-MIN SCANNER ---------------
async def automatic_signal_scan(application):
    users = get_all_user_ids()
    if not users:
        logger.info("No registered users; automatic scan skipped.")
        return

    for symbol in CRYPTO_COINS:
        try:
            result = await analyse_market(symbol, AUTO_TIMEFRAME)
            close_time = result["candle_close_time"]
            if was_candle_scanned(symbol, AUTO_TIMEFRAME, close_time):
                logger.info("%s candle already scanned.", symbol)
                continue

            direction = result["direction"]
            if direction in ("BUY", "SELL") and has_active_signal(symbol, AUTO_TIMEFRAME):
                record_candle_scan(symbol, AUTO_TIMEFRAME, close_time, "SKIPPED_ACTIVE")
                logger.info("%s skipped: earlier 15m signal is still active.", symbol)
                continue

            # Record NO SIGNAL too, preventing repeat scans of the same candle.
            record_candle_scan(symbol, AUTO_TIMEFRAME, close_time, direction)
            if direction == "NO SIGNAL":
                logger.info("%s: no valid setup.", symbol)
                continue

            # user_id=0 marks a scanner-generated signal in the shared history.
            save_signal(0, symbol, AUTO_TIMEFRAME, direction, result["entry"],
                        result["stop_loss"], result["take_profit"])
            message = format_signal(result, automatic=True)
            delivered = 0
            for user_id in users:
                try:
                    await application.bot.send_message(chat_id=user_id, text=message)
                    delivered += 1
                except Exception:
                    logger.exception("Auto signal delivery failed for user %s", user_id)
            logger.info("Automatic %s %s signal delivered to %s users.",
                        direction, symbol, delivered)
        except Exception:
            logger.exception("Automatic scan failed for %s.", symbol)


async def automatic_signal_loop(application):
    logger.info("Automatic 15-minute scanner started.")
    while True:
        cycle_started = time.monotonic()
        try:
            await automatic_signal_scan(application)
        except Exception:
            logger.exception("Automatic scanner cycle failed.")
        elapsed = time.monotonic() - cycle_started
        await asyncio.sleep(max(1, AUTO_INTERVAL_SECONDS - elapsed))


# ------------------------- BUTTONS -------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "home":
        await query.edit_message_text(f"上帝 | {BOT_TITLE}\nChoose an option:",
                                      reply_markup=main_keyboard())
    elif data == "crypto":
        await query.edit_message_text("₿ 𝑪𝑹𝒀𝑷𝑻𝑶 𝑴𝑨𝑹𝑲𝑬𝑻\n\nChoose a cryptocurrency:",
                                      reply_markup=crypto_keyboard())
    elif data.startswith("coin_"):
        symbol = data.replace("coin_", "", 1)
        if symbol not in CRYPTO_COINS:
            await query.edit_message_text("Unsupported coin.")
            return
        await query.edit_message_text(f"Selected asset: {symbol}/USDT\n\nChoose a timeframe:",
                                      reply_markup=timeframe_keyboard(symbol))
    elif data.startswith("analyse_"):
        parts = data.split("_")
        if len(parts) != 3 or parts[1] not in CRYPTO_COINS or parts[2] not in TIMEFRAMES:
            await query.edit_message_text("Invalid market selection.")
            return
        symbol, timeframe = parts[1], parts[2]
        await query.edit_message_text("🧮 𝑨𝑵𝑨𝑳𝒀𝒁𝑰𝑵𝑮...")
        try:
            result = await analyse_market(symbol, timeframe)
            if result["direction"] in ("BUY", "SELL"):
                save_signal(query.from_user.id, symbol, timeframe, result["direction"],
                            result["entry"], result["stop_loss"], result["take_profit"])
            await query.edit_message_text(format_signal(result))
        except Exception:
            logger.exception("Button analysis failed")
            await query.edit_message_text("Market analysis is unavailable. Please try again.")
    elif data == "history":
        rows = get_recent_signals(10)
        if not rows:
            message = "No signals have been recorded yet."
        else:
            lines = ["📚 𝑹𝑬𝑪𝑬𝑵𝑻 𝑺𝑰𝑮𝑵𝑨𝑳𝑺\n"]
            for symbol, timeframe, direction, entry, created_at in rows:
                lines.append(f"{symbol}/USDT | {timeframe} | {direction}\nEntry: ${entry:,.4f}\n"
                             f"{created_at[:16].replace('T', ' ')} UTC\n")
            message = "\n".join(lines)
        await query.edit_message_text(message, reply_markup=main_keyboard())
    elif data == "account":
        await query.edit_message_text(f"𝑴𝒀 𝑨𝑪𝑪𝑶𝑼𝑵𝑻\n\nTelegram ID: {query.from_user.id}\nAccount: Registered",
                                      reply_markup=main_keyboard())
    elif data in ("forex", "indices", "binary"):
        labels = {"forex": "𝑭𝑶𝑹𝑬𝑿 𝑴𝑨𝑹𝑲𝑬𝑻",
                  "indices": "𝑰𝑵𝑫𝑰𝑪𝑬𝑺", "binary": "𝑩𝑰𝑵𝑨𝑹𝒀 𝑨𝑵𝑨𝑳𝒀𝑺𝑰𝑺"}
        await query.edit_message_text(f"{labels[data]}\n\nThis market is not connected yet. Crypto is active.",
                                      reply_markup=main_keyboard())


# -------------------- ERROR + APPLICATION ------------------
async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Unknown command. Use /help to see available commands.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled Telegram application error: %s", context.error,
                 exc_info=context.error)


async def post_init(application: Application):
    me = await application.bot.get_me()
    logger.info("Connected as @%s", me.username)
    application.create_task(automatic_signal_loop(application),
                            name="automatic_15_minute_signal_scanner")


def main():
    validate_config()
    init_database()
    app = (Application.builder().token(BOT_TOKEN).post_init(post_init).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(error_handler)
    logger.info("Starting 上帝 trading intelligence bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Binance public market-data endpoint.
# No API key or trading permission is required.
BINANCE_BASE_URL = "https://api.binance.com"

SUPPORTED_PAIRS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "BNB": "BNBUSDT",
    "SOL": "SOLUSDT",
}

SUPPORTED_TIMEFRAMES = {
    "1m", "5m", "15m", "30m",
    "1h", "4h", "1d",
}


async def get_price(symbol: str = "BTC") -> dict[str, Any]:
    """Get the latest Binance spot price."""
    symbol = symbol.upper()

    if symbol not in SUPPORTED_PAIRS:
        raise ValueError(f"Unsupported crypto: {symbol}")

    pair = SUPPORTED_PAIRS[symbol]
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            url,
            params={"symbol": pair}
        )
        response.raise_for_status()
        data = response.json()

    return {
        "symbol": symbol,
        "pair": pair,
        "price": float(data["price"]),
    }


async def get_candles(
    symbol: str = "BTC",
    interval: str = "15m",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get historical OHLCV candles from Binance."""
    symbol = symbol.upper()

    if symbol not in SUPPORTED_PAIRS:
        raise ValueError(f"Unsupported crypto: {symbol}")

    if interval not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {interval}")

    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000.")

    pair = SUPPORTED_PAIRS[symbol]
    url = f"{BINANCE_BASE_URL}/api/v3/klines"

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            url,
            params={
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            },
        )
        response.raise_for_status()
        rows = response.json()

    candles = []

    for row in rows:
        candles.append({
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": int(row[6]),
        })

    return candles


async def test_binance_connection() -> dict[str, Any]:
    """Test the public Binance market-data connection."""
    price = await get_price("BTC")

    return {
        "connected": True,
        "exchange": "Binance",
        "symbol": price["pair"],
        "price": price["price"],
      }
  

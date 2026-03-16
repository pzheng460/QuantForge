"""Exchange connector wrapper for Pine live trading.

Handles fetching warmup klines and subscribing to live kline streams
using ccxt for historical data.  The actual live WebSocket kline feed
should be wired in by the caller (e.g. via QuantForge engine connectors).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from quantforge.pine.interpreter.context import BarData

logger = logging.getLogger(__name__)

# Timeframe → seconds mapping (subset)
_TF_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_to_seconds(tf: str) -> int:
    """Convert a timeframe string like '15m' to seconds."""
    if tf in _TF_SECONDS:
        return _TF_SECONDS[tf]
    raise ValueError(f"Unsupported timeframe: {tf}")


def fetch_warmup_bars(
    symbol: str,
    exchange_id: str,
    timeframe: str,
    num_bars: int = 500,
) -> list[BarData]:
    """Fetch historical bars for indicator warmup via ccxt.

    Returns a list of ``BarData`` sorted oldest-first.
    """
    import ccxt

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"Exchange '{exchange_id}' not found in ccxt")

    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()

    tf_sec = timeframe_to_seconds(timeframe)
    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(seconds=tf_sec * num_bars)
    since_ms = int(since_dt.timestamp() * 1000)

    all_ohlcv: list[list] = []
    current_since = since_ms
    limit = 1000

    while len(all_ohlcv) < num_bars:
        ohlcv = exchange.fetch_ohlcv(
            symbol, timeframe, since=current_since, limit=limit
        )
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        if last_ts <= current_since:
            break
        current_since = last_ts + 1

    # De-dup by timestamp and sort
    seen: set[int] = set()
    unique: list[list] = []
    for bar in all_ohlcv:
        ts = bar[0]
        if ts not in seen:
            seen.add(ts)
            unique.append(bar)
    unique.sort(key=lambda b: b[0])

    # Convert to BarData
    bars = [
        BarData(
            open=bar[1],
            high=bar[2],
            low=bar[3],
            close=bar[4],
            volume=bar[5],
            time=bar[0] // 1000,
        )
        for bar in unique[-num_bars:]
    ]

    logger.info("Fetched %d warmup bars for %s (%s)", len(bars), symbol, exchange_id)
    return bars


class CcxtConnector:
    """Wraps a ccxt exchange instance for order submission and position queries.

    Used by :class:`OrderBridge` to submit real orders when *demo* mode is off.

    Parameters
    ----------
    exchange_id : str
        ccxt exchange id (e.g. ``"bitget"``).
    symbol : str
        Trading symbol in ccxt format (e.g. ``"BTC/USDT:USDT"``).
    demo : bool
        If ``True``, use sandbox/demo API keys.
    """

    def __init__(self, exchange_id: str, symbol: str, demo: bool = True) -> None:
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.demo = demo
        self._exchange = self._create_exchange()

    def _create_exchange(self):
        import ccxt

        from quantforge.constants import settings

        exchange_cls = getattr(ccxt, self.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"Exchange '{self.exchange_id}' not found in ccxt")

        config: dict = {"enableRateLimit": True}

        # Load API keys from settings
        try:
            if self.exchange_id == "bitget":
                if self.demo:
                    config["apiKey"] = settings.BITGET.DEMO.API_KEY
                    config["secret"] = settings.BITGET.DEMO.SECRET
                    config["password"] = settings.BITGET.DEMO.PASSPHRASE
                    # Bitget UTA demo uses paptrading header (not sandbox URLs)
                    config["headers"] = {"paptrading": "1"}
                else:
                    config["apiKey"] = settings.BITGET.API_KEY
                    config["secret"] = settings.BITGET.SECRET
                    config["password"] = settings.BITGET.PASSPHRASE
            elif self.exchange_id == "binance":
                if self.demo:
                    config["apiKey"] = settings.BINANCE.TESTNET.API_KEY
                    config["secret"] = settings.BINANCE.TESTNET.SECRET
                else:
                    config["apiKey"] = settings.BINANCE.API_KEY
                    config["secret"] = settings.BINANCE.SECRET
            elif self.exchange_id == "okx":
                if self.demo:
                    config["apiKey"] = settings.OKX.DEMO_1.API_KEY
                    config["secret"] = settings.OKX.DEMO_1.SECRET
                    config["password"] = settings.OKX.DEMO_1.PASSPHRASE
                else:
                    config["apiKey"] = settings.OKX.LIVE.ACCOUNT1.API_KEY
                    config["secret"] = settings.OKX.LIVE.ACCOUNT1.SECRET
                    config["password"] = settings.OKX.LIVE.ACCOUNT1.PASSPHRASE
            elif self.exchange_id == "bybit":
                if self.demo:
                    config["apiKey"] = settings.BYBIT.TESTNET.API_KEY
                    config["secret"] = settings.BYBIT.TESTNET.SECRET
                else:
                    config["apiKey"] = settings.BYBIT.API_KEY
                    config["secret"] = settings.BYBIT.SECRET
        except (AttributeError, KeyError):
            logger.warning(
                "API keys not found for %s (demo=%s) — order submission will fail",
                self.exchange_id,
                self.demo,
            )

        exchange = exchange_cls(config)

        # Bitget demo uses paptrading header (set above), not sandbox mode.
        # Other exchanges may still need set_sandbox_mode.
        if self.demo and self.exchange_id != "bitget":
            exchange.set_sandbox_mode(True)

        exchange.load_markets()
        return exchange

    def submit_market_order(
        self, side: str, qty: float, reduce_only: bool = False
    ) -> dict:
        """Submit a market order.

        Parameters
        ----------
        side : str
            ``"buy"`` or ``"sell"``.
        qty : float
            Order quantity (in contracts or base currency).
        reduce_only : bool
            If ``True``, only reduce existing position.
        """
        params: dict = {}
        if reduce_only:
            params["reduceOnly"] = True
        # Bitget UTA requires uta=True to use v3 unified account API
        if self.exchange_id == "bitget":
            params["uta"] = True

        logger.info(
            "Submitting %s %s %.6f %s (reduce_only=%s)",
            "MARKET",
            side.upper(),
            qty,
            self.symbol,
            reduce_only,
        )
        result = self._exchange.create_order(
            self.symbol, "market", side, qty, params=params
        )
        logger.info(
            "Order result: id=%s status=%s", result.get("id"), result.get("status")
        )
        return result

    def submit_limit_order(
        self, side: str, qty: float, price: float, reduce_only: bool = False
    ) -> dict:
        """Submit a limit order."""
        params: dict = {}
        if reduce_only:
            params["reduceOnly"] = True
        if self.exchange_id == "bitget":
            params["uta"] = True

        logger.info(
            "Submitting LIMIT %s %.6f @ %.2f %s",
            side.upper(),
            qty,
            price,
            self.symbol,
        )
        result = self._exchange.create_order(
            self.symbol, "limit", side, qty, price, params=params
        )
        logger.info(
            "Order result: id=%s status=%s", result.get("id"), result.get("status")
        )
        return result

    def get_position(self) -> dict | None:
        """Get current position for the symbol.

        Returns a dict with ``side``, ``contracts``, ``entryPrice``, or
        ``None`` if flat.
        """
        positions = self._exchange.fetch_positions([self.symbol])
        for pos in positions:
            contracts = float(pos.get("contracts", 0))
            if contracts > 0:
                return {
                    "side": pos.get("side"),
                    "contracts": contracts,
                    "entryPrice": float(pos.get("entryPrice", 0)),
                    "unrealizedPnl": float(pos.get("unrealizedPnl", 0)),
                }
        return None

    def get_ticker_price(self) -> float:
        """Get the current ticker price for the symbol."""
        ticker = self._exchange.fetch_ticker(self.symbol)
        return float(ticker.get("last", 0))


def ohlcv_to_bar(ohlcv: list) -> BarData:
    """Convert a single ccxt OHLCV list ``[ts, o, h, l, c, v]`` to BarData."""
    return BarData(
        open=ohlcv[1],
        high=ohlcv[2],
        low=ohlcv[3],
        close=ohlcv[4],
        volume=ohlcv[5],
        time=ohlcv[0] // 1000,
    )

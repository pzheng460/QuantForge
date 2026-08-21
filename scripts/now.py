#!/usr/bin/env python3
"""Print current time and the next bar decision point in BOTH CST and UTC.

The QuantForge server runs in CST (UTC+8); exchange bars anchor on UTC hour
boundaries and journal logs print local CST. This one command removes the
8-hour confusion before operating live engines or reading logs.

Usage:
  uv run python scripts/now.py [--timeframe 1h] [--exchange bitget] [--symbol BTC/USDT:USDT]
"""

import argparse
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")

CST = timezone(timedelta(hours=8), "CST")


def _fmt(dt: datetime, tz: timezone) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--exchange", default="bitget")
    ap.add_argument("--symbol", default="BTC/USDT:USDT")
    args = ap.parse_args(argv)

    tf = args.timeframe
    # Map common timeframes to seconds; only hour-aligned frames are meaningful
    # for "next bar" below, but we show the formula for any.
    mult = {"1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800, "1m": 60,
            "5m": 300, "15m": 900, "30m": 1800}.get(tf)
    if mult is None:
        mult = 3600  # default assumption for unknown frames

    now = datetime.now(timezone.utc)
    print(f"now           : {_fmt(now, CST)}   (CST)")
    print(f"               {_fmt(now, timezone.utc)}   (UTC)")

    # Latest closed bar (UTC-anchored): the largest whole multiple of `mult`
    # that is strictly before now.
    epoch = int(now.timestamp())
    latest_close_epoch = (epoch // mult) * mult
    latest_close = datetime.fromtimestamp(latest_close_epoch, tz=timezone.utc)
    print(f"latest closed : {_fmt(latest_close, CST)}   (CST)  [{tf} bar]")
    print(f"               {_fmt(latest_close, timezone.utc)}   (UTC)")

    next_close_epoch = latest_close_epoch + mult  # = next decision bar close
    next_open_epoch = next_close_epoch  # engine submits at this bar's open
    next_close = datetime.fromtimestamp(next_close_epoch, tz=timezone.utc)
    print(f"next DECISION : {_fmt(next_close, CST)}   (CST)  — next {tf} bar "
          f"decision happens here (bar close)")
    print(f"               {_fmt(next_close, timezone.utc)}   (UTC)")
    mins = (next_close_epoch - epoch) / 60
    print(f"in            : {mins:.0f} minutes")
    print(f"engine note   : decides on last CLOSED bar, submits next-bar OPEN")

    # Optional: live market last price (best-effort, may fail offline).
    try:
        import ccxt

        cls = getattr(ccxt, args.exchange, None)
        if cls is None:
            raise ValueError(f"unknown exchange {args.exchange!r}")
        ex = cls()
        tick = ex.fetch_ticker(args.symbol)
        print(f"live price    : {args.symbol} bid={tick.get('bid')} ask={tick.get('ask')} last={tick.get('last')}")
    except Exception as exc:  # noqa: BLE001 — informational only
        print(f"live price    : unavailable ({type(exc).__name__})")


if __name__ == "__main__":
    main()

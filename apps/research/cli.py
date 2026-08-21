"""Unified CLI for the research app.

Usage
-----
  python -m apps.research download earnings [--workers 6] [--force] [--max N] [--symbols A,B]
  python -m apps.research download prices   [--chunk 40] [--workers 3] [--years 6y]
  python -m apps.research download crypto   [--force] [--spots A,B] [--perps A,B]
  python -m apps.research import             rebuild duckdb raw tables + events (SQL)
  python -m apps.research import-crypto      load crypto OHLCV/funding/OI into duckdb
  python -m apps.research import-options     load option chain snapshots into duckdb
  python -m apps.research events             rebuild events table only (no raw import)
  python -m apps.research study              run event study -> reports/*.md
  python -m apps.research crypto             crypto research (BTC/ETH/SOL) -> reports/crypto_research_*.md
  python -m apps.research options            live option-chain research -> reports/options_research_*.md
  python -m apps.research technical          multi-asset price/technical screen -> reports/technical_screen_*.md
  python -m apps.research status | verify | manifest
  python -m apps.research query "SELECT ..."
  python -m apps.research snapshot [--keep N]
  python -m apps.research restore <snap.tar.gz>

A thin launcher lives at scripts/research.py.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config, event_study, warehouse
from .download import fetch_crypto, fetch_earnings, fetch_prices
from .screen import main as screen_main
from .theme_resonance import main as resonance_main
from .repeat_surgers import main as repeats_main
from .composite_signal import main as composite_main


def _download(args: argparse.Namespace) -> int:
    symbols: list[str] = []
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = list(dict.fromkeys(config.load_universe() + config.EXTRA_SYMBOLS))
    if args.max:
        symbols = symbols[: args.max]

    if args.kind == "earnings":
        print(f"universe: {len(symbols)} tickers -> {config.EARN}", flush=True)
        counts, errors = fetch_earnings(symbols, workers=args.workers, force=args.force)
        print("done:", counts)
        for s, e in errors[:20]:
            print(f"  {s}: {e}", file=sys.stderr)
    elif args.kind == "crypto":
        spots = [s.strip() for s in args.spots.split(",") if s.strip()] or None
        perps = [s.strip() for s in args.perps.split(",") if s.strip()] or None
        counts, errors = fetch_crypto(spots=spots, perps=perps, force=args.force)
        print("done:", counts)
        for e in errors[:20]:
            print(f"  {e}", file=sys.stderr)
    else:
        ok, fails = fetch_prices(
            symbols, chunk_size=args.chunk, workers=args.workers,
            years=f"{args.years}y", force=args.force,
        )
        print(f"done: {ok} ok, {len(fails)} fail")
        for f in fails[:20]:
            print("  fail:", f, file=sys.stderr)
    return 0


def _cmd(args: argparse.Namespace) -> int:
    if args.cmd == "import":
        warehouse.import_raw()
    elif args.cmd == "import-crypto":
        print("crypto rows:", warehouse.import_crypto())
    elif args.cmd == "import-options":
        print("option chain rows:", warehouse.import_option_chains())
    elif args.cmd == "events":
        warehouse.build_events()
    elif args.cmd == "study":
        return event_study.main()
    elif args.cmd == "screen":
        return screen_main()
    elif args.cmd == "resonance":
        return resonance_main()
    elif args.cmd == "repeats":
        return repeats_main()
    elif args.cmd == "composite":
        return composite_main()
    elif args.cmd == "crypto":
        from . import crypto_research

        return crypto_research.main()
    elif args.cmd == "options":
        from . import options_research

        return options_research.main()
    elif args.cmd == "technical":
        from . import technical

        return technical.main([])
    elif args.cmd == "daily":
        from . import daily

        daily.run_daily(send_email=bool(args.email) and not bool(args.no_email))
        return 0
    elif args.cmd == "email":
        from apps.research import email_reports

        return email_reports.main([args.action])
    elif args.cmd == "status":
        print(json.dumps(warehouse.status(), indent=2, ensure_ascii=False))
    elif args.cmd == "verify":
        warehouse.verify()
    elif args.cmd == "manifest":
        warehouse.write_manifest()
    elif args.cmd == "query":
        df = warehouse.query(args.sql)
        print(f"rows: {len(df)}")
        print(df.to_string(max_rows=200, max_colwidth=40))
    elif args.cmd == "snapshot":
        warehouse.snapshot(keep=args.keep)
    elif args.cmd == "restore":
        warehouse.restore(args.snap)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="research", description="QuantForge research app")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dl = sub.add_parser("download", help="download earnings/prices/crypto (idempotent)")
    dl.add_argument("kind", choices=["earnings", "prices", "crypto"])
    dl.add_argument("--symbols", default="", help="comma-separated override (earnings/prices)")
    dl.add_argument("--workers", type=int, default=6)
    dl.add_argument("--force", action="store_true", help="re-download cached files")
    dl.add_argument("--max", type=int, default=0, help="cap on tickers (dev)")
    dl.add_argument("--chunk", type=int, default=40, help="prices: tickers per request")
    dl.add_argument("--years", type=int, default=6, help="prices: history in years")
    dl.add_argument("--spots", default="", help="crypto: spot markets override (ccxt symbols)")
    dl.add_argument("--perps", default="", help="crypto: perp markets override (ccxt symbols)")
    dl.set_defaults(fn=_download)

    sub.add_parser("import", help="rebuild duckdb raw tables + events from raw CSVs")
    sub.add_parser("import-crypto", help="load data/crypto/*.csv into crypto_ohlcv/funding/oi")
    sub.add_parser("import-options", help="load data/options/chains_*.csv into option_chains")
    sub.add_parser("events", help="rebuild only the SQL `events` table")
    sub.add_parser("study", help="run event study -> reports/surge_event_study_<date>.md")
    sub.add_parser("screen", help="rank current surge candidates -> reports/surge_screen_<date>.md")
    sub.add_parser("resonance", help="validate theme resonance (T3) -> reports/theme_resonance_<date>.md")
    sub.add_parser("repeats", help="validate repeat-winner persistence (T5) -> reports/repeat_surgers_<date>.md")
    sub.add_parser("composite", help="validate resonance x momentum composite (T7) -> reports/composite_signal_<date>.md")
    sub.add_parser("crypto", help="crypto research (momentum/vol/funding/OI) -> reports/crypto_research_<date>.md")
    sub.add_parser("options", help="live US-equity option-chain research -> reports/options_research_<date>.md")
    sub.add_parser("technical", help="multi-asset pure price/technical screen -> reports/technical_screen_<date>.md")
    sub.add_parser("status")
    sub.add_parser("verify")
    sub.add_parser("manifest")
    qp = sub.add_parser("query")
    qp.add_argument("sql")
    sp = sub.add_parser("snapshot")
    sp.add_argument("--keep", type=int, default=5)
    rp = sub.add_parser("restore")
    rp.add_argument("snap")
    # daily 子命令单独解析（带 --email/--no-email），避免与主 parser 参数冲突
    dly = sub.add_parser("daily", help="每日自动研究：crypto→options→technical→(邮件)")
    dly.add_argument("--email", action="store_true", help="生成后发送邮件")
    dly.add_argument("--no-email", action="store_true", help="跳过邮件")
    ep = sub.add_parser("email", help="邮件配置状态 / 发送测试（research email config|test）")
    ep.add_argument("action", nargs="?", choices=["config", "test"], default="config")
    for c in ("import", "import-crypto", "import-options", "events", "study", "screen",
              "resonance", "repeats", "composite", "crypto", "options", "technical",
              "status", "verify", "manifest", "query", "snapshot", "restore", "email",
              "daily"):
        sub.choices[c].set_defaults(fn=_cmd)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

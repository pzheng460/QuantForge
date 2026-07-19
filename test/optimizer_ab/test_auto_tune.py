from eval.auto_tune import (
    GateConfig,
    HealthMetrics,
    WindowHealth,
    build_evidence_report,
    build_orchestrate_command,
    decide_action,
    parse_window_specs,
    score_news_events,
)


def test_decide_action_reoptimizes_fragile_profitable_strategy():
    metrics = HealthMetrics(
        return_pct=0.1755,
        profit_factor=1.52,
        max_drawdown=0.138,
        win_rate=0.255,
        n_trades=47,
        largest_win=34216.70,
        net_profit=17552.50,
    )

    decision = decide_action(metrics, GateConfig())

    assert decision.action == "reoptimize"
    assert "single_trade_concentration" in decision.reasons
    assert "max_drawdown" in decision.reasons


def test_decide_action_observes_healthy_strategy():
    metrics = HealthMetrics(
        return_pct=0.12,
        profit_factor=1.7,
        max_drawdown=0.08,
        win_rate=0.42,
        n_trades=80,
        largest_win=2500,
        net_profit=12000,
    )

    decision = decide_action(metrics, GateConfig())

    assert decision.action == "observe"
    assert decision.reasons == []


def test_build_orchestrate_command_uses_cross_review_guided_defaults():
    cmd = build_orchestrate_command(
        strategy="ema_crossover",
        regime="trend_2024h1",
        seeds="1,2",
        providers="claude,codex",
        execute_holdout=True,
    )

    assert cmd[:4] == ["uv", "run", "python", "-m"]
    assert "eval.optimizer_ab.orchestrate" in cmd
    assert cmd[cmd.index("--methods") + 1] == "baseline,cross_review_guided"
    assert cmd[cmd.index("--agent-providers") + 1] == "claude,codex"
    assert "--no-holdout" not in cmd


def test_parse_window_specs_accepts_named_ranges():
    assert parse_window_specs(
        "recent:2024-07-01:2024-12-31,stress:2024-08-01:2024-09-30"
    ) == [
        ("recent", "2024-07-01", "2024-12-31"),
        ("stress", "2024-08-01", "2024-09-30"),
    ]


def test_evidence_report_aggregates_window_reasons():
    good = HealthMetrics(0.08, 1.6, 0.08, 0.38, 45, 1200, 8000)
    fragile = HealthMetrics(0.17, 1.52, 0.138, 0.255, 47, 34216.70, 17552.50)
    windows = [
        WindowHealth(
            "recent",
            "2024-07-01",
            "2024-12-31",
            good,
            decide_action(good, GateConfig()),
        ),
        WindowHealth(
            "stress",
            "2024-08-01",
            "2024-09-30",
            fragile,
            decide_action(fragile, GateConfig()),
        ),
    ]

    report = build_evidence_report(windows)

    assert report["worst_window"] == "stress"
    assert report["decision"]["action"] == "reoptimize"
    assert report["trigger_reasons"] == [
        "max_drawdown",
        "single_trade_concentration",
        "win_rate",
    ]


def test_news_events_raise_reoptimization_risk_for_relevant_shock():
    events = [
        {
            "title": "Bitcoin ETF inflows surge as volatility rises",
            "summary": "BTC market volatility and liquidation risk increased after macro news.",
            "symbols": ["BTC/USDT:USDT"],
            "source": "fixture",
            "published_at": "2024-12-01T00:00:00Z",
        },
        {
            "title": "Unrelated equity dividend update",
            "symbols": ["AAPL"],
            "source": "fixture",
        },
    ]

    risk = score_news_events(events, symbol="BTC/USDT:USDT")

    assert risk["risk_level"] == "high"
    assert risk["matched_events"] == 1
    assert "volatility" in risk["keywords"]


def test_external_events_fuse_structured_risk_components():
    events = [
        {
            "title": "bitget investigating major: API degraded performance",
            "summary": "Futures order API has elevated latency for BTCUSDT.",
            "symbols": ["BTC/USDT:USDT"],
            "source": "bitget",
        },
        {
            "title": "bitget funding high: BTC/USDT:USDT 0.1200%",
            "summary": "funding_rate=0.0012 may indicate crowded leverage.",
            "symbols": ["BTC/USDT:USDT"],
            "source": "bitget",
        },
        {
            "title": "coinglass liquidation spike: BTC/USDT:USDT long $25000000",
            "summary": "liquidation notional_usd=25000000 side=long.",
            "symbols": ["BTC/USDT:USDT"],
            "source": "coinglass",
        },
    ]

    risk = score_news_events(events, symbol="BTC/USDT:USDT")

    assert risk["risk_level"] == "high"
    assert risk["components"]["exchange_status"] > 0
    assert risk["components"]["funding"] > 0
    assert risk["components"]["liquidation"] > 0
    assert "exchange_status" in risk["reasons"]
    assert "liquidation" in risk["reasons"]

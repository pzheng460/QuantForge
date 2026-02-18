"""Tests for exchange profile lookup and cost_config generation."""

import pytest

from strategy.backtest.exchange_profiles import (
    ExchangeProfile,
    get_profile,
    list_exchanges,
)


class TestExchangeProfiles:
    """Tests for exchange profiles."""

    def test_list_exchanges_returns_all_five(self):
        """All five exchanges should be available."""
        exchanges = list_exchanges()
        assert "bitget" in exchanges
        assert "binance" in exchanges
        assert "okx" in exchanges
        assert "bybit" in exchanges
        assert "hyperliquid" in exchanges

    def test_get_profile_bitget(self):
        """Bitget profile should have correct defaults."""
        profile = get_profile("bitget")
        assert isinstance(profile, ExchangeProfile)
        assert profile.name == "Bitget"
        assert profile.ccxt_id == "bitget"
        assert profile.default_symbol == "BTC/USDT:USDT"
        assert profile.maker_fee == 0.0002
        assert profile.taker_fee == 0.0005

    def test_get_profile_binance(self):
        """Binance profile should have correct defaults."""
        profile = get_profile("binance")
        assert profile.name == "Binance"
        assert profile.ccxt_id == "binance"
        assert profile.taker_fee == 0.0004

    def test_get_unknown_profile_raises(self):
        """Looking up a non-existent exchange should raise KeyError."""
        with pytest.raises(KeyError):
            get_profile("nonexistent_exchange")

    def test_cost_config_generation(self):
        """cost_config() should return a CostConfig with correct fees."""
        from nexustrader.backtest import CostConfig

        profile = get_profile("bitget")
        cc = profile.cost_config()
        assert isinstance(cc, CostConfig)
        assert cc.maker_fee == profile.maker_fee
        assert cc.taker_fee == profile.taker_fee
        assert cc.slippage_pct == profile.slippage_pct

    def test_nexus_symbol(self):
        """nexus_symbol() should produce correct NexusTrader symbol format."""
        profile = get_profile("binance")
        sym = profile.nexus_symbol("BTCUSDT-PERP")
        assert sym == "BTCUSDT-PERP.BINANCE"

    def test_nexus_symbol_default(self):
        """nexus_symbol() default base should work."""
        profile = get_profile("okx")
        sym = profile.nexus_symbol()
        assert sym.endswith(".OKX")
        assert "BTCUSDT-PERP" in sym

    def test_all_profiles_have_required_fields(self):
        """Every profile should have all required fields populated."""
        for name in list_exchanges():
            profile = get_profile(name)
            assert profile.name, f"{name} missing display name"
            assert profile.ccxt_id, f"{name} missing ccxt_id"
            assert profile.default_symbol, f"{name} missing default_symbol"
            assert profile.nexus_symbol_suffix, f"{name} missing nexus_symbol_suffix"
            assert profile.maker_fee > 0, f"{name} maker_fee should be positive"
            assert profile.taker_fee > 0, f"{name} taker_fee should be positive"

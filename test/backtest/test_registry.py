"""Tests for strategy registration and lookup."""

import pytest

from strategy.backtest.registry import (
    HeatmapConfig,
    get_strategy,
    list_strategies,
)


class TestStrategyRegistry:
    """Tests for the global strategy registry."""

    def test_list_strategies_contains_all_three(self):
        """All three strategies should be registered after import."""
        # Importing strategy.strategies triggers registration
        import strategy.strategies  # noqa: F401

        names = list_strategies()
        assert "hurst_kalman" in names
        assert "ema_crossover" in names
        assert "bollinger_band" in names

    def test_get_strategy_hurst_kalman(self):
        """Hurst-Kalman strategy should be retrievable."""
        import strategy.strategies  # noqa: F401

        reg = get_strategy("hurst_kalman")
        assert reg.name == "hurst_kalman"
        assert reg.display_name == "Hurst-Kalman"
        assert reg.signal_generator_cls is not None
        assert reg.config_cls is not None
        assert reg.filter_config_cls is not None
        assert reg.default_grid is not None
        assert reg.heatmap_config is not None

    def test_get_strategy_ema_crossover(self):
        """EMA Crossover strategy should be retrievable."""
        import strategy.strategies  # noqa: F401

        reg = get_strategy("ema_crossover")
        assert reg.name == "ema_crossover"
        assert reg.display_name == "EMA Crossover"

    def test_get_strategy_bollinger_band(self):
        """Bollinger Band strategy should be retrievable."""
        import strategy.strategies  # noqa: F401

        reg = get_strategy("bollinger_band")
        assert reg.name == "bollinger_band"
        assert reg.display_name == "Bollinger Band"

    def test_get_unknown_strategy_raises(self):
        """Looking up a non-existent strategy should raise KeyError."""
        with pytest.raises(KeyError):
            get_strategy("nonexistent_strategy")

    def test_heatmap_config_fields(self):
        """HeatmapConfig should have required fields."""
        import strategy.strategies  # noqa: F401

        reg = get_strategy("hurst_kalman")
        hmc = reg.heatmap_config
        assert isinstance(hmc, HeatmapConfig)
        assert hmc.x_param_name == "zscore_entry"
        assert hmc.y_param_name == "hurst_window"
        assert len(hmc.x_range) == 2
        assert len(hmc.y_range) == 2
        assert hmc.x_label
        assert hmc.y_label

    def test_registration_has_split_params_fn(self):
        """Each registration should have a split_params_fn."""
        import strategy.strategies  # noqa: F401

        for name in list_strategies():
            reg = get_strategy(name)
            assert reg.split_params_fn is not None, f"{name} missing split_params_fn"

    def test_registration_has_mesa_dict_to_config_fn(self):
        """Each registration should have a mesa_dict_to_config_fn."""
        import strategy.strategies  # noqa: F401

        for name in list_strategies():
            reg = get_strategy(name)
            assert reg.mesa_dict_to_config_fn is not None, (
                f"{name} missing mesa_dict_to_config_fn"
            )

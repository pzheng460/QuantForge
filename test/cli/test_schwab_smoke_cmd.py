from click.testing import CliRunner

from quantforge.cli.main import cli


def test_schwab_verify_is_explicitly_read_only(monkeypatch):
    class FakeConnector:
        authenticated = True
        trading_authenticated = True
        market_data_authenticated = True
        account_hash = "hash"

        def get_account_snapshot(self):
            return {"securitiesAccount": {"positions": []}}

        def get_quote_price(self, symbol):
            assert symbol == "TSLA"
            return 350.0

        def get_option_chain(self, symbol, **_kwargs):
            assert symbol == "TSLA"
            return {"status": "SUCCESS"}

    monkeypatch.setattr(
        "quantforge.cli.commands.schwab_cmd._connector",
        lambda: FakeConnector(),
    )

    result = CliRunner().invoke(cli, ["schwab", "verify", "--symbol", "TSLA"])

    assert result.exit_code == 0
    assert '"mode": "read_only"' in result.output
    assert '"option_chain_status": "SUCCESS"' in result.output

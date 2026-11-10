import json
from datetime import date
from decimal import Decimal

import pytest
import requests

from src.currency import FxRates, PriceParseError, parse_price


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("1.299,00 €", "1299.00", "EUR"),
        ("1 299,00 zł", "1299.00", "PLN"),
        ("€1,299.00", "1299.00", "EUR"),
        ("1299 Kč", "1299", "CZK"),
        ("4 190 Kč", "4190", "CZK"),  # non-breaking spaces
        ("€ 389,00", "389.00", "EUR"),
        ("169,-", "169", None),  # Dutch "whole euros" notation
        ("12,99 €", "12.99", "EUR"),
        ("1.299 €", "1299", "EUR"),  # dot as thousands separator
        ("1,299 €", "1299", "EUR"),  # comma as thousands separator
        ("2 649,5 zł", "2649.5", "PLN"),
        ("EUR 1.234.567,89", "1234567.89", "EUR"),
        ("CZK 12 490", "12490", "CZK"),
    ],
)
def test_parse_price(text, amount, currency):
    assert parse_price(text) == (Decimal(amount), currency)


def test_parse_price_rejects_text_without_number():
    with pytest.raises(PriceParseError):
        parse_price("Preis auf Anfrage")


def _fx_with_cache(tmp_path, payload):
    path = tmp_path / "fx.json"
    path.write_text(json.dumps(payload))
    return FxRates(cache_path=path)


def test_fx_uses_todays_cache_without_network(tmp_path, monkeypatch):
    fx = _fx_with_cache(tmp_path, {"fetched": date.today().isoformat(), "ecb_date": "2026-11-09",
                                   "rates": {"PLN": 4.0, "CZK": 25.0}})
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("network must not be used"))
    fx.load({"PLN", "CZK", "EUR"})
    assert fx.to_eur(Decimal("100"), "PLN") == Decimal("25.00")
    assert fx.to_eur(Decimal("100"), "EUR") == Decimal("100.00")


def test_fx_falls_back_to_last_saved_rates_when_api_down(tmp_path, monkeypatch, caplog):
    fx = _fx_with_cache(tmp_path, {"fetched": "2026-01-01", "ecb_date": "2025-12-31",
                                   "rates": {"PLN": 4.0}})

    def down(*args, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", down)
    fx.load({"PLN"})
    assert fx.as_of == "2025-12-31"
    assert fx.to_eur(Decimal("10"), "PLN") == Decimal("2.50")
    assert "using saved rates" in caplog.text


def test_fx_without_cache_and_api_raises(tmp_path, monkeypatch):
    fx = FxRates(cache_path=tmp_path / "missing.json")
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()))
    with pytest.raises(RuntimeError):
        fx.load({"PLN"})

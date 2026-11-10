"""Locale-aware price parsing and conversion to EUR with ECB reference rates."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# Frankfurter serves the ECB reference rates (published once per working day).
FX_API_URL = "https://api.frankfurter.dev/v1/latest"
FX_CACHE_PATH = Path("data/fx_rates.json")

CURRENCY_MARKERS = {
    "€": "EUR",
    "EUR": "EUR",
    "zł": "PLN",
    "PLN": "PLN",
    "Kč": "CZK",
    "CZK": "CZK",
    "£": "GBP",
    "GBP": "GBP",
    "CHF": "CHF",
}
_NUMBER_RE = re.compile(r"\d[\d\s  .,']*")


class PriceParseError(ValueError):
    pass


def parse_price(text: str) -> tuple[Decimal, str | None]:
    """Parse a human-formatted price into (amount, ISO currency or None).

    Handles European and English conventions, e.g.
    "1.299,00 €", "1 299,00 zł", "€1,299.00", "1299 Kč", "169,-".
    """
    currency = next((iso for marker, iso in CURRENCY_MARKERS.items() if marker in text), None)
    match = _NUMBER_RE.search(text)
    if not match:
        raise PriceParseError(f"no number in {text!r}")
    return _to_decimal(match.group()), currency


def _to_decimal(raw: str) -> Decimal:
    number = re.sub(r"[\s  ']", "", raw).rstrip(".,")
    if "," in number and "." in number:
        # whichever separator comes last is the decimal one
        decimal_sep = "," if number.rfind(",") > number.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        number = number.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in number or "." in number:
        sep = "," if "," in number else "."
        head, _, tail = number.rpartition(sep)
        if number.count(sep) == 1 and len(tail) in (1, 2):
            number = f"{head}.{tail}"  # "12,99" / "12.5" -> decimal
        else:
            number = number.replace(sep, "")  # "1.299" / "1,299,000" -> thousands
    try:
        return Decimal(number)
    except ArithmeticError as exc:
        raise PriceParseError(f"cannot parse {raw!r}") from exc


class FxRates:
    """Daily ECB rates (units of currency per 1 EUR) with an on-disk cache.

    Rates are fetched at most once per day. If the API is unreachable, the
    last saved rates are used and a warning is logged.
    """

    def __init__(self, cache_path: Path = FX_CACHE_PATH) -> None:
        self.cache_path = cache_path
        self.rates: dict[str, Decimal] = {"EUR": Decimal(1)}
        self.as_of: str | None = None

    def load(self, currencies: set[str]) -> None:
        wanted = currencies - {"EUR"}
        if not wanted:
            return
        cached = self._read_cache()
        if cached and cached["fetched"] == date.today().isoformat() and wanted <= cached["rates"].keys():
            self._apply(cached)
            log.info("FX rates from today's cache (ECB %s)", self.as_of)
            return
        try:
            resp = requests.get(FX_API_URL, params={"base": "EUR", "symbols": ",".join(sorted(wanted))}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            fresh = {"fetched": date.today().isoformat(), "ecb_date": data["date"], "rates": data["rates"]}
            self._write_cache(fresh)
            self._apply(fresh)
            log.info("FX rates fetched (ECB %s): %s", self.as_of, fresh["rates"])
        except (requests.RequestException, KeyError, ValueError) as exc:
            if not cached:
                raise RuntimeError("FX API unavailable and no cached rates") from exc
            self._apply(cached)
            log.warning("FX API unavailable (%s) — using saved rates from ECB %s", exc, self.as_of)

    def to_eur(self, amount: Decimal, currency: str) -> Decimal | None:
        rate = self.rates.get(currency)
        if rate is None:
            log.error("No FX rate for %s", currency)
            return None
        return (amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _apply(self, payload: dict) -> None:
        self.rates.update({cur: Decimal(str(rate)) for cur, rate in payload["rates"].items()})
        self.as_of = payload["ecb_date"]

    def _read_cache(self) -> dict | None:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _write_cache(self, payload: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

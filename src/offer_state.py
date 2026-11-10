"""Last known state of every offer, with `last_seen`.

Keeps the most recent observation of each offer URL. When a site is stale
today, its last known offers are carried into today's dataset (flagged
`stale`, with their original `last_seen`), so a blocked or half-loaded site
never looks like "all products disappeared".
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from src.models import Product

log = logging.getLogger(__name__)

STATE_PATH = Path("data/offers_latest.json")
FORGET_AFTER_DAYS = 30

_STORED_FIELDS = ("site", "country", "name", "price", "currency", "url", "in_stock", "model", "ean",
                  "brand", "seller", "is_official", "condition", "last_seen")


class OfferState:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        self.offers: dict[str, dict] = {}
        if path.exists():
            self.offers = json.loads(path.read_text(encoding="utf-8"))

    def merge_today(self, fresh: list[Product], stale_sites: set[str], today: date | None = None) -> list[Product]:
        """Return today's dataset: fresh offers + carried-over offers of stale sites."""
        today = today or date.today()
        fresh_urls = {p.url for p in fresh}
        for product in fresh:
            product.last_seen = today
            product.stale = False

        carried = []
        for url, row in self.offers.items():
            if row["site"] in stale_sites and url not in fresh_urls:
                carried.append(self._to_product(row, today))
        if carried:
            log.warning("Carrying over %d last-known offers from stale sites %s", len(carried), sorted(stale_sites))

        for product in fresh:
            self.offers[product.url] = self._to_row(product)
        self._forget_old(today)
        return fresh + carried

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(sorted(self.offers.items()))
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def _forget_old(self, today: date) -> None:
        cutoff = (today - timedelta(days=FORGET_AFTER_DAYS)).isoformat()
        old = [url for url, row in self.offers.items() if row["last_seen"] < cutoff]
        for url in old:
            del self.offers[url]

    @staticmethod
    def _to_row(product: Product) -> dict:
        row = {name: getattr(product, name) for name in _STORED_FIELDS}
        row["price"] = str(product.price)
        row["last_seen"] = product.last_seen.isoformat()
        return row

    @staticmethod
    def _to_product(row: dict, today: date) -> Product:
        kwargs = {k: v for k, v in row.items() if k in _STORED_FIELDS}
        kwargs["price"] = Decimal(row["price"])
        kwargs["last_seen"] = date.fromisoformat(row["last_seen"])
        return Product(**kwargs, date=today, stale=True)

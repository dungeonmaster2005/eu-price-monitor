"""Core data model shared by scrapers, analysis and the Sheets writer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class Product:
    """One offer for one product on one shop, as seen on a given day."""

    site: str
    country: str
    name: str
    price: Decimal | None  # None only while a listing hides it (filled from product page)
    currency: str
    url: str
    in_stock: bool | None = None
    model: str | None = None  # manufacturer part number (MPN)
    ean: str | None = None  # EAN / GTIN-13
    brand: str | None = None
    seller: str | None = None  # who sells the offer (shop itself or marketplace partner)
    is_official: bool = True  # False for third-party marketplace offers
    condition: str = "new"  # new | open_box | refurbished | used
    price_eur: Decimal | None = None
    base_model: str | None = None  # colour-agnostic key grouping variants
    match_id: str | None = None  # same id = same product across shops
    match_method: str | None = None  # ean | mpn | name | manual | none
    date: date = field(default_factory=date.today)
    last_seen: date | None = None  # last day the offer was actually observed
    stale: bool = False  # carried over from an earlier run (site blocked / incomplete today)

    def to_row(self) -> dict[str, Any]:
        """Flat, JSON/CSV-friendly representation."""
        row = asdict(self)
        row["date"] = self.date.isoformat()
        row["last_seen"] = (self.last_seen or self.date).isoformat()
        row["price"] = None if self.price is None else str(self.price)
        row["price_eur"] = None if self.price_eur is None else str(self.price_eur)
        return row

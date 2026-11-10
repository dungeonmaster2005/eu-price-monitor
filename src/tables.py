"""Builds the spreadsheet tables (pure data, no Google API) from today's offers.

Rules shared by every table:
- prices are as shown by the shops: VAT included, shipping excluded;
- cross-country comparisons use official sellers' in-stock offers only;
- offers from stale sites carry their last known price and are flagged.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from src.identifiers import COLOURS, canonical_brand, colours_in, strip_accents
from src.models import Product
from src.site_status import SiteStatus

COUNTRIES = ["PL", "DE", "NL", "CZ", "FR"]
PRICE_NOTE = "Prices include VAT, excluding shipping."
COMPARE_NOTE = "Only official sellers' in-stock offers are compared; click a price to open the shop page."
# Shops whose product names read best in a pan-European table (no category prefix).
_NAME_PREFERENCE = ["coolblue", "ldlc", "alternate_de", "mironet", "morele"]
_CATEGORY_PREFIXES = ("Słuchawki ", "Sluchátka ")


@dataclass(frozen=True)
class Link:
    """A numeric cell that should link to a URL (written as =HYPERLINK in Sheets)."""

    value: float
    url: str


@dataclass(frozen=True)
class Column:
    header: str
    kind: str = "text"  # text | eur | price | number | pct | delta | date | bool
    width: int = 110


def column_index(columns: list[Column], header: str) -> int:
    return next(i for i, c in enumerate(columns) if c.header == header)


def _truthy(value: Any) -> bool:
    """Booleans read back from Sheets arrive as 'TRUE'/'FALSE' strings."""
    return value is True or str(value).upper() == "TRUE"


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def display_name(offers: list[Product]) -> str:
    """Most readable name among the offers of one product."""
    rank = {site: i for i, site in enumerate(_NAME_PREFERENCE)}
    best = min(offers, key=lambda p: (rank.get(p.site, 99), len(p.name)))
    name = best.name
    for prefix in _CATEGORY_PREFIXES:
        name = name.removeprefix(prefix)
    return name


# Shop-language fragments -> English, for the pan-European product title.
_PHRASES = [
    (r",\s*(?:Gaming-|In-Ear-|Over-Ear-|On-Ear-)?(?:Headset|Kopfhörer)\b", ""),
    (r"\bmet draadloze oplaadcase\b", "with Wireless Charging Case"),
    (r"\bmit kabellosem Ladecase\b", "with Wireless Charging Case"),
    (r"\bmet oplaadcase\b", "with Charging Case"),
    (r"\b(?:Generalüberholt|Reconditionné|Odnowione)\b", "Refurbished"),
    (r"\(?\b(\d)\s*(?:e|nd|rd|th|st|\.)?\s*gen(?:eration|eratie|\.)?\)?", r"(Gen \1)"),
]


def english_name(offers: list[Product]) -> str:
    """Brand + model + colour in English, e.g. 'Sony WH-1000XM6 Black'."""
    name = display_name(offers)
    for pattern, replacement in _PHRASES:
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)

    words, colours = [], []
    for word in name.split():
        parts = [strip_accents(w).lower() for w in word.strip(",()").split("-")]
        if parts and all(part in COLOURS for part in parts):
            colours += [COLOURS[part] for part in parts if COLOURS[part] not in colours]
        else:
            words.append(word)
    if not colours:  # take the colour from another shop's name for the same product
        colours = next((sorted(colours_in(p.name)) for p in offers if colours_in(p.name)), [])

    title = " ".join(words).strip(" ,")
    brand = canonical_brand(offers[0].brand, offers[0].name)
    if brand and not re.sub(r"[^a-z0-9]", "", strip_accents(title).lower()).startswith(brand):
        title = f"{_brand_label(offers, brand)} {title}"
    return f"{title} {'/'.join(c.title() for c in colours)}".strip()


def _brand_label(offers: list[Product], brand_key: str) -> str:
    for p in offers:
        if p.brand and re.sub(r"[^a-z0-9]", "", strip_accents(p.brand).lower()) == brand_key:
            return p.brand
    return brand_key.title()


def comparable(offers: list[Product]) -> list[Product]:
    """Offers that take part in cross-country comparison."""
    return [p for p in offers if p.is_official and p.in_stock is not False and p.price_eur is not None]


def country_prices(offers: list[Product]) -> dict[str, Decimal]:
    """Cheapest comparable EUR price per country."""
    prices: dict[str, Decimal] = {}
    for p in comparable(offers):
        prices[p.country] = min(prices.get(p.country, p.price_eur), p.price_eur)
    return prices


def cheapest_offers(offers: list[Product]) -> dict[str, Product]:
    """The cheapest comparable offer per country (its URL is linked from the price)."""
    best: dict[str, Product] = {}
    for p in comparable(offers):
        if p.country not in best or p.price_eur < best[p.country].price_eur:
            best[p.country] = p
    return best


def group_by_product(products: list[Product]) -> dict[str, list[Product]]:
    groups: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        groups[p.match_id or p.url].append(p)
    return groups


# --- today / history ------------------------------------------------------------

OFFER_COLUMNS = [
    Column("Date", "date", 90), Column("Country", width=70), Column("Site", width=100),
    Column("Product", width=320), Column("Model (MPN)", width=130), Column("EAN", width=120),
    Column("Price", "price", 85), Column("Currency", width=75), Column("Price EUR", "eur", 95),
    Column("In stock", "bool", 75), Column("Seller", width=140), Column("Official", "bool", 75),
    Column("Match ID", width=190), Column("Base model", width=170), Column("Last seen", "date", 90),
    Column("Stale", "bool", 60), Column("URL", width=300),
]


def offer_rows(products: list[Product]) -> list[list[Any]]:
    rows = []
    for p in sorted(products, key=lambda p: (p.country, p.site, p.name)):
        rows.append([
            p.date.isoformat(), p.country, p.site, p.name, p.model or "", p.ean or "",
            _f(p.price), p.currency, _f(p.price_eur), p.in_stock, p.seller or "", p.is_official,
            p.match_id or "", p.base_model or "", (p.last_seen or p.date).isoformat(), p.stale, p.url,
        ])
    return rows


# --- compare --------------------------------------------------------------------

COMPARE_COLUMNS = [
    Column("Product", width=320), *[Column(c, "eur", 85) for c in COUNTRIES],
    Column("Min EUR", "eur", 90), Column("Cheapest at", width=150), Column("Spread", "pct", 80),
    Column("Countries", "number", 80), Column("Notes", width=220),
]


def compare_rows(products: list[Product]) -> list[list[Any]]:
    """One row per product offered in 2+ countries, sorted by price spread."""
    rows = []
    for offers in group_by_product(products).values():
        prices = country_prices(offers)
        if len(prices) < 2:
            continue
        low, high = min(prices.values()), max(prices.values())
        best = cheapest_offers(offers)
        cheapest = sorted(c for c, v in prices.items() if v == low)
        stale = sorted({p.country for p in comparable(offers) if p.stale})
        notes = f"last known price for {', '.join(stale)} (site stale)" if stale else ""
        rows.append([
            english_name(offers),
            *[Link(float(best[c].price_eur), best[c].url) if c in best else None for c in COUNTRIES],
            Link(float(low), best[cheapest[0]].url),
            ", ".join(f"{best[c].site} ({c})" for c in cheapest), float((high - low) / low),
            len(prices), notes,
        ])
    spread = column_index(COMPARE_COLUMNS, "Spread")
    rows.sort(key=lambda r: r[spread], reverse=True)
    return rows


def cell_value(cell: Any) -> Any:
    return cell.value if isinstance(cell, Link) else cell


# --- summary --------------------------------------------------------------------

SUMMARY_COLUMNS = [
    Column("Product", width=320), Column("Min EUR", "eur", 90), Column("Site", width=110),
    Column("Country", width=70), Column("vs yesterday", "delta", 95), Column("vs 7 days", "delta", 90),
    Column("Most expensive country", width=150), Column("Above min", "pct", 90),
    Column("Countries", "number", 80), Column("Match ID", width=190),
]


def daily_minimums(history: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """{match_id: {date: cheapest official in-stock EUR price}} from history rows."""
    result: dict[str, dict[str, float]] = defaultdict(dict)
    for row in history:
        in_stock = row.get("In stock")
        if not _truthy(row.get("Official")) or (in_stock not in ("", None) and not _truthy(in_stock)):
            continue
        if row.get("Price EUR") in (None, ""):
            continue
        mid, day, price = row["Match ID"], row["Date"], float(row["Price EUR"])
        if mid and (day not in result[mid] or price < result[mid][day]):
            result[mid][day] = price
    return result


def _change(now: float, before: float | None) -> float | None:
    return None if not before else (now - before) / before


def _price_on_or_before(series: dict[str, float], day: date) -> float | None:
    earlier = [d for d in series if d <= day.isoformat()]
    return series[max(earlier)] if earlier else None


def summary_rows(products: list[Product], history: list[dict[str, Any]], today: date) -> list[list[Any]]:
    """Per product: cheapest offer in Europe and how the minimum moved."""
    minimums = daily_minimums(history)
    rows = []
    for match_id, offers in group_by_product(products).items():
        prices = country_prices(offers)
        if not prices:
            continue
        low = min(prices.values())
        best = min((p for p in comparable(offers) if p.price_eur == low), key=lambda p: p.site)
        top_country = max(prices, key=prices.get)
        series = {d: v for d, v in minimums.get(match_id, {}).items() if d < today.isoformat()}
        yesterday = series[max(series)] if series else None
        week_ago = _price_on_or_before(series, today - timedelta(days=7))
        rows.append([
            display_name(offers), _f(low), best.site, best.country,
            _change(float(low), yesterday), _change(float(low), week_ago),
            top_country if len(prices) > 1 else "", float((prices[top_country] - low) / low) if len(prices) > 1 else None,
            len(prices), match_id,
        ])
    rows.sort(key=lambda r: (-r[8], r[0]))
    return rows


# --- status ---------------------------------------------------------------------

STATUS_COLUMNS = [
    Column("Site", width=110), Column("Country", width=70), Column("Status", width=80),
    Column("Last successful run", "date", 140), Column("Products today", "number", 110),
    Column("Usual count", "number", 95), Column("Notes", width=520),
]


def status_rows(statuses: list[SiteStatus]) -> list[list[Any]]:
    return [
        [s.site, s.country, "STALE" if s.stale else "OK", s.last_success or "never",
         s.count_today, s.usual_count, s.reason]
        for s in sorted(statuses, key=lambda s: COUNTRIES.index(s.country) if s.country in COUNTRIES else 99)
    ]

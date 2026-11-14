"""Helpers for schema.org JSON-LD blocks, which most shops embed for SEO."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup

from src.id_cache import ProductIds
from src.scrapers.base import ProductPage

log = logging.getLogger(__name__)


def iter_jsonld(soup: BeautifulSoup) -> Iterator[dict[str, Any]]:
    """Yield every JSON object (including nested ones) from ld+json scripts."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            log.debug("Skipping malformed JSON-LD block")
            continue
        yield from _walk(data)


def find_typed(soup: BeautifulSoup, schema_type: str) -> list[dict[str, Any]]:
    """All JSON-LD objects whose @type equals (or contains) `schema_type`."""
    found = []
    for obj in iter_jsonld(soup):
        obj_type = obj.get("@type")
        types = obj_type if isinstance(obj_type, list) else [obj_type]
        if schema_type in types:
            found.append(obj)
    return found


def first_gtin(product: dict[str, Any]) -> str | None:
    """Return the product's GTIN/EAN from any of the gtin* keys, digits only."""
    for key in ("gtin13", "gtin", "gtin14", "gtin12", "gtin8", "ean"):
        value = product.get(key)
        if value:
            digits = "".join(ch for ch in str(value) if ch.isdigit())
            if len(digits) >= 8:
                return digits
    return None


def brand_name(product: dict[str, Any]) -> str | None:
    brand = product.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    return str(brand).strip() if brand else None


def is_in_stock(availability: str | None) -> bool | None:
    """Map a schema.org availability URL to a boolean (None if unknown)."""
    if not availability:
        return None
    value = availability.rsplit("/", 1)[-1].lower()
    if value in {"instock", "limitedavailability", "onlineonly", "presale", "preorder"}:
        return True
    if value in {"outofstock", "soldout", "discontinued", "backorder"}:
        return False
    return None


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


_NOT_CURRENT = ("StrikethroughPrice", "ListPrice", "MSRP")


def current_price_spec(offer: dict[str, Any]) -> dict[str, Any]:
    """The selling-price entry of `priceSpecification`, skipping crossed-out/list prices."""
    specs = offer.get("priceSpecification") or []
    specs = specs if isinstance(specs, list) else [specs]
    for spec in specs:
        if isinstance(spec, dict) and not str(spec.get("priceType", "")).endswith(_NOT_CURRENT):
            return spec
    return {}


def offer_currency(offer: dict[str, Any]) -> str | None:
    return offer.get("priceCurrency") or current_price_spec(offer).get("priceCurrency")


def offer_price(offer: dict[str, Any]) -> Decimal | None:
    """Price of a schema.org Offer as Decimal.

    Reads `price`, an AggregateOffer's `lowPrice`, or the current entry of
    `priceSpecification` (a sale lists the old price as StrikethroughPrice).
    """
    value = offer.get("price", offer.get("lowPrice"))
    if value in (None, ""):
        value = current_price_spec(offer).get("price")
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def clean_mpn(mpn: object, brand: str | None) -> str | None:
    """Reject MPN values that are really a brand name or an EAN."""
    value = str(mpn or "").strip()
    if not value or value.isdigit() or (brand and value.lower() == brand.lower()):
        return None
    return value


def product_page_from_jsonld(soup: BeautifulSoup) -> ProductPage:
    """Generic ProductPage from a schema.org Product block (EAN, MPN, brand, stock)."""
    for item in find_typed(soup, "Product"):
        offers = item.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        brand = brand_name(item)
        if item.get("mpn") or first_gtin(item) or offers:
            return ProductPage(
                ids=ProductIds(ean=first_gtin(item), model=clean_mpn(item.get("mpn"), brand), brand=brand),
                in_stock=is_in_stock(offers.get("availability")),
                price=offer_price(offers),
                currency=offer_currency(offers),
            )
    return ProductPage()

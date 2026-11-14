"""planeo.cz (CZ) — server-rendered catalogue, prices in "3 299 Kč".

Replaced mironet.cz on 2026-11-14 (mironet answers GitHub runner IPs with a
captcha). Listing card (div.c-product--catalogue): name, URL, current price and
delivery availability. A discounted card also shows the old, crossed-out price;
only `[data-testid="catalogue.item.price"]` is the current one.
Product page: JSON-LD Product with gtin (EAN), brand and price.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from src.currency import PriceParseError, parse_price
from src.models import Product
from src.scrapers.base import BaseScraper, ProductPage
from src.scrapers.html import absolute, text_of
from src.scrapers.jsonld import product_page_from_jsonld

BASE_URL = "https://www.planeo.cz"
PAGE_SIZE = 24  # products per listing page; pages are addressed by ?offset=
_OUT_OF_STOCK = re.compile(r"nedostupn|vyprodan|neni skladem|není skladem|na objednávku", re.IGNORECASE)


class PlaneoScraper(BaseScraper):
    def page_url(self, start_url: str, page: int) -> str:
        return start_url if page == 1 else f"{start_url}?offset={(page - 1) * PAGE_SIZE}"

    def parse_listing(self, soup: BeautifulSoup) -> list[Product]:
        products = []
        for card in soup.select("div.c-product--catalogue"):
            product = self._parse_card(card)
            if product:
                products.append(product)
        return products

    def parse_details(self, soup: BeautifulSoup) -> ProductPage:
        return product_page_from_jsonld(soup)

    def _parse_card(self, card: Tag) -> Product | None:
        link = card.select_one(".c-product__title a[href]") or card.select_one(".c-product__image a[href]")
        name = text_of(card.select_one('[data-testid="catalogue.item.title"]')) or card.get("data-gtm-product-name")
        price_text = text_of(card.select_one('[data-testid="catalogue.item.price"]'))
        if link is None or not name or not price_text:
            return None
        try:
            price, currency = parse_price(price_text)
        except PriceParseError:
            self.log.warning("Unparseable price %r", price_text)
            return None
        return Product(
            site=self.site_id,
            country=self.country,
            name=name,
            price=price,
            currency=currency or self.currency,
            url=absolute(BASE_URL, link["href"]),
            in_stock=delivery_to_stock(card),
            brand=card.get("data-gtm-product-brand") or None,
            seller="Planeo",
        )


def delivery_to_stock(card: Tag) -> bool | None:
    """Stock flag from the card's delivery availability (None if unknown).

    Unknown stock is read from the product page JSON-LD (`stock_from_details`).
    """
    state = card.select_one('[data-testid="catalogue.item.availability.delivery.text"]')
    if state is None:
        return None
    if "c--success" in (state.get("class") or []):
        return True
    delivery = text_of(card.select_one(".c-product__availability--delivery")) or ""
    return False if _OUT_OF_STOCK.search(delivery) else None

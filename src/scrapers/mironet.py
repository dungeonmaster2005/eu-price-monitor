"""mironet.cz (CZ) — server-rendered grid, prices in "4 190 Kč".

Listing card (div.item_b): name, short description, price. Stock on the
listing is filled in later by JavaScript from an internal endpoint, which we
do not call; instead stock is read from the product page JSON-LD on every
run (`stock_from_details: true` in sites.yaml).
Product page: JSON-LD Product with mpn (no EAN published) and availability.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from src.currency import PriceParseError, parse_price
from src.models import Product
from src.scrapers.base import BaseScraper, ProductPage
from src.scrapers.html import absolute, text_of
from src.scrapers.jsonld import product_page_from_jsonld

BASE_URL = "https://www.mironet.cz"


class MironetScraper(BaseScraper):
    def page_url(self, start_url: str, page: int) -> str:
        return start_url if page == 1 else f"{start_url}?PgID={page}"

    def parse_listing(self, soup: BeautifulSoup) -> list[Product]:
        products = []
        for card in soup.select("#productContainer div.item_b"):
            product = self._parse_card(card)
            if product:
                products.append(product)
        return products

    def parse_details(self, soup: BeautifulSoup) -> ProductPage:
        return product_page_from_jsonld(soup)

    def _parse_card(self, card: Tag) -> Product | None:
        link = card.select_one("a.item_b_name")
        price_text = text_of(card.select_one(".item_b_cena"))
        if link is None or not price_text:
            return None
        try:
            price, currency = parse_price(price_text)
        except PriceParseError:
            self.log.warning("Unparseable price %r", price_text)
            return None
        return Product(
            site=self.site_id,
            country=self.country,
            name=text_of(link),
            price=price,
            currency=currency or self.currency,
            url=absolute(BASE_URL, link["href"]),
            in_stock=None,  # filled from the product page
            seller="Mironet",
        )

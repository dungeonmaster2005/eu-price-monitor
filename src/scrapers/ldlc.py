"""ldlc.com (FR).

Listing cards (li.pdt-item) give name, URL and seller type: LDLC's own
offers have "AR…"/"PB…" ids, marketplace partners "MK…". Price and stock are
in the JSON-LD ItemList on page 1 only; on later pages they are loaded by
JavaScript, so for those offers they are read from the product page JSON-LD.
Product page: JSON-LD Product with gtin13, mpn, price and availability.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from src.models import Product
from src.scrapers.base import BaseScraper, ProductPage
from src.scrapers.html import absolute, text_of
from src.scrapers.jsonld import find_typed, is_in_stock, offer_price, product_page_from_jsonld

BASE_URL = "https://www.ldlc.com"
MARKETPLACE_SELLER = "LDLC Marketplace partner"


class LdlcScraper(BaseScraper):
    def page_url(self, start_url: str, page: int) -> str:
        if page == 1:
            return start_url
        # /c4380/+fc194-1.html -> /c4380/page2/+fc194-1.html ; /c4380/ -> /c4380/page2/
        return re.sub(r"(/c\d+/)", rf"\1page{page}/", start_url, count=1)

    def parse_listing(self, soup: BeautifulSoup) -> list[Product]:
        offers = self._jsonld_offers(soup)
        products = []
        for card in soup.select("li.pdt-item"):
            link = card.select_one("a[href*='/fiche/']")
            name = text_of(card.select_one("h3"))
            if link is None or not name:
                continue
            url = absolute(BASE_URL, link["href"])
            offer = offers.get(url, {})
            is_marketplace = card.get("data-id", "").startswith("MK")
            products.append(
                Product(
                    site=self.site_id,
                    country=self.country,
                    name=name,
                    price=offer_price(offer),
                    currency=offer.get("priceCurrency") or self.currency,
                    url=url,
                    in_stock=is_in_stock(offer.get("availability")),
                    seller=MARKETPLACE_SELLER if is_marketplace else "LDLC",
                )
            )
        return products

    def parse_details(self, soup: BeautifulSoup) -> ProductPage:
        return product_page_from_jsonld(soup)

    @staticmethod
    def _jsonld_offers(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
        offers = {}
        for item_list in find_typed(soup, "ItemList"):
            for element in item_list.get("itemListElement", []):
                item = element.get("item", element)
                if item.get("url"):
                    offers[absolute(BASE_URL, item["url"])] = item.get("offers") or {}
        return offers

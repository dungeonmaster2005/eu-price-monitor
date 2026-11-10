"""morele.net (PL) — server-rendered listing with a schema.org ItemList.

Listing page: name, URL and price come from the JSON-LD ItemList; stock and
the marketplace seller come from the product cards (matched by product id).
Product page: JSON-LD Product carries gtin13 (EAN) and mpn (manufacturer code).
morele is a marketplace: offers not sold by "Morele.net" are third-party.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from src.models import Product
from src.scrapers.base import BaseScraper, ProductPage
from src.scrapers.jsonld import find_typed, product_page_from_jsonld

PRODUCT_ID_RE = re.compile(r"-(\d+)/?$")
# a bracketed token with at least one digit and one letter, e.g. "(SM-R640NZKAEUB)"
NAME_CODE_RE = re.compile(r"\(((?=[^)]*\d)(?=[^)]*[A-Za-z])[A-Za-z0-9/.\-]{4,})\)\s*$")


class MoreleScraper(BaseScraper):
    def page_url(self, start_url: str, page: int) -> str:
        if page == 1:
            return start_url
        # morele encodes filters as commas; the trailing segment is the page number
        return f"{start_url.rstrip('/')}/,,,,,,,,0,,,,/{page}/"

    def parse_listing(self, soup: BeautifulSoup) -> list[Product]:
        cards = self._cards_by_id(soup)
        products = []
        for item_list in find_typed(soup, "ItemList"):
            for element in item_list.get("itemListElement", []):
                product = self._product_from_item(element.get("item", element), cards)
                if product:
                    products.append(product)
        return products

    def parse_details(self, soup: BeautifulSoup) -> ProductPage:
        page = product_page_from_jsonld(soup)
        if not page.ids.model:
            names = [p.get("name", "") for p in find_typed(soup, "Product")]
            page.ids.model = next(filter(None, map(model_from_name, names)), None)
        return page

    # -- helpers -----------------------------------------------------------

    def _product_from_item(self, item: dict, cards: dict[str, dict]) -> Product | None:
        url, name = item.get("url"), item.get("name")
        offer = item.get("offers") or {}
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        try:
            price = Decimal(str(offer["price"]))
        except (KeyError, InvalidOperation):
            self.log.debug("No usable price for %s", url)
            return None
        if not url or not name:
            return None

        card = cards.get(_product_id(url), {})
        return Product(
            site=self.site_id,
            country=self.country,
            name=name.strip(),
            price=price,
            currency=offer.get("priceCurrency") or self.currency,
            url=url,
            in_stock=card.get("in_stock"),
            seller=card.get("seller"),
        )

    @staticmethod
    def _cards_by_id(soup: BeautifulSoup) -> dict[str, dict]:
        """Stock flag and seller per product id, read from the visible cards."""
        cards: dict[str, dict] = {}
        for card in soup.select("article[data-tracker-id]"):
            if not card.select_one(".product-card__price"):
                continue  # blog/ranking teasers share the same <article> markup
            seller = card.select_one(".product-seller__name")
            cards[card["data-tracker-id"]] = {
                "in_stock": card.select_one('[data-testid="product-add-to-basket"]') is not None,
                "seller": seller.get_text(strip=True) if seller else None,
            }
        return cards


def model_from_name(name: str) -> str | None:
    """morele often appends the manufacturer code in brackets: '... (MKFT4ZM/A)'."""
    match = NAME_CODE_RE.search(name)
    return match.group(1) if match else None


def _product_id(url: str) -> str | None:
    match = PRODUCT_ID_RE.search(url)
    return match.group(1) if match else None

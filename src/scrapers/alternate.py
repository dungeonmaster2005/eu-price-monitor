"""alternate.de (DE) — classic server-rendered listing.

Listing card (a.productBox): brand + name, variant line (colour), price
"€ 389,00" and a delivery line ("Sofort verfügbar" / "Artikel ... nicht ...").
Product page: JSON-LD Product with EAN (in the gtin8 key), mpn and stock.
Alternate sells everything itself — no marketplace.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from src.currency import PriceParseError, parse_price
from src.models import Product
from src.scrapers.base import BaseScraper, ProductPage
from src.scrapers.html import absolute, text_of
from src.scrapers.jsonld import product_page_from_jsonld

IN_STOCK_MARKERS = ("sofort verfügbar", "auf lager", "versandfertig")
OUT_OF_STOCK_MARKERS = ("nicht verfügbar", "ausverkauft", "unbekannt", "nicht lieferbar", "vorbestell")


class AlternateScraper(BaseScraper):
    def page_url(self, start_url: str, page: int) -> str:
        return start_url if page == 1 else f"{start_url}?page={page}"

    def parse_listing(self, soup: BeautifulSoup) -> list[Product]:
        products = []
        for card in soup.select("a.productBox[href*='/html/product/']"):
            product = self._parse_card(card)
            if product:
                products.append(product)
        return products

    def parse_details(self, soup: BeautifulSoup) -> ProductPage:
        return product_page_from_jsonld(soup)

    def _parse_card(self, card: Tag) -> Product | None:
        name = text_of(card.select_one(".product-name"))
        variant = text_of(card.select_one(".product-name-sub"))
        price_text = text_of(card.select_one(".price"))
        if not name or not price_text:
            return None
        try:
            price, currency = parse_price(price_text)
        except PriceParseError:
            self.log.warning("Unparseable price %r for %s", price_text, name)
            return None
        brand = text_of(card.select_one(".product-name span"))
        # variant line: "schwarz, Bluetooth, USB-C, ANC" — only the colour part identifies it
        colour = variant.split(",")[0].strip() if variant else ""
        return Product(
            site=self.site_id,
            country=self.country,
            name=f"{name} {colour}".strip(),
            price=price,
            currency=currency or self.currency,
            url=absolute("https://www.alternate.de", card["href"]),
            in_stock=delivery_to_stock(text_of(card.select_one(".delivery-info"))),
            brand=brand,
            seller="ALTERNATE",
        )


def delivery_to_stock(text: str | None) -> bool | None:
    """Map Alternate's delivery line to a stock flag (None if unknown)."""
    if not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in OUT_OF_STOCK_MARKERS):
        return False
    if any(marker in lowered for marker in IN_STOCK_MARKERS):
        return True
    return None

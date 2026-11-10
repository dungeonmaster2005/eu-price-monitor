"""coolblue.nl (NL) — listing with a schema.org ItemList incl. availability.

Listing: JSON-LD ItemList gives name, URL, price and stock for every card.
Product page: no GTIN is published; the manufacturer code is in the spec
table under "Fabrikantcode". Coolblue sells everything itself.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from src.id_cache import ProductIds
from src.models import Product
from src.scrapers.base import BaseScraper, ProductPage
from src.scrapers.html import spec_value
from src.scrapers.jsonld import brand_name, clean_mpn, find_typed, is_in_stock, product_page_from_jsonld


class CoolblueScraper(BaseScraper):
    def page_url(self, start_url: str, page: int) -> str:
        return start_url if page == 1 else f"{start_url}?pagina={page}"

    def parse_listing(self, soup: BeautifulSoup) -> list[Product]:
        products = []
        for item_list in find_typed(soup, "ItemList"):
            for item in item_list.get("itemListElement", []):
                item = item.get("item", item)
                offer = item.get("offers") or {}
                try:
                    price = Decimal(str(offer["price"]))
                except (KeyError, InvalidOperation):
                    continue
                if not item.get("url") or not item.get("name"):
                    continue
                products.append(
                    Product(
                        site=self.site_id,
                        country=self.country,
                        name=item["name"].strip(),
                        price=price,
                        currency=offer.get("priceCurrency") or self.currency,
                        url=item["url"],
                        in_stock=is_in_stock(offer.get("availability")),
                        brand=brand_name(item),
                        seller="Coolblue",
                    )
                )
        return products

    def parse_details(self, soup: BeautifulSoup) -> ProductPage:
        page = product_page_from_jsonld(soup)
        brand = page.ids.brand or spec_value(soup, "Merk")
        model = page.ids.model or clean_mpn(spec_value(soup, "Fabrikantcode"), brand)
        return ProductPage(ids=ProductIds(ean=page.ids.ean, model=model, brand=brand), in_stock=page.in_stock)

"""Base class for shop scrapers.

A concrete scraper only describes the site: how page N of a category is
addressed, how to read products from a listing page and how to read details
from a product page. Pagination, deduplication, identifier caching, seller
flags and error isolation live here.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from src.http_client import BlockedError, FetchError, PoliteClient
from src.id_cache import IdCache, ProductIds
from src.identifiers import normalize_gtin
from src.models import Product

log = logging.getLogger(__name__)


@dataclass
class ProductPage:
    """What a product page tells us beyond the listing."""

    ids: ProductIds = field(default_factory=ProductIds)
    in_stock: bool | None = None
    price: Decimal | None = None
    currency: str | None = None


class BaseScraper(ABC):
    def __init__(self, config: dict[str, Any], client: PoliteClient, id_cache: IdCache) -> None:
        self.site_id: str = config["id"]
        self.country: str = config["country"]
        self.currency: str = config["currency"]
        urls = config.get("start_urls") or [config["start_url"]]
        self.start_urls: list[str] = list(urls)
        self.max_pages: int = int(config.get("max_pages", 5))
        self.fetch_details: bool = bool(config.get("fetch_details", True))
        # Listing has no stock info -> product pages are read on every run.
        self.stock_from_details: bool = bool(config.get("stock_from_details", False))
        self.official_sellers: set[str] = {s.lower() for s in config.get("official_sellers", [])}
        # Cap on product pages fetched only to fill the id cache, for sites that
        # rate-limit: the cache then fills up over several days.
        self.max_new_id_pages: int | None = config.get("max_new_id_pages")
        self.client = client
        self.id_cache = id_cache
        # Filled during scrape(): what went wrong, so the run can be judged stale.
        self.listing_complete = True
        self.problems: list[str] = []
        self.log = logging.getLogger(f"scraper.{self.site_id}")

    # -- to implement per site ---------------------------------------------

    @abstractmethod
    def page_url(self, start_url: str, page: int) -> str:
        """URL of listing page `page` (1-based) of the category `start_url`."""

    @abstractmethod
    def parse_listing(self, soup: BeautifulSoup) -> list[Product]:
        """Products found on one listing page (ids may still be missing)."""

    @abstractmethod
    def parse_details(self, soup: BeautifulSoup) -> ProductPage:
        """Identifiers (and stock, if available) from a single product page."""

    # -- shared workflow ---------------------------------------------------

    def scrape(self) -> list[Product]:
        self.listing_complete, self.problems = True, []
        products: dict[str, Product] = {}
        for start_url in self.start_urls:
            for product in self._scrape_listing(start_url):
                products.setdefault(product.url, product)
        result = list(products.values())

        if self.fetch_details:
            self._enrich_from_product_pages(result)
        priced = [p for p in result if p.price is not None]
        if len(priced) < len(result):
            self.log.warning("%d products dropped: no price on listing or product page", len(result) - len(priced))
        result = priced
        for product in result:
            product.is_official = self._is_official(product.seller)
            product.ean = normalize_gtin(product.ean, context=f"({self.site_id} {product.url})")

        self.log.info(
            "%d products: %d with EAN, %d with model, %d in stock, %d third-party",
            len(result),
            sum(p.ean is not None for p in result),
            sum(p.model is not None for p in result),
            sum(p.in_stock is True for p in result),
            sum(not p.is_official for p in result),
        )
        return result

    def _scrape_listing(self, start_url: str) -> list[Product]:
        seen: dict[str, Product] = {}
        for page in range(1, self.max_pages + 1):
            url = self.page_url(start_url, page)
            try:
                soup = self._soup(url)
            except BlockedError as exc:
                self.log.error("Blocked (%s) — stopping this site, not working around it", exc)
                self._listing_problem(f"listing page {page} blocked: {exc}")
                break
            except FetchError as exc:
                self.log.error("Listing page %d failed: %s", page, exc)
                self._listing_problem(f"listing page {page} failed: {exc}")
                break

            page_products = self.parse_listing(soup)
            new = [p for p in page_products if p.url not in seen]
            self.log.info("page %d: %d products (%d new)", page, len(page_products), len(new))
            if not new:
                break  # past the last page, or the site loops back to page 1
            for product in new:
                seen[product.url] = product
        return list(seen.values())

    def _enrich_from_product_pages(self, products: list[Product]) -> None:
        # Product pages are read once for identifiers (then cached), and daily
        # for offers whose listing hides the price or the stock.
        needed_daily = [
            p for p in products
            if p.price is None or (self.stock_from_details and p.in_stock is None)
        ]
        daily_urls = {p.url for p in needed_daily}
        ids_only = [p for p in products if self.id_cache.get(p.url) is None and p.url not in daily_urls]
        if self.max_new_id_pages is not None and len(ids_only) > self.max_new_id_pages:
            self.log.info("id cache: %d new products, fetching %d today (rate-limit budget)",
                          len(ids_only), self.max_new_id_pages)
            ids_only = ids_only[: self.max_new_id_pages]
        todo = needed_daily + ids_only
        self.log.info("product pages: %d to fetch, %d served from id cache", len(todo), len(products) - len(todo))

        for i, product in enumerate(todo, 1):
            try:
                page = self.parse_details(self._soup(product.url))
            except BlockedError as exc:
                self.log.error("Blocked while fetching product pages (%s) — stopping, not working around it", exc)
                self.problems.append(f"product pages blocked after {i - 1}/{len(todo)}: {exc}")
                break
            except FetchError as exc:
                self.log.warning("Product page failed (%s): %s", product.url, exc)
                continue
            except Exception:  # a parser bug on one page must not kill the run
                self.log.exception("Could not parse product page %s", product.url)
                continue
            self.id_cache.put(product.url, page.ids)
            if product.in_stock is None:
                product.in_stock = page.in_stock
            if product.price is None and page.price is not None:
                product.price, product.currency = page.price, page.currency or product.currency
            if i % 20 == 0:
                self.log.info("product pages: %d/%d", i, len(todo))
                self.id_cache.save()
        self.id_cache.save()

        for product in products:
            ids = self.id_cache.get(product.url)
            if ids:
                product.ean = product.ean or ids.ean
                product.model = product.model or ids.model
                product.brand = product.brand or ids.brand

    def _listing_problem(self, message: str) -> None:
        self.listing_complete = False
        self.problems.append(message)

    def _is_official(self, seller: str | None) -> bool:
        """No seller shown means the shop sells it itself."""
        if seller is None or not self.official_sellers:
            return True
        return seller.lower() in self.official_sellers

    def _soup(self, url: str) -> BeautifulSoup:
        return BeautifulSoup(self.client.get_html(url), "lxml")

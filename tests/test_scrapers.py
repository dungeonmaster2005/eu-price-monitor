"""Scraper parsing tests on HTML fixtures cut from real pages (Nov 2026).

Out-of-stock cases: LDLC's fixture contains a real out-of-stock offer
(AKG N9 Hybrid). The other shops had nothing out of stock in the scraped
categories, so their out-of-stock fixtures are the real markup with exactly
the stock signal flipped (availability URL, missing basket button, delivery
line), which is the part the parser reads.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from src.scrapers.alternate import AlternateScraper, delivery_to_stock
from src.scrapers.coolblue import CoolblueScraper
from src.scrapers.jsonld import clean_mpn, first_gtin, is_in_stock
from src.scrapers.ldlc import LdlcScraper
from src.scrapers.mironet import MironetScraper
from src.scrapers.morele import MoreleScraper, model_from_name

FIXTURES = Path(__file__).parent / "fixtures"


def soup(name: str, replace: tuple[str, str] | None = None) -> BeautifulSoup:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    if replace:
        assert replace[0] in html, "fixture no longer contains the stock marker"
        html = html.replace(*replace)
    return BeautifulSoup(html, "lxml")


def make(cls, site_id, country, currency, **extra):
    config = {"id": site_id, "country": country, "currency": currency, "start_url": "https://x/", **extra}
    return cls(config, client=None, id_cache=None)


# --- morele -------------------------------------------------------------------

def test_morele_listing():
    [p] = make(MoreleScraper, "morele", "PL", "PLN").parse_listing(soup("morele_listing.html"))
    assert p.currency == "PLN" and p.price > 0
    assert p.in_stock is True
    assert p.seller


def test_morele_out_of_stock_when_no_basket_button():
    page = soup("morele_listing.html", ('data-testid="product-add-to-basket"', 'data-testid="notify-me"'))
    [p] = make(MoreleScraper, "morele", "PL", "PLN").parse_listing(page)
    assert p.in_stock is False


def test_morele_page_url():
    s = make(MoreleScraper, "morele", "PL", "PLN")
    base = "https://www.morele.net/kategoria/sluchawki-bezprzewodowe-458/"
    assert s.page_url(base, 1) == base
    assert s.page_url(base, 3) == base + ",,,,,,,,0,,,,/3/"


def test_morele_model_from_name():
    assert model_from_name("Słuchawki Apple AirPods 5 (MKFT4ZM/A)") == "MKFT4ZM/A"
    assert model_from_name("Słuchawki Sony WH-1000XM6 czarne") is None
    assert model_from_name("Słuchawki JBL (czarne)") is None


# --- alternate ----------------------------------------------------------------

def test_alternate_listing():
    products = make(AlternateScraper, "alternate_de", "DE", "EUR").parse_listing(soup("alternate_listing.html"))
    assert len(products) == 2
    p = products[0]
    assert p.currency == "EUR" and isinstance(p.price, Decimal)
    assert p.in_stock is True
    assert p.url.startswith("https://www.alternate.de/")
    assert "Bluetooth" not in p.name  # feature list from the variant line is dropped


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Sofort verfügbar", True), ("Auf Lager", True), ("Artikel nicht verfügbar", False),
     ("Liefertermin unbekannt", False), (None, None), ("Lieferung in 3–5 Tagen", None)],
)
def test_alternate_delivery_line(text, expected):
    assert delivery_to_stock(text) is expected


def test_alternate_out_of_stock_card():
    page = soup("alternate_listing.html", ("Sofort verfügbar", "Liefertermin unbekannt"))
    assert make(AlternateScraper, "alternate_de", "DE", "EUR").parse_listing(page)[0].in_stock is False


# --- coolblue -----------------------------------------------------------------

def test_coolblue_listing_and_out_of_stock():
    s = make(CoolblueScraper, "coolblue", "NL", "EUR")
    assert all(p.in_stock for p in s.parse_listing(soup("coolblue_listing.html")))
    page = soup("coolblue_listing.html", ("schema.org/InStock", "schema.org/OutOfStock"))
    assert s.parse_listing(page)[0].in_stock is False


def test_coolblue_manufacturer_code_from_spec_table():
    page = make(CoolblueScraper, "coolblue", "NL", "EUR").parse_details(soup("coolblue_product.html"))
    assert page.ids.model == "WF1000XM5B.CE7"


# --- mironet ------------------------------------------------------------------

def test_mironet_listing_price_in_czk():
    [p] = make(MironetScraper, "mironet", "CZ", "CZK").parse_listing(soup("mironet_listing.html"))
    assert p.currency == "CZK" and p.price > 100
    assert p.in_stock is None  # stock comes from the product page


def test_mironet_product_page_stock_and_mpn():
    s = make(MironetScraper, "mironet", "CZ", "CZK")
    page = s.parse_details(soup("mironet_product.html"))
    assert page.in_stock is True and page.ids.model == "MXP63" and page.ids.ean is None
    oos = s.parse_details(soup("mironet_product.html", ("schema.org/InStock", "schema.org/OutOfStock")))
    assert oos.in_stock is False


# --- ldlc ---------------------------------------------------------------------

def test_ldlc_real_out_of_stock_and_marketplace():
    products = make(LdlcScraper, "ldlc", "FR", "EUR").parse_listing(soup("ldlc_listing.html"))
    by_name = {p.name: p for p in products}
    akg = next(p for name, p in by_name.items() if name.startswith("AKG N9"))
    assert akg.in_stock is False  # real out-of-stock offer
    assert any(p.in_stock for p in products)
    assert any(p.seller == "LDLC Marketplace partner" for p in products)


def test_ldlc_page_url_with_filter():
    s = make(LdlcScraper, "ldlc", "FR", "EUR")
    url = "https://www.ldlc.com/image-son/son-numerique/casque/c4380/+fc194-1.html"
    assert s.page_url(url, 2) == "https://www.ldlc.com/image-son/son-numerique/casque/c4380/page2/+fc194-1.html"


# --- shared JSON-LD helpers ---------------------------------------------------

def test_jsonld_helpers():
    assert first_gtin({"gtin8": "4711387604847"}) == "4711387604847"  # Alternate's mislabelled key
    assert first_gtin({"gtin13": "n/a"}) is None
    assert is_in_stock("InStock") is True  # Alternate omits the schema.org prefix
    assert is_in_stock("https://schema.org/BackOrder") is False
    assert clean_mpn("Sony", "Sony") is None
    assert clean_mpn("8806095646770", "Samsung") is None

from datetime import date
from decimal import Decimal

from src import tables
from src.models import Product
from src.site_status import SiteStatus

TODAY = date(2026, 11, 10)


def offer(site, country, eur, match_id="ean:1", official=True, in_stock=True, stale=False, name=None):
    return Product(site=site, country=country, name=name or f"Sony WH-1000XM6 {site}", price=Decimal(eur),
                   currency="EUR", url=f"https://{site}/{match_id}/{eur}", price_eur=Decimal(eur),
                   is_official=official, in_stock=in_stock, match_id=match_id, base_model="sony wh1000xm6",
                   stale=stale, date=TODAY)


def col(header):
    return tables.column_index(tables.COMPARE_COLUMNS, header)


def test_compare_row_prices_min_and_spread():
    products = [offer("morele", "PL", "300"), offer("coolblue", "NL", "360"), offer("ldlc", "FR", "330")]
    [row] = tables.compare_rows(products)
    assert row[col("PL")] == tables.Link(300.0, "https://morele/ean:1/300")  # price links to the shop page
    assert row[col("NL")].value == 360.0 and row[col("DE")] is None
    assert row[col("Min EUR")].value == 300.0
    assert row[col("Cheapest at")] == "morele (PL)"
    assert abs(row[col("Spread")] - 0.2) < 1e-9
    assert row[col("Product")] == "Sony WH-1000XM6 coolblue"  # coolblue names preferred
    assert "Base model" not in [c.header for c in tables.COMPARE_COLUMNS]


def test_compare_ignores_marketplace_and_out_of_stock_and_single_country():
    products = [
        offer("morele", "PL", "100", official=False),  # third-party seller
        offer("ldlc", "FR", "120", in_stock=False),  # cannot be bought
        offer("coolblue", "NL", "300"),
        offer("alternate_de", "DE", "310"),
        offer("mironet", "CZ", "50", match_id="ean:2"),  # one country only
    ]
    rows = tables.compare_rows(products)
    assert len(rows) == 1
    assert rows[0][col("PL")] is None and rows[0][col("FR")] is None


def test_compare_sorted_by_spread_and_notes_stale():
    a = [offer("morele", "PL", "100", "ean:a"), offer("coolblue", "NL", "110", "ean:a")]
    b = [offer("morele", "PL", "100", "ean:b"), offer("coolblue", "NL", "150", "ean:b", stale=True)]
    rows = tables.compare_rows(a + b)
    assert [r[col("Spread")] for r in rows] == sorted([r[col("Spread")] for r in rows], reverse=True)
    assert "NL" in rows[0][col("Notes")]


def test_summary_changes_vs_yesterday_and_week():
    history = [
        {"Date": "2026-11-03", "Match ID": "ean:1", "Price EUR": 400, "Official": "TRUE", "In stock": "TRUE"},
        {"Date": "2026-11-09", "Match ID": "ean:1", "Price EUR": 320, "Official": "TRUE", "In stock": "TRUE"},
        {"Date": "2026-11-09", "Match ID": "ean:1", "Price EUR": 250, "Official": "FALSE", "In stock": "TRUE"},
    ]
    products = [offer("morele", "PL", "300"), offer("coolblue", "NL", "360")]
    [row] = tables.summary_rows(products, history, TODAY)
    h = {c.header: i for i, c in enumerate(tables.SUMMARY_COLUMNS)}
    assert row[h["Min EUR"]] == 300.0 and row[h["Country"]] == "PL"
    assert abs(row[h["vs yesterday"]] - (300 - 320) / 320) < 1e-9  # marketplace 250 ignored
    assert abs(row[h["vs 7 days"]] - (300 - 400) / 400) < 1e-9
    assert row[h["Most expensive country"]] == "NL"


def test_status_rows():
    rows = tables.status_rows([
        SiteStatus("ldlc", "FR", last_success="2026-11-10", count_today=240, usual_count=240),
        SiteStatus("morele", "PL", last_success="2026-11-09", count_today=12, stale=True, reason="only 12 vs usual 150"),
    ])
    assert rows[0][0] == "morele" and rows[0][2] == "STALE"
    assert rows[1][2] == "OK"


def test_english_name_translates_colour_and_shop_phrases():
    def named(name, site, brand):
        return [Product(site=site, country="NL", name=name, price=Decimal(1), currency="EUR", url="u", brand=brand)]

    assert tables.english_name(named("Sony WH-1000XM6 Zwart", "coolblue", "Sony")) == "Sony WH-1000XM6 Black"
    assert tables.english_name(named("Samsung Galaxy Buds 4, Kopfhörer weiß", "alternate_de", "Samsung"))         == "Samsung Galaxy Buds 4 White"
    assert tables.english_name(named("Bose QuietComfort Ultra Headphones (2e gen) Wit", "coolblue", "Bose"))         == "Bose QuietComfort Ultra Headphones (Gen 2) White"
    assert tables.english_name(named("Kraken V4 černá", "mironet", "Razer")) == "Razer Kraken V4 Black"
    assert tables.english_name(named("Apple AirPods 5 met draadloze oplaadcase", "coolblue", "Apple"))         == "Apple AirPods 5 with Wireless Charging Case"

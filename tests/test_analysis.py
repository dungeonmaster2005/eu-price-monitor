from datetime import date
from decimal import Decimal

from src.analysis import AlertConfig, detect
from src.models import Product
from src.notify import build_message

CFG = AlertConfig(price_drop_pct=5, new_leader_margin_pct=1, top_n_per_group=2)


def offer(site, country, price, currency="EUR", rate="1", match_id="ean:1", url=None, in_stock=True,
          official=True, stale=False, name="Sony WH-1000XM6 Zwart"):
    price = Decimal(price)
    return Product(site=site, country=country, name=name, price=price, currency=currency,
                   url=url or f"https://{site}/{match_id}", in_stock=in_stock, is_official=official,
                   stale=stale, match_id=match_id, price_eur=(price / Decimal(rate)).quantize(Decimal("0.01")),
                   date=date(2026, 11, 10))


def prev(p, price=None, in_stock=True, currency=None):
    return {"site": p.site, "url": p.url, "price": str(price or p.price),
            "currency": currency or p.currency, "in_stock": in_stock}


def kinds(alerts):
    return sorted(a.kind for a in alerts)


def test_first_run_has_no_alerts():
    assert detect([offer("ldlc", "FR", "100")], {}, set(), CFG) == []


def test_price_drop_in_shop_currency_threshold():
    p = offer("morele", "PL", "1499", "PLN", rate="4.38")
    alerts = detect([p], {p.url: prev(p, "1699")}, set(), CFG)
    assert kinds(alerts) == ["price_drop"]
    assert round(alerts[0].magnitude, 1) == 11.8
    small = offer("ldlc", "FR", "97")
    assert detect([small], {small.url: prev(small, "100")}, set(), CFG) == []  # 3% < 5%


def test_fx_move_alone_is_not_a_price_drop():
    # same PLN price yesterday and today; only the EUR value moved with the rate
    p = offer("morele", "PL", "1699", "PLN", rate="4.60")
    assert detect([p], {p.url: prev(p, "1699")}, set(), CFG) == []


def test_out_of_stock_and_new_product():
    gone = offer("ldlc", "FR", "249", in_stock=False, match_id="ean:2")
    new = offer("ldlc", "FR", "99", match_id="ean:3")
    alerts = detect([gone, new], {gone.url: prev(gone, in_stock=True)}, set(), CFG, {"ldlc": 7})
    assert kinds(alerts) == ["new_product", "out_of_stock"]


def test_new_products_wait_for_a_week_of_history():
    known = offer("ldlc", "FR", "249", match_id="ean:2")
    new = offer("ldlc", "FR", "99", match_id="ean:3")
    previous = {known.url: prev(known)}
    assert detect([known, new], previous, set(), CFG, {"ldlc": 6}) == []
    assert kinds(detect([known, new], previous, set(), CFG, {"ldlc": 7})) == ["new_product"]


def test_open_box_and_refurbished_never_alert():
    p = offer("mironet", "CZ", "3000", "CZK", rate="24.3", name="Rozbaleno - JBL Tour One M3")
    p.condition = "open_box"
    assert detect([p], {p.url: prev(p, "5000", in_stock=True)}, set(), CFG, {"mironet": 7}) == []


def test_new_leader_needs_margin():
    pl = offer("morele", "PL", "430", "PLN", rate="4.3")  # 100.00 EUR
    nl = offer("coolblue", "NL", "103")
    yesterday = {pl.url: prev(pl, "460"), nl.url: prev(nl, "103")}  # PL was 107 EUR -> NL led
    alerts = detect([pl, nl], yesterday, set(), CFG)
    assert "new_leader" in kinds(alerts)
    leader = next(a for a in alerts if a.kind == "new_leader")
    assert leader.country == "PL" and leader.details["old_country"] == "NL"

    nl_close = offer("coolblue", "NL", "100.50")  # PL leads by only 0.5%
    alerts = detect([pl, nl_close], {pl.url: prev(pl, "460"), nl_close.url: prev(nl_close, "100.50")}, set(), CFG)
    assert "new_leader" not in kinds(alerts)


def test_stale_sites_and_marketplace_never_alert():
    stale = offer("morele", "PL", "1000", "PLN", rate="4.3")
    carried = offer("ldlc", "FR", "50", stale=True, match_id="ean:9")
    market = offer("morele", "PL", "10", "PLN", rate="4.3", official=False, match_id="ean:8", url="https://m/8")
    previous = {stale.url: prev(stale, "2000"), carried.url: prev(carried, "100"), market.url: prev(market, "100")}
    assert detect([stale, carried, market], previous, {"morele"}, CFG) == []


def test_message_groups_top_n_and_more_line():
    drops = [offer("ldlc", "FR", str(100 - i * 5), match_id=f"ean:{i}") for i in range(1, 5)]
    previous = {p.url: prev(p, "100") for p in drops}
    alerts = detect(drops, previous, set(), CFG)
    text = build_message(alerts, CFG, date(2026, 11, 10), stale_sites={"alternate_de"})
    assert "Price drops (4)" in text
    assert text.count("• ") == 2 and "…and 2 more" in text  # top_n = 2
    assert text.index("−20.0%") < text.index("−15.0%")  # biggest change first
    assert "<a href=" in text and "alternate_de" in text


def test_no_alerts_no_message():
    assert build_message([], CFG, date(2026, 11, 10)) is None

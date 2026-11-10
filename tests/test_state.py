from datetime import date
from decimal import Decimal

from src.models import Product
from src.offer_state import OfferState
from src.site_status import SiteRun, SiteStatusStore

D1, D2 = date(2026, 11, 9), date(2026, 11, 10)


def offer(url, site="morele"):
    return Product(site=site, country="PL", name=url, price=Decimal("10"), currency="PLN", url=url)


def test_site_ok_then_stale_on_drop(tmp_path):
    store = SiteStatusStore(tmp_path / "s.json")
    for day in range(1, 4):
        assert not store.record(SiteRun("morele", "PL", 150, True), date(2026, 11, day)).stale
    status = store.record(SiteRun("morele", "PL", 40, True), D2)
    assert status.stale and "usual 150" in status.reason
    assert status.last_success == "2026-11-03"


def test_site_stale_when_blocked(tmp_path):
    store = SiteStatusStore(tmp_path / "s.json")
    status = store.record(SiteRun("alternate_de", "DE", 24, False, ["listing page 2 blocked: HTTP 429"]), D2)
    assert status.stale and "429" in status.reason
    assert status.last_success is None


def test_detail_block_alone_is_not_stale(tmp_path):
    store = SiteStatusStore(tmp_path / "s.json")
    status = store.record(SiteRun("alternate_de", "DE", 120, True, ["product pages blocked after 25/90"]), D2)
    assert not status.stale and "product pages blocked" in status.reason


def test_stale_site_offers_are_carried_over_with_last_seen(tmp_path):
    state = OfferState(tmp_path / "o.json")
    state.merge_today([offer("a"), offer("b"), offer("x", site="ldlc")], stale_sites=set(), today=D1)

    # next day morele is blocked after page 1: only "a" is fresh
    today = state.merge_today([offer("a"), offer("y", site="ldlc")], stale_sites={"morele"}, today=D2)
    by_url = {p.url: p for p in today}
    assert by_url["a"].last_seen == D2 and not by_url["a"].stale
    assert by_url["b"].stale and by_url["b"].last_seen == D1  # not "disappeared"
    assert "x" not in by_url  # ldlc was fine: x really left the listing


def test_state_roundtrip(tmp_path):
    path = tmp_path / "o.json"
    state = OfferState(path)
    state.merge_today([offer("a")], stale_sites=set(), today=D1)
    state.save()
    assert OfferState(path).offers["a"]["last_seen"] == "2026-11-09"

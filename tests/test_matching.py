from decimal import Decimal

from src.matching import match_products, name_similarity
from src.models import Product


def offer(site, country, name, ean=None, model=None, brand=None, seller=None, official=True):
    return Product(site=site, country=country, name=name, price=Decimal("100"), currency="EUR",
                   url=f"https://{site}/{name.replace(' ', '-')}", ean=ean, model=model, brand=brand,
                   seller=seller, is_official=official)


def test_ean_match_wins():
    a = offer("morele", "PL", "Słuchawki Sony WH-1000XM6 czarne", ean="4548736162624")
    b = offer("ldlc", "FR", "Sony WH-1000XM6 Noir", ean="4548736162624")
    result = match_products([a, b])
    assert a.match_id == b.match_id == "ean:4548736162624"
    assert a.match_method == "ean"
    assert len(result.groups) == 1


def test_mpn_match_across_regional_suffixes():
    a = offer("morele", "PL", "Słuchawki Apple AirPods 4 (MXP63ZM/A)", model="MXP63ZM/A", brand="Apple")
    b = offer("mironet", "CZ", "Apple AirPods 4 bílá", model="MXP63", brand="Apple")
    match_products([a, b])
    assert a.match_id == b.match_id
    assert a.match_method == "mpn"


def test_same_mpn_different_brand_not_matched():
    a = offer("morele", "PL", "Foo X100", model="X1000", brand="Foo")
    b = offer("ldlc", "FR", "Bar X100", model="X1000", brand="Bar")
    match_products([a, b])
    assert a.match_id != b.match_id


def test_colour_variants_are_different_products_with_same_base_model():
    black = offer("coolblue", "NL", "Sony WH-1000XM6 Zwart", brand="Sony")
    silver = offer("morele", "PL", "Słuchawki Sony WH-1000XM6 srebrne", brand="Sony")
    match_products([black, silver])
    assert black.match_id != silver.match_id
    assert black.base_model == silver.base_model == "sony wh1000xm6"


def test_high_confidence_name_match_is_automatic():
    a = offer("coolblue", "NL", "Sony WH-1000XM6 Zwart", brand="Sony")
    b = offer("morele", "PL", "Słuchawki Sony WH-1000XM6 czarne", brand="Sony")
    result = match_products([a, b])
    assert a.match_id == b.match_id
    assert a.match_method == "name"
    assert not result.review


def test_medium_confidence_goes_to_review_not_merged():
    a = offer("coolblue", "NL", "Sony WH-1000XM6 Zwart", brand="Sony")
    b = offer("morele", "PL", "Słuchawki Sony WH-1000XM6 czarne Limited Bundle", brand="Sony")
    result = match_products([a, b])
    assert a.match_id != b.match_id
    assert len(result.review) == 1


def test_different_model_numbers_never_match_by_name():
    score, reason = name_similarity(
        offer("a", "PL", "JBL Tune 520BT czarne", brand="JBL"),
        offer("b", "NL", "JBL Tune 720BT Zwart", brand="JBL"),
    )
    assert score <= 0.5
    assert "model numbers differ" in reason


def test_name_match_blocked_when_both_sides_have_different_eans():
    a = offer("coolblue", "NL", "Sony WH-1000XM6 Zwart", ean="4548736162624", brand="Sony")
    b = offer("morele", "PL", "Sony WH-1000XM6 czarne", ean="4006381333931", brand="Sony")
    match_products([a, b])
    assert a.match_id != b.match_id


def test_manual_merge_and_split_overrides():
    from src.matching import MatchOverrides

    headphones = offer("coolblue", "NL", "Bose QuietComfort Ultra Headphones (2e gen) Zwart", brand="Bose")
    unnamed = offer("mironet", "CZ", "Bose QuietComfort Ultra 2.Gen černá", brand="Bose")
    earbuds = offer("ldlc", "FR", "Bose QuietComfort Ultra Earbuds (2e gen) Noir", brand="Bose")
    overrides = MatchOverrides(
        merge=[[headphones.url, unnamed.url]],
        split=[[earbuds.url, unnamed.url]],
    )
    result = match_products([headphones, unnamed, earbuds], overrides)
    assert headphones.match_id == unnamed.match_id
    assert headphones.match_method == "manual"
    assert earbuds.match_id != unnamed.match_id
    assert all(unnamed not in (c.left, c.right) or earbuds not in (c.left, c.right) for c in result.review)


def test_split_blocks_even_ean_match():
    from src.matching import MatchOverrides

    a = offer("morele", "PL", "Foo X1", ean="4006381333931")
    b = offer("ldlc", "FR", "Foo X1 Bundle", ean="4006381333931")
    match_products([a, b], MatchOverrides(split=[[a.url, b.url]]))
    assert a.match_id != b.match_id


def test_brand_aliases_let_mpn_match():
    a = offer("coolblue", "NL", "Soundcore Liberty 5 Pro Zwart", model="A3954Z11", brand="Soundcore by Anker")
    b = offer("mironet", "CZ", "Anker Soundcore Liberty 5 Pro černá", model="A3954Z11", brand="nezatříděno")
    match_products([a, b])
    assert a.match_id == b.match_id and a.match_method == "mpn"


def test_same_mpn_different_colour_goes_to_review():
    pink = offer("coolblue", "NL", "Nothing Headphone (a) Roze", model="A11300030", brand="Nothing Tech")
    black = offer("mironet", "CZ", "Nothing Headphone (a) černá", model="A11300030", brand="Nothing")
    result = match_products([pink, black])
    assert pink.match_id != black.match_id
    assert any("colour differs" in c.reason for c in result.review)


def test_refurbished_never_matches_the_new_product():
    new = offer("ldlc", "FR", "Apple AirPods Max Bleu", ean="0194253397076", brand="Apple")
    refurb = offer("alternate_de", "DE", "Apple AirPods Max Generalüberholt, Kopfhörer blau",
                   ean="0194253397076", brand="Apple")
    refurb.condition = "refurbished"
    match_products([new, refurb])
    assert new.match_id == "ean:0194253397076"
    assert refurb.match_id == "ean:0194253397076|refurbished"

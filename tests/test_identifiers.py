import pytest

from src.identifiers import base_model, colours_in, gtin_check_digit_ok, normalize_gtin, normalize_mpn


class TestGtin:
    def test_valid_ean13_kept(self):
        assert normalize_gtin("4548736162624") == "4548736162624"

    def test_upc12_padded_to_gtin13(self):
        # AirPods Pro 3 UPC as published by morele
        assert normalize_gtin("195950543742") == "0195950543742"

    def test_gtin14_with_leading_zero_trimmed(self):
        assert normalize_gtin("04548736162624") == "4548736162624"

    def test_ean8_padded(self):
        assert normalize_gtin("96385074") == "0000096385074"

    def test_bad_check_digit_rejected_and_logged(self, caplog):
        assert normalize_gtin("4548736162625") is None
        assert "Invalid EAN" in caplog.text

    @pytest.mark.parametrize("raw", [None, "", "n/a", "12345", "0000000000000"])
    def test_garbage_rejected(self, raw):
        assert normalize_gtin(raw) is None

    def test_check_digit(self):
        assert gtin_check_digit_ok("4006381333931")
        assert not gtin_check_digit_ok("4006381333932")


class TestMpn:
    @pytest.mark.parametrize(
        ("raw", "brand", "expected"),
        [
            # Apple regional suffixes: same device sold in different regions
            ("MXP63ZM/A", "Apple", "MXP63"),
            ("MXP63", "Apple", "MXP63"),
            ("MFHP4ZM-A", "Apple", "MFHP4"),  # shop wrote the slash as a dash
            ("MKFT4ZM/A", "Apple", "MKFT4"),
            # Sony: ".CE7" is the EU region code, the trailing B/S is the colour
            ("WF1000XM5B.CE7", "Sony", "WF1000XM5B"),
            ("WF-1000XM5B", "Sony", "WF1000XM5B"),
            ("WH1000XM6L.CE7/EU", "Sony", "WH1000XM6L"),
            # Samsung: EU region suffix dropped, colour code (ZK/ZA/ZW) kept
            ("SM-R640NZKAEUB", "Samsung", "SMR640NZKA"),
            ("SM-R640NZKAEUE", "Samsung", "SMR640NZKA"),
            # Brand prefix dropped
            ("JBLT780NCBLK", "JBL", "T780NCBLK"),
            ("T780NCBLK", "JBL", "T780NCBLK"),
        ],
    )
    def test_same_product_same_key(self, raw, brand, expected):
        assert normalize_mpn(raw, brand) == expected

    @pytest.mark.parametrize(
        ("a", "b", "brand"),
        [
            ("WF1000XM5B.CE7", "WF1000XM5S.CE7", "Sony"),  # black vs silver
            ("WH1000XM5B", "WH1000XM6B", "Sony"),  # different generation
            ("SM-R640NZKAEUB", "SM-R640NZWAEUB", "Samsung"),  # black vs white
            ("MKFT4ZM/A", "MKFW4ZM/A", "Apple"),  # AirPods 5 vs AirPods 5 ANC
            ("T760NCBLK", "T760BTBLK", "JBL"),  # NC vs non-NC
        ],
    )
    def test_different_products_do_not_collapse(self, a, b, brand):
        assert normalize_mpn(a, brand) != normalize_mpn(b, brand)

    def test_rejects_ean_like_and_too_short(self):
        assert normalize_mpn("8806095646770", "Samsung") is None  # EAN in the MPN field
        assert normalize_mpn("X1", None) is None

    def test_keeps_numeric_manufacturer_codes(self):
        # Bose part numbers are digits only: headphones gen 2 white vs black differ
        assert normalize_mpn("890101-0200", "Bose") == "8901010200"
        assert normalize_mpn("890101-0200", "Bose") != normalize_mpn("890101-0100", "Bose")


class TestNames:
    def test_colours_are_canonical_across_languages(self):
        assert colours_in("Słuchawki Sony WH-1000XM6 czarne") == {"black"}
        assert colours_in("Sony WH-1000XM6 Zwart") == {"black"}
        assert colours_in("SONY WHULT900NB.CE7 černá") == {"black"}
        assert colours_in("Apple AirPods Max Lumière stellaire") == colours_in("Apple AirPods Max Starlight")
        assert colours_in("Apple AirPods Max Mauve") == {"purple"}
        assert colours_in("Apple AirPods Max 2 Minuit") == {"black"}
        assert base_model("Apple AirPods Max 2 Lumière stellaire", "Apple") == "apple airpods max 2"

    def test_base_model_ignores_colour_and_category_words(self):
        pl = base_model("Słuchawki Sony WH-1000XM6 czarne", "Sony")
        nl = base_model("Sony WH-1000XM6 Zilver", "Sony")
        assert pl == nl == "sony wh1000xm6"

    def test_base_model_keeps_distinguishing_words(self):
        assert base_model("JBL Tune 760NC czarne") != base_model("JBL Tune 760 czarne")


@pytest.mark.parametrize(
    ("text", "condition"),
    [
        ("Rozbaleno - JBL Tour One M3 latte", "open_box"),  # mironet
        ("Słuchawki Sony WH-1000XM5 powystawowe", "open_box"),  # morele
        ("JBL Tune 520BT B-Ware", "open_box"),  # alternate
        ("https://www.coolblue.nl/tweedekans/product/905648", "open_box"),  # coolblue
        ("Apple AirPods Max Generalüberholt, Kopfhörer blau", "refurbished"),
        ("Casque Sony reconditionné", "refurbished"),  # ldlc
        ("Słuchawki JBL używane", "used"),
        ("Sony WH-1000XM6 Zwart", "new"),
    ],
)
def test_detect_condition(text, condition):
    from src.identifiers import detect_condition

    assert detect_condition(text) == condition

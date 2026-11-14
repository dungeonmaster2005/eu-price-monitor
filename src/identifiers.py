"""Normalisation of product identifiers and names for cross-shop matching."""

from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# --- EAN / GTIN --------------------------------------------------------------


def gtin_check_digit_ok(code: str) -> bool:
    """Validate the GS1 mod-10 check digit of a GTIN of any length."""
    if not code.isdigit() or len(code) < 8:
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_gtin(raw: str | None, context: str = "") -> str | None:
    """Return a valid GTIN-13, or None (invalid codes are logged).

    UPC-A (12) and EAN-8 are left-padded with zeros, GTIN-14 with a leading
    zero is trimmed — the standard way to compare GTINs of different lengths.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) in (8, 12):
        digits = digits.zfill(13)
    if len(digits) != 13 or not gtin_check_digit_ok(digits) or set(digits) == {"0"}:
        log.warning("Invalid EAN %r ignored for matching %s", raw, context)
        return None
    return digits


# --- Manufacturer part numbers ----------------------------------------------

# Regional / packaging suffixes that do not change the physical product.
_MPN_SUFFIXES = [
    # Apple: 5-char part + region + "/A": MXP63ZM/A, MXP63Z/A, MFHP4ZM-A (dash instead of slash)
    re.compile(r"(?<=^[A-Z0-9]{5})[A-Z]{1,2}[/-]A$"),
    re.compile(r"\.(?:CE7|E8|CE|EU|EU1|AE|UC|G)(?:/EU)?$"),  # Sony: WF1000XM5B.CE7, WH1000XM6L.CE7/EU
    re.compile(r"(?<=[A-Z0-9])(?:EUB|EUE|EUA|EUC|EU|XEF|XEO)$"),  # Samsung: SM-R640NZKAEUB
]


def normalize_mpn(raw: str | None, brand: str | None = None) -> str | None:
    """Canonical form of a manufacturer part number.

    Uppercase, regional suffix removed, separators dropped, brand prefix
    removed ("JBLT780NCBLK" -> "T780NCBLK"). Colour codes are kept on purpose:
    WF1000XM5B (black) and WF1000XM5S (silver) stay different products.
    """
    if not raw:
        return None
    value = str(raw).strip().upper()
    for pattern in _MPN_SUFFIXES:
        value = pattern.sub("", value)
    value = re.sub(r"[\s\-_/.]", "", value)
    if brand:
        brand_key = re.sub(r"\W", "", brand.upper())
        if value.startswith(brand_key) and len(value) - len(brand_key) >= 4:
            value = value[len(brand_key):]
    if len(value) < 4 or (value.isdigit() and (len(value) < 6 or len(value) in (8, 12, 13, 14))):
        return None  # too short, or an EAN/UPC put in the MPN field
    return value  # numeric codes of other lengths are real MPNs (Bose "890101-0200")


# --- Names ---------------------------------------------------------------------

# Colour words in the five shop languages, mapped to one canonical English name,
# so that "czarne", "Zwart" and "noir" are recognised as the same colour.
_COLOUR_WORDS = {
    "black": "black czarne czarny czarna schwarz zwart cerna cerne cerny noir noire midnight minuit middernacht onyx",
    "white": "white biale bialy biala weiss weis wit bila bile bily blanc blanche starlight",
    "silver": "silver srebrne srebrny silber zilver stribrna stribrne argent titanium",
    "blue": "blue niebieskie niebieski granatowe blau dunkelblau blauw donkerblauw modra modre bleu navy",
    "pink": "pink rozowe rozowy rosa roze ruzova ruzove rose",
    "green": "green zielone zielony grun gruen dunkelgrun groen zelena vert",
    "brown": "brown brazowe brazowy braun bruin hneda hnede marron",
    "grey": "grey gray szare szary grafitowe grafitowy grau grijs seda sede gris graphite anthracite charcoal",
    "beige": "beige bezowe bezowy bezova cream creme sand sable latte",
    "red": "red czerwone rot rood cervena rouge",
    "gold": "gold goud zlote zlota zlata dore champagne",
    "purple": "purple fioletowe lila paars fialova violet mauve",
    "orange": "orange oranje pomaranczowe oranzova",
    "copper": "copper koper kupfer miedziane",
    "coral": "coral koralle koraal korallowe koralova corail",
}
COLOURS = {word: canon for canon, words in _COLOUR_WORDS.items() for word in words.split()}

# Two-word colour names, rewritten to a single colour word before tokenising:
# Apple's "Lumière stellaire" is Starlight, "Bleu nuit" is navy.
_COLOUR_PHRASES = [
    (re.compile(r"\blumi[eè]re\s+stellaire\b", re.IGNORECASE), "Starlight"),
    (re.compile(r"\bbleu\s+nuit\b", re.IGNORECASE), "Navy"),
]


def replace_colour_phrases(text: str) -> str:
    """'AirPods Max Lumière stellaire' -> 'AirPods Max Starlight'."""
    for pattern, colour in _COLOUR_PHRASES:
        text = pattern.sub(colour, text)
    return text

# Category words and filler that carry no identity.
STOPWORDS = {
    "sluchawki", "bezprzewodowe", "douszne", "nauszne", "dokanalowe", "kopfhorer", "headset",
    "wireless", "bluetooth", "true", "tws", "in", "ear",
    "over", "on", "oordopjes", "koptelefoon", "draadloze", "sluchatka", "bezdratova", "casque",
    "ecouteurs", "sans", "fil", "audio", "with", "z", "mit", "met", "s", "avec", "and", "und",
    "noise", "cancelling", "the", "de", "mikrofonem", "mikrofon", "micro", "bt", "etui", "case", "et", "pour",
}
# Note: "anc", "nc", "pro", "lite", "headphones", "earbuds" are NOT stopwords —
# they distinguish models (JBL Tune 760 vs 760NC, Bose QC Ultra Headphones vs Earbuds).

# Generation markers in five languages -> one token: "(2e gen)", "2.Gen",
# "2nd generation", "2. generacji", "2ème génération" -> "gen2".
_GENERATION = re.compile(r"\(?\b(\d)\s*(?:st|nd|rd|th|e|eme|\.)?\s*(?:gen|generation|generacji|generace|generatie)\b\.?\)?")


# Spec fragments with numbers that are not model numbers: "Bluetooth 5.3",
# "BT 5.0", "30h", "40 mm", "IP54", "USB-C", "3.5 mm".
_SPEC_NOISE = re.compile(
    r"\b(?:bluetooth|bt)\s*\d(?:[.,]\d)?\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:h|hod|godz|mah|mm|g|hz|khz|db|ms|m)\b"
    r"|\bipx?\d+\b"
    r"|\busb[\s-]?c\b"
)


# Product condition markers in the five shop languages (matched on accent-free
# lowercase text of the name and URL). Anything that is not brand-new is kept
# out of matching with new products, the compare sheet and alerts.
CONDITION_PATTERNS = [
    ("open_box", re.compile(
        r"\b(?:rozbalen\w*|powystawow\w*|b-?ware|open[\s-]?box|tweede[\s-]?kans|tweedekans|outlet"
        r"|deballe\w*|ausgepackt|vystaven\w*|rozpakowan\w*|retourware)\b")),
    ("refurbished", re.compile(
        r"\b(?:refurbished|refurb|generaluberholt\w*|reconditionn\w*|odnowion\w*|repasovan\w*|renewed|renove\w*)\b")),
    ("used", re.compile(r"\b(?:uzywan\w*|gebraucht\w*|pouzit\w*|occasion|second[\s-]?hand)\b")),
]
CONDITION_TOKENS = {"openbox", "refurbished", "used"}


def detect_condition(text: str) -> str:
    """'new', 'open_box', 'refurbished' or 'used' from a product name and/or URL."""
    normalised = strip_accents(text).lower()
    return next((condition for condition, pattern in CONDITION_PATTERNS if pattern.search(normalised)), "new")


def strip_accents(text: str) -> str:
    text = text.replace("ł", "l").replace("Ł", "L").replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def name_tokens(name: str) -> list[str]:
    """Lowercase ASCII tokens; model codes like 'WH-1000XM5' become 'wh1000xm5'."""
    text = strip_accents(replace_colour_phrases(name)).lower()
    text = _GENERATION.sub(r" gen\1 ", text)
    for condition, pattern in CONDITION_PATTERNS:  # "Rozbaleno - JBL ..." -> "openbox jbl ..."
        text = pattern.sub(f" {condition.replace('_', '')} ", text)
    # bracketed codes "(MKFT4ZM/A)" are handled via MPN; "(Noir)" stays as a word
    text = re.sub(r"\((?=[^)]*\d)[^)]*\)", " ", text)
    text = _SPEC_NOISE.sub(" ", text)
    text = _HYPHENATED.sub(_join_codes, text)
    return re.findall(r"[a-z0-9+]+", text)


_HYPHENATED = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)+")


def _join_codes(match: re.Match) -> str:
    """'wh-1000xm5' -> 'wh1000xm5' (has a digit), 'biale-srebrne' -> 'biale srebrne'."""
    word = match.group()
    return re.sub(r"[-/]", "", word) if any(c.isdigit() for c in word) else re.sub(r"[-/]", " ", word)


def colours_in(name: str) -> set[str]:
    """Canonical colours mentioned in a name: 'Sony WH-1000XM6 Zwart' -> {'black'}."""
    return {COLOURS[t] for t in name_tokens(name) if t in COLOURS}


def core_tokens(name: str) -> list[str]:
    """Identity-bearing tokens: name minus colours and category filler."""
    return [t for t in name_tokens(name) if t not in COLOURS and t not in STOPWORDS]


# --- Brands --------------------------------------------------------------------

# Shops spell brands differently; keys and values are compact lowercase forms.
BRAND_ALIASES = {
    "soundcore": "anker",
    "soundcorebyanker": "anker",
    "ankersoundcore": "anker",
    "nothingtech": "nothing",
    "bw": "bowerswilkins",
    "beatsbydrdre": "beats",
    "logitechgaming": "logitech",
    "logitechg": "logitech",
    "jblbyharman": "jbl",
}
# Placeholder values some shops put in the brand field ("nezatříděno" = unsorted).
JUNK_BRANDS = {"nezatrideno", "noname", "generique", "generic", "economy", "inne", "other", "unknown", ""}


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", strip_accents(text).lower())


def canonical_brand(brand: str | None, name: str) -> str:
    """Brand key used for matching.

    'Soundcore by Anker' and 'Anker' -> 'anker'; 'Nothing Tech' -> 'nothing';
    a junk brand ('nezatříděno') is replaced by the brand at the start of the name.
    """
    key = _compact(brand or "")
    if key in JUNK_BRANDS:
        tokens = name_tokens(name)
        while tokens and tokens[0] in _CATEGORY_WORDS | CONDITION_TOKENS:  # "Rozbaleno - JBL ..." -> "JBL ..."
            tokens = tokens[1:]
        compact_name = "".join(tokens)
        known = sorted(set(BRAND_ALIASES) | set(BRAND_ALIASES.values()) | KNOWN_BRANDS, key=len, reverse=True)
        key = next((b for b in known if compact_name.startswith(b)), tokens[0] if tokens else "")
    return BRAND_ALIASES.get(key, key)


_CATEGORY_WORDS = {"sluchawki", "sluchatka", "casque", "ecouteurs", "kopfhorer", "headset", "oordopjes"}


# Brands whose names span several words in product titles (for junk-brand recovery).
KNOWN_BRANDS = {"audiotechnica", "bowerswilkins", "turtlebeach", "cambridgeaudio", "beyerdynamic", "steelseries"}


def base_model(name: str, brand: str | None = None) -> str:
    """Colour-agnostic key used to group variants, e.g. 'sony wh1000xm6'."""
    tokens = core_tokens(name)
    if brand:
        brand_tokens = name_tokens(brand)  # "Audio-Technica" -> ["audio", "technica"]
        tokens = [t for t in tokens if t not in brand_tokens]
        tokens.insert(0, " ".join(brand_tokens))
    return " ".join(dict.fromkeys(tokens))  # dedupe, keep order

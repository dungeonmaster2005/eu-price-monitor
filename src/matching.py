"""Cross-shop product matching.

Offers are clustered with a union-find in three passes of decreasing
reliability:

1. identical normalised EAN (GTIN-13, check digit verified);
2. identical normalised manufacturer part number, same brand;
3. similar names — merged automatically only with high confidence; medium
   confidence pairs go to a review list instead of being merged.

Manual decisions from config/match_overrides.yaml come first: "merge" forces
offers together, "split" forbids any pass from putting two offers together
(and removes the pair from the review list).

Colour variants are separate products (different EAN/MPN, conflicting colour
words), but share `base_model` so they can be grouped in reports.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.identifiers import base_model, canonical_brand, colours_in, core_tokens, normalize_mpn
from src.models import Product

log = logging.getLogger(__name__)

AUTO_MATCH_SCORE = 0.9
REVIEW_SCORE = 0.6
OVERRIDES_PATH = Path("config/match_overrides.yaml")


@dataclass
class MatchOverrides:
    """Manual matching decisions, keyed by offer URL."""

    merge: list[list[str]] = field(default_factory=list)
    split: list[list[str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path = OVERRIDES_PATH) -> MatchOverrides:
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(merge=raw.get("merge") or [], split=raw.get("split") or [])


@dataclass
class NameCandidate:
    """A name-based pair that was NOT merged automatically."""

    score: float
    left: Product
    right: Product
    reason: str


@dataclass
class MatchResult:
    groups: dict[str, list[Product]]
    review: list[NameCandidate] = field(default_factory=list)
    suspicious: list[tuple[str, list[Product]]] = field(default_factory=list)


class _UnionFind:
    """Union-find over offer indices that refuses to join forbidden pairs."""

    def __init__(self, size: int, forbidden: dict[int, set[int]] | None = None) -> None:
        self.parent = list(range(size))
        self.method = ["none"] * size
        self.members: dict[int, set[int]] = {i: {i} for i in range(size)}
        self.forbidden = forbidden or {}

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def can_union(self, a: int, b: int) -> bool:
        ma, mb = self.members[self.find(a)], self.members[self.find(b)]
        return not any(self.forbidden.get(i, set()) & mb for i in ma)

    def union(self, a: int, b: int, method: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if not self.can_union(a, b):
            return False
        self.parent[rb] = ra
        self.members[ra] |= self.members.pop(rb)
        # keep the strongest evidence that holds the cluster together
        self.method[ra] = _strongest(self.method[ra], self.method[rb], method)
        return True

    def cluster(self, i: int) -> set[int]:
        return self.members[self.find(i)]


_STRENGTH = {"manual": 4, "ean": 3, "mpn": 2, "name": 1, "none": 0}


def _strongest(*methods: str) -> str:
    return max(methods, key=_STRENGTH.__getitem__)


def brand_key(product: Product) -> str:
    """Canonical brand: aliases unified, junk values replaced by the name's brand."""
    return canonical_brand(product.brand, product.name)


def name_similarity(a: Product, b: Product) -> tuple[float, str]:
    """Confidence (0..1) that two offers are the same product, plus a reason."""
    if brand_key(a) != brand_key(b):
        return 0.0, "different brand"
    brand = brand_key(a)
    ta = {t for t in core_tokens(a.name) if t != brand}
    tb = {t for t in core_tokens(b.name) if t != brand}
    if not ta or not tb:
        return 0.0, "no model tokens"

    numeric_a = {t for t in ta if _is_model_number(t)}
    numeric_b = {t for t in tb if _is_model_number(t)}
    words_a, words_b = ta - numeric_a, tb - numeric_b
    word_overlap = len(words_a & words_b) / len(words_a | words_b) if words_a | words_b else 1.0

    if numeric_a and numeric_a == numeric_b:
        # identical model numbers are the main signal; words refine it
        score = 0.6 + 0.4 * word_overlap
        reason = f"same model numbers {sorted(numeric_a)}, word overlap {word_overlap:.2f}"
    elif numeric_a != numeric_b:
        score = min(0.5, word_overlap)
        reason = f"model numbers differ {sorted(numeric_a)} vs {sorted(numeric_b)}"
    else:
        score = 0.8 * word_overlap
        reason = f"no model numbers, word overlap {word_overlap:.2f}"

    ca, cb = colours_in(a.name), colours_in(b.name)
    if not numeric_a and not numeric_b and words_a == words_b and ca and ca == cb:
        # "Sennheiser Accentum Zwart" vs "SENNHEISER ACCENTUM černá": every word
        # and the colour agree — the only case a number-less name auto-matches
        return AUTO_MATCH_SCORE, "identical name words and colour"
    if ca and cb and not ca & cb:
        score = min(score, 0.5)
        reason = f"colour differs {sorted(ca)} vs {sorted(cb)}"
    elif bool(ca) != bool(cb):
        score *= 0.9
        reason += ", colour only on one side"
    return round(score, 2), reason


def _is_model_number(token: str) -> bool:
    """Tokens with digits identify a model — except generation markers ("gen2"),
    which are shared by unrelated products of one brand."""
    return any(c.isdigit() for c in token) and not (token.startswith("gen") and token[3:].isdigit())


def match_products(products: list[Product], overrides: MatchOverrides | None = None) -> MatchResult:
    overrides = overrides or MatchOverrides()
    index = {p.url: i for i, p in enumerate(products)}
    uf = _UnionFind(len(products), _forbidden_pairs(overrides, index))

    # 0. Manual merges
    for urls in overrides.merge:
        present = [index[u] for u in urls if u in index]
        for j in present[1:]:
            if not uf.union(present[0], j, "manual"):
                log.error("match_overrides: merge %s conflicts with a split rule", urls)

    # 1. EAN
    by_ean: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(products):
        if p.ean:
            by_ean[p.ean].append(i)
    for idx in by_ean.values():
        for j in idx[1:]:
            uf.union(idx[0], j, "ean")

    # 2. MPN (same brand only, so short codes of different brands never collide)
    by_mpn: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, p in enumerate(products):
        key = normalize_mpn(p.model, p.brand)
        if key:
            by_mpn[(brand_key(p), key)].append(i)
    review: list[NameCandidate] = []
    for idx in by_mpn.values():
        for j in idx[1:]:
            # Same MPN but different colours means one shop's data is wrong
            # (Nothing Headphone (a) A11300030: pink at one shop, black at another).
            cluster_colours = set().union(*(colours_in(products[k].name) for k in uf.cluster(idx[0])))
            own = colours_in(products[j].name)
            if cluster_colours and own and not cluster_colours & own:
                if uf.can_union(idx[0], j) and products[idx[0]].site != products[j].site:
                    review.append(NameCandidate(1.0, products[idx[0]], products[j],
                                                f"same MPN, colour differs {sorted(cluster_colours)} vs {sorted(own)}"))
                continue
            uf.union(idx[0], j, "mpn")

    # 3. Names — compare clusters that have no site in common, within a brand
    review += _match_by_name(products, uf)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(products)):
        groups[uf.find(i)].append(i)

    result = MatchResult(groups={}, review=review)
    for root, members in groups.items():
        offers = [products[i] for i in members]
        method = uf.method[root] if len(members) > 1 else "none"
        match_id = _stable_id(offers)
        if match_id in result.groups:  # only possible when a manual split separated equal ids
            match_id = f"{match_id}#{min(p.url for p in offers)[-12:]}"
        group_base = Counter(base_model(p.name, p.brand) for p in offers).most_common(1)[0][0]
        for p in offers:
            p.match_id, p.match_method, p.base_model = match_id, method, group_base
        result.groups[match_id] = offers
        if method in ("ean", "mpn") and _looks_inconsistent(offers):
            result.suspicious.append((match_id, offers))
    return result


def _match_by_name(products: list[Product], uf: _UnionFind) -> list[NameCandidate]:
    by_brand: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(products):
        by_brand[brand_key(p)].append(i)

    candidates: list[tuple[float, int, int, str]] = []
    for idx in by_brand.values():
        for x, i in enumerate(idx):
            for j in idx[x + 1:]:
                if products[i].site == products[j].site:
                    continue
                score, reason = name_similarity(products[i], products[j])
                if score >= REVIEW_SCORE:
                    candidates.append((score, i, j, reason))

    review = []
    for score, i, j, reason in sorted(candidates, reverse=True):
        if uf.find(i) == uf.find(j) or not uf.can_union(i, j):
            continue  # already together, or manually split
        if _clusters_share_site(products, uf, i, j) or _clusters_have_ids_conflict(products, uf, i, j):
            continue
        if score >= AUTO_MATCH_SCORE:
            uf.union(i, j, "name")
        else:
            review.append(NameCandidate(score, products[i], products[j], reason))
    return review


def _forbidden_pairs(overrides: MatchOverrides, index: dict[str, int]) -> dict[int, set[int]]:
    forbidden: dict[int, set[int]] = defaultdict(set)
    for pair in overrides.split:
        if len(pair) != 2:
            log.error("match_overrides: split entries must be pairs, got %s", pair)
            continue
        a, b = (index.get(u) for u in pair)
        if a is not None and b is not None:
            forbidden[a].add(b)
            forbidden[b].add(a)
    return forbidden


def _cluster(products: list[Product], uf: _UnionFind, i: int) -> list[Product]:
    return [products[k] for k in uf.cluster(i)]


def _clusters_share_site(products: list[Product], uf: _UnionFind, i: int, j: int) -> bool:
    sites_i = {p.site for p in _cluster(products, uf, i)}
    return any(p.site in sites_i for p in _cluster(products, uf, j))


def _clusters_have_ids_conflict(products: list[Product], uf: _UnionFind, i: int, j: int) -> bool:
    """Two clusters that both carry EANs, all different, are different products."""
    eans_i = {p.ean for p in _cluster(products, uf, i) if p.ean}
    eans_j = {p.ean for p in _cluster(products, uf, j) if p.ean}
    return bool(eans_i and eans_j and not eans_i & eans_j)


def _stable_id(offers: list[Product]) -> str:
    """Deterministic id so the same product keeps its id from day to day."""
    eans = sorted(p.ean for p in offers if p.ean)
    if eans:
        return f"ean:{eans[0]}"
    mpns = sorted(filter(None, (normalize_mpn(p.model, p.brand) for p in offers)))
    if mpns:
        return f"mpn:{brand_key(offers[0])}:{mpns[0]}"
    # No identifiers: readable name key plus a short hash of the anchor URL, so
    # two different products with similar names can never share an id.
    first = min(offers, key=lambda p: p.url)
    colour = ",".join(sorted(colours_in(first.name))) or "-"
    digest = hashlib.sha1(first.url.encode()).hexdigest()[:6]
    return f"name:{base_model(first.name, first.brand)}|{colour}#{digest}"


def _looks_inconsistent(offers: list[Product]) -> bool:
    """Flag id-based clusters whose names share almost nothing (bad EAN/MPN data)."""
    token_sets = [set(core_tokens(p.name)) for p in offers]
    common = set.intersection(*token_sets)
    return len({p.site for p in offers}) > 1 and len(common) <= 1

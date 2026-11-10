"""Day-over-day change detection for Telegram alerts.

Compares today's offers with the previous observation of each offer
(data/offers_latest.json, i.e. the last day it was actually seen):

- price_drop    one offer got cheaper by >= N %, in the shop's own currency;
- new_leader    a product's cheapest country in Europe changed and the new
                leader beats the next country by >= M % (both days valued at
                today's exchange rates, so FX moves alone never trigger it);
- out_of_stock  an offer that was in stock is now out of stock;
- new_product   an offer never seen before on a site we already monitor.

Never alerted: stale sites, carried-over offers, marketplace partners (if
`official_only`), and offers merely missing from a listing — the monitor reads
the top N listing pages, so products drifting out of them are not "gone".
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from src.models import Product
from src.tables import english_name, group_by_product

log = logging.getLogger(__name__)

CONFIG_PATH = Path("config/alerts.yaml")
KINDS = ("price_drop", "new_leader", "out_of_stock", "new_product")


@dataclass
class AlertConfig:
    price_drop_pct: float = 5.0
    new_leader_margin_pct: float = 1.0
    official_only: bool = True
    top_n_per_group: int = 5
    groups: list[str] = field(default_factory=lambda: list(KINDS))

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> AlertConfig:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        unknown = set(raw.get("groups", [])) - set(KINDS)
        if unknown:
            raise ValueError(f"alerts.yaml: unknown groups {sorted(unknown)}")
        return cls(**raw)


@dataclass
class Alert:
    kind: str
    product: str  # English product title
    url: str
    country: str
    site: str
    magnitude: float  # what the group is sorted by (biggest first)
    details: dict[str, Any] = field(default_factory=dict)


def detect(
    products: list[Product],
    previous: dict[str, dict[str, Any]],
    stale_sites: set[str],
    config: AlertConfig,
) -> list[Alert]:
    """All alerts for today, given the previous observation of each offer URL."""
    if not previous:
        log.info("No previous observations — first run, no alerts")
        return []

    groups = group_by_product(products)
    titles = {match_id: english_name(offers) for match_id, offers in groups.items()}
    known_sites = {row["site"] for row in previous.values()}

    def eligible(p: Product) -> bool:
        return not p.stale and p.site not in stale_sites and (p.is_official or not config.official_only)

    alerts: list[Alert] = []
    for p in filter(eligible, products):
        title = titles[p.match_id or p.url]
        prev = previous.get(p.url)
        if prev is None:
            if p.site in known_sites and p.in_stock is not False:
                alerts.append(Alert("new_product", title, p.url, p.country, p.site, float(p.price_eur or 0),
                                    {"price": p.price, "currency": p.currency, "price_eur": p.price_eur}))
            continue
        alerts += _offer_changes(p, prev, title, config)

    for match_id, offers in groups.items():
        alert = _leader_change(match_id, [p for p in offers if eligible(p)], previous, titles[match_id], config)
        if alert:
            alerts.append(alert)

    wanted = set(config.groups)
    alerts = [a for a in alerts if a.kind in wanted]
    log.info("Alerts: %s", {k: sum(a.kind == k for a in alerts) for k in config.groups})
    return alerts


def _offer_changes(p: Product, prev: dict[str, Any], title: str, config: AlertConfig) -> list[Alert]:
    found = []
    old_price = Decimal(str(prev["price"]))
    if prev.get("currency") == p.currency and p.in_stock is not False and old_price > 0:
        drop_pct = float((old_price - p.price) / old_price * 100)
        if drop_pct >= config.price_drop_pct:
            found.append(Alert("price_drop", title, p.url, p.country, p.site, drop_pct,
                               {"old": old_price, "new": p.price, "currency": p.currency}))
    if prev.get("in_stock") is True and p.in_stock is False:
        found.append(Alert("out_of_stock", title, p.url, p.country, p.site, float(p.price_eur or 0),
                           {"price": p.price, "currency": p.currency}))
    return found


def _leader_change(match_id: str, offers: list[Product], previous: dict[str, dict[str, Any]],
                   title: str, config: AlertConfig) -> Alert | None:
    """New cheapest country in Europe, with an FX-neutral comparison of both days."""
    today: dict[str, tuple[Decimal, Product]] = {}
    yesterday: dict[str, Decimal] = defaultdict(lambda: Decimal("Infinity"))
    for p in offers:
        if p.in_stock is False or not p.price_eur or not p.price:
            continue
        if p.country not in today or p.price_eur < today[p.country][0]:
            today[p.country] = (p.price_eur, p)
        prev = previous.get(p.url)
        if prev and prev.get("in_stock") is not False and prev.get("currency") == p.currency:
            rate = p.price_eur / p.price  # today's EUR rate for this currency
            yesterday[p.country] = min(yesterday[p.country], Decimal(str(prev["price"])) * rate)

    if len(today) < 2 or len(yesterday) < 2:
        return None
    ranked = sorted(today.items(), key=lambda item: item[1][0])
    (leader, (low, best)), (_, (runner_up, _)) = ranked[0], ranked[1]
    old_leader = min(yesterday, key=yesterday.get)
    if leader == old_leader:
        return None
    margin_pct = float((runner_up - low) / low * 100)
    if margin_pct < config.new_leader_margin_pct:
        return None
    return Alert("new_leader", title, best.url, leader, best.site, margin_pct,
                 {"price_eur": low, "old_country": old_leader, "old_site": _site_of(offers, old_leader)})


def _site_of(offers: list[Product], country: str) -> str:
    return next((p.site for p in offers if p.country == country), "")

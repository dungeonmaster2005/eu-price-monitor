"""Telegram message formatting and delivery.

Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Messages use Telegram's
HTML parse mode; nothing is sent when there are no alerts.
"""

from __future__ import annotations

import html
import logging
import os
from datetime import date
from decimal import Decimal

import requests

from src.analysis import Alert, AlertConfig
from src.tables import PRICE_NOTE

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
FLAGS = {"PL": "🇵🇱", "DE": "🇩🇪", "NL": "🇳🇱", "CZ": "🇨🇿", "FR": "🇫🇷"}
GROUP_TITLES = {
    "price_drop": "📉 Price drops",
    "new_leader": "🏆 New cheapest in Europe",
    "out_of_stock": "🚫 Out of stock",
    "new_product": "🆕 New products",
}


def money(value: Decimal | float, currency: str) -> str:
    return f"{value:,.2f} €" if currency == "EUR" else f"{value:,.2f} {currency}"


def _where(country: str, site: str) -> str:
    return f"{FLAGS.get(country, country)} {site}"


def _line(alert: Alert) -> str:
    d = alert.details
    link = f'<a href="{html.escape(alert.url, quote=True)}">{html.escape(alert.product)}</a>'
    where = _where(alert.country, alert.site)
    if alert.kind == "price_drop":
        return (f"• {link} — {where}: {money(d['old'], d['currency'])} → "
                f"<b>{money(d['new'], d['currency'])}</b> (−{alert.magnitude:.1f}%)")
    if alert.kind == "new_leader":
        return (f"• {link} — now {where} <b>{money(d['price_eur'], 'EUR')}</b> "
                f"(was {_where(d['old_country'], d['old_site'])}), {alert.magnitude:.1f}% below next")
    if alert.kind == "out_of_stock":
        return f"• {link} — {where}, was {money(d['price'], d['currency'])}"
    price = money(d["price"], d["currency"])
    eur = f" ({money(d['price_eur'], 'EUR')})" if d["currency"] != "EUR" and d.get("price_eur") else ""
    return f"• {link} — {where} {price}{eur}"


def build_message(alerts: list[Alert], config: AlertConfig, day: date,
                  stale_sites: set[str] = frozenset(), sheet_url: str | None = None) -> str | None:
    """Grouped message, top N per group by size of change; None if nothing to say."""
    if not alerts:
        return None
    parts = [f"<b>EU headphone price monitor — {day:%d %b %Y}</b>"]
    for kind in config.groups:
        items = sorted((a for a in alerts if a.kind == kind), key=lambda a: a.magnitude, reverse=True)
        if not items:
            continue
        lines = [f"\n<b>{GROUP_TITLES[kind]} ({len(items)})</b>"]
        lines += [_line(a) for a in items[: config.top_n_per_group]]
        if len(items) > config.top_n_per_group:
            lines.append(f"…and {len(items) - config.top_n_per_group} more")
        parts.append("\n".join(lines))

    footer = [f"\n<i>{PRICE_NOTE} Shops' own offers only.</i>"]
    if stale_sites:
        footer.append(f"<i>No alerts for stale sites today: {', '.join(sorted(stale_sites))}.</i>")
    if sheet_url:
        footer.append(f'<a href="{html.escape(sheet_url, quote=True)}">Full comparison table</a>')
    message = "\n".join(parts + footer)
    if len(message) > TELEGRAM_LIMIT:  # cannot happen with sane top_n, but never fail on it
        message = message[: TELEGRAM_LIMIT - 20].rsplit("\n", 1)[0] + "\n…(truncated)"
    return message


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> None:
    token, chat_id = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=20,
    )
    if not resp.ok:
        # never log the URL: it contains the bot token
        raise RuntimeError(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
    log.info("Telegram message sent (%d chars)", len(text))


def sample_alerts() -> list[Alert]:
    """Fixed example used by --test-alert."""
    return [
        Alert("price_drop", "Sony WH-1000XM6 Black", "https://www.morele.net/", "PL", "morele", 11.8,
              {"old": Decimal("1699.00"), "new": Decimal("1499.00"), "currency": "PLN"}),
        Alert("new_leader", "Apple AirPods Pro 3 White", "https://www.coolblue.nl/", "NL", "coolblue", 3.2,
              {"price_eur": Decimal("211.00"), "old_country": "DE", "old_site": "alternate_de"}),
        Alert("out_of_stock", "AKG N9 Hybrid Black", "https://www.ldlc.com/", "FR", "ldlc", 249.0,
              {"price": Decimal("249.00"), "currency": "EUR"}),
        Alert("new_product", "Samsung Galaxy Buds4 Pro Black", "https://www.planeo.cz/", "CZ", "planeo", 171.97,
              {"price": Decimal("4190"), "currency": "CZK", "price_eur": Decimal("171.97")}),
    ]

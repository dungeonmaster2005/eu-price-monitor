"""Daily pipeline: scrape -> judge site health -> EUR -> match -> save -> Google Sheets.

    python -m src.main                          # full daily run
    python -m src.main --site morele --max-pages 2   # debug one site (no Sheets)
    python -m src.main --from-file data/x.json  # re-run matching/Sheets on saved offers
    python -m src.main --from-file today.json --baseline yesterday.json --no-sheets  # alert dry run
    python -m src.main --test-alert              # send an example Telegram message
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.analysis import AlertConfig, detect
from src.config import load_sites
from src.currency import FxRates
from src.http_client import PoliteClient
from src.id_cache import IdCache
from src.matching import MatchOverrides, MatchResult, match_products
from src.models import Product
from src.notify import build_message, sample_alerts, send_telegram, telegram_configured
from src.offer_state import OfferState
from src.scrapers import SCRAPERS
from src.site_status import SiteRun, SiteStatusStore

log = logging.getLogger("main")
OUTPUT_DIR = Path("data")


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EU wireless-headphone price monitor")
    parser.add_argument("--site", action="append", help="only this site id (repeatable); skips Sheets")
    parser.add_argument("--max-pages", type=int, help="override max listing pages per site")
    parser.add_argument("--no-details", action="store_true", help="skip product pages (no EAN/MPN)")
    parser.add_argument("--from-file", type=Path, help="use saved offers JSON instead of scraping")
    parser.add_argument("--no-sheets", action="store_true", help="do not write to Google Sheets")
    parser.add_argument("--no-history", action="store_true", help="write Sheets but do not append to history")
    parser.add_argument("--baseline", type=Path, help="with --from-file: offers JSON to treat as 'yesterday'")
    parser.add_argument("--no-telegram", action="store_true", help="build the alert message but do not send it")
    parser.add_argument("--test-alert", action="store_true", help="send an example alert message and exit")
    return parser.parse_args()


# --- scraping -------------------------------------------------------------------

def select_sites(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    defaults, sites = load_sites()
    sites = [s for s in sites if s["id"] in args.site] if args.site else [s for s in sites if s.get("enabled")]
    for site in sites:
        if args.max_pages:
            site["max_pages"] = args.max_pages
        if args.no_details:
            site["fetch_details"] = False
    return defaults, sites


def scrape_site(site: dict[str, Any], defaults: dict[str, Any], id_cache: IdCache) -> tuple[list[Product], SiteRun]:
    """Run one scraper. Any failure is logged and reported as an incomplete run."""
    scraper_cls = SCRAPERS.get(site.get("scraper", site["id"]))
    if scraper_cls is None:
        return [], SiteRun(site["id"], site["country"], 0, False, ["no scraper class configured"])
    client = PoliteClient(
        user_agent=defaults["user_agent"],
        delay_range=tuple(site.get("delay_seconds", (2, 5))),
        timeout=site.get("timeout_seconds", 25),
    )
    scraper = scraper_cls(site, client, id_cache)
    try:
        products = scraper.scrape()
    except Exception as exc:  # one broken site must never stop the others
        log.exception("Scraper %s failed", site["id"])
        return [], SiteRun(site["id"], site["country"], 0, False, [f"crashed: {exc!r}"])
    return products, SiteRun(site["id"], site["country"], len(products), scraper.listing_complete, scraper.problems)


def scrape_all(defaults: dict[str, Any], sites: list[dict[str, Any]]) -> tuple[list[Product], list[SiteRun]]:
    """Sites run in parallel (each keeps its own polite per-host delay)."""
    id_cache = IdCache()
    with ThreadPoolExecutor(max_workers=max(1, len(sites))) as pool:
        results = list(pool.map(lambda s: scrape_site(s, defaults, id_cache), sites))
    return [p for products, _ in results for p in products], [run for _, run in results]


def load_products(path: Path) -> list[Product]:
    """Rebuild Product objects from a saved products_<date>.json (offline mode)."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    fields = {"site", "country", "name", "price", "currency", "url", "in_stock", "model", "ean", "brand",
              "seller", "is_official"}
    products = []
    for row in rows:
        kwargs = {k: row[k] for k in fields}
        kwargs["price"] = Decimal(row["price"])
        day = date.fromisoformat(row["date"])
        products.append(Product(**kwargs, date=day, last_seen=day))
    return products


# --- processing -----------------------------------------------------------------

def convert_to_eur(products: list[Product]) -> FxRates:
    fx = FxRates()
    fx.load({p.currency for p in products})
    for p in products:
        p.price_eur = fx.to_eur(p.price, p.currency)
    return fx


def save(products: list[Product], matches: MatchResult) -> Path:
    """Write the day's offers and the name-match review list to data/."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    stem = OUTPUT_DIR / f"products_{date.today().isoformat()}"
    rows = [p.to_row() for p in products]
    stem.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if rows:
        with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    review = [
        {"score": c.score, "reason": c.reason,
         "left": f"[{c.left.country}] {c.left.name}", "right": f"[{c.right.country}] {c.right.name}",
         "left_url": c.left.url, "right_url": c.right.url}
        for c in matches.review
    ]
    (OUTPUT_DIR / "match_review.json").write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    return stem.with_suffix(".csv")


def log_summary(products: list[Product], matches: MatchResult) -> None:
    log.info("Offers per site: %s", dict(Counter(p.site for p in products)))
    countries_per_group = Counter(
        len({p.country for p in offers if p.is_official}) for offers in matches.groups.values()
    )

    def at_least(n: int) -> int:
        return sum(count for k, count in countries_per_group.items() if k >= n)

    log.info("Products seen in 2+ countries: %d, 3+: %d, all 5: %d (official sellers only)",
             at_least(2), at_least(3), at_least(5))
    log.info("Name matches left for review: %d, suspicious id matches: %d",
             len(matches.review), len(matches.suspicious))


def write_sheets(products: list[Product], fresh: list[Product], statuses: SiteStatusStore, fx: FxRates,
                 write_history: bool = True) -> None:
    from src.sheets import SheetsWriter, open_spreadsheet, sheets_configured

    if not sheets_configured():
        log.warning("Google Sheets not configured (SHEET_ID + GOOGLE_CREDS/_PATH) — skipping")
        return
    try:
        SheetsWriter(open_spreadsheet(), fx.as_of).write_all(
            products, fresh, list(statuses.sites.values()), today=date.today(), write_history=write_history)
    except Exception:
        log.exception("Writing Google Sheets failed")


def sheet_url() -> str | None:
    sheet_id = os.getenv("SHEET_ID")
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}" if sheet_id else None


def alert(products: list[Product], previous: dict[str, dict], stale_sites: set[str], send: bool) -> None:
    """Detect changes, save the message to data/alert_message.txt and send it to Telegram."""
    config = AlertConfig.load()
    message = build_message(detect(products, previous, stale_sites, config), config, date.today(),
                            stale_sites, sheet_url())
    if message is None:
        log.info("No changes worth an alert — nothing sent")
        return
    (OUTPUT_DIR / "alert_message.txt").write_text(message, encoding="utf-8")
    log.info("Alert message:\n%s", message)
    if not send:
        log.info("Telegram sending skipped")
    elif not telegram_configured():
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID) — not sent")
    else:
        try:
            send_telegram(message)
        except Exception:
            log.exception("Sending Telegram message failed")


def load_baseline(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    return {row["url"]: row for row in json.loads(path.read_text(encoding="utf-8"))}


def main() -> None:
    load_dotenv()
    setup_logging()
    args = parse_args()

    if args.test_alert:
        config = AlertConfig.load()
        text = "[TEST] " + build_message(sample_alerts(), config, date.today(), sheet_url=sheet_url())
        log.info("Test message:\n%s", text)
        send_telegram(text)
        return

    statuses = SiteStatusStore()
    if args.from_file:
        products = load_products(args.from_file)
        fresh = products
        previous = load_baseline(args.baseline)
        stale_sites = {site for site, s in statuses.sites.items() if s.stale}
        log.info("Loaded %d offers from %s", len(products), args.from_file)
    else:
        fresh, runs = scrape_all(*select_sites(args))
        for run in runs:
            statuses.record(run)
        stale_sites = {run.site for run in runs if statuses.is_stale(run.site)}
        offer_state = OfferState()
        previous = dict(offer_state.offers)  # last observation of every offer, before today
        products = offer_state.merge_today(fresh, stale_sites)
        offer_state.save()
        statuses.save()

    if not products:
        log.error("No products scraped and nothing to carry over")
        return
    fx = convert_to_eur(products)
    matches = match_products(products, MatchOverrides.load())
    path = save(products, matches)
    log_summary(products, matches)
    log.info("Saved %d offers to %s", len(products), path)

    if args.no_sheets or args.site:
        log.info("Sheets skipped (%s)", "--no-sheets" if args.no_sheets else "partial --site run")
    else:
        write_sheets(products, fresh, statuses, fx, write_history=not args.no_history)

    # A partial --site run would report every other site's products as unchanged/new.
    alert(products, previous if not args.site else {}, stale_sites, send=not (args.no_telegram or args.site))


if __name__ == "__main__":
    main()

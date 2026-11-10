"""Per-site run health: decides whether today's data from a site can be trusted.

A site run is STALE when the listing was blocked or failed part-way, or when
it returned far fewer products than usual. Stale sites are never treated as
"products disappeared" and produce no alerts; their last known offers are
carried over instead (see offer_state.py).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from statistics import median

log = logging.getLogger(__name__)

STATUS_PATH = Path("data/site_status.json")
MIN_SHARE_OF_USUAL = 0.7  # fewer than 70% of the usual product count -> stale
HISTORY_LENGTH = 14


@dataclass
class SiteRun:
    """Outcome of scraping one site today."""

    site: str
    country: str
    count: int
    listing_complete: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class SiteStatus:
    site: str
    country: str
    last_run: str | None = None
    last_success: str | None = None
    count_today: int = 0
    usual_count: int | None = None
    stale: bool = False
    reason: str = ""
    recent_counts: list[int] = field(default_factory=list)


class SiteStatusStore:
    def __init__(self, path: Path = STATUS_PATH) -> None:
        self.path = path
        self.sites: dict[str, SiteStatus] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.sites = {site: SiteStatus(**data) for site, data in raw.items()}

    def record(self, run: SiteRun, today: date | None = None) -> SiteStatus:
        """Judge today's run against the site's history and remember the result."""
        today_str = (today or date.today()).isoformat()
        status = self.sites.get(run.site) or SiteStatus(site=run.site, country=run.country)
        usual = int(median(status.recent_counts)) if status.recent_counts else None

        reasons = []
        if not run.listing_complete:
            reasons.append("listing incomplete (" + "; ".join(run.problems) + ")")
        if run.count == 0:
            reasons.append("no products collected")
        elif usual and run.count < MIN_SHARE_OF_USUAL * usual:
            reasons.append(f"only {run.count} products vs usual {usual}")

        status.country = run.country
        status.last_run = today_str
        status.count_today = run.count
        status.usual_count = usual
        status.stale = bool(reasons)
        status.reason = "; ".join(reasons) or "; ".join(run.problems)  # non-fatal notes for OK runs
        if not status.stale:
            status.last_success = today_str
            status.recent_counts = (status.recent_counts + [run.count])[-HISTORY_LENGTH:]
            log.info("%s: OK, %d products (usual %s)", run.site, run.count, usual or "n/a")
        else:
            log.warning("%s: STALE — %s", run.site, status.reason)
        self.sites[run.site] = status
        return status

    def is_stale(self, site: str) -> bool:
        status = self.sites.get(site)
        return bool(status and status.stale)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {site: asdict(s) for site, s in sorted(self.sites.items())}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

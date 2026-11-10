"""Polite HTTP client: robots.txt check, per-host delays, honest User-Agent."""

from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; EUPriceMonitor/0.1; portfolio demo)"
ROBOTS_AGENT_TOKEN = "EUPriceMonitor"


class FetchError(Exception):
    """A page could not be fetched (network error, bad status, robots.txt)."""


class BlockedError(FetchError):
    """The site actively refused us (403/429, captcha). Never worked around."""


class PoliteClient:
    """requests.Session wrapper that behaves like a well-mannered crawler.

    - consults robots.txt once per host and refuses disallowed URLs;
    - waits a random 2–5 s between consecutive requests to the same host;
    - retries transient 5xx errors with backoff, but treats 403/429 as a block.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay_range: tuple[float, float] = (2.0, 5.0),
        timeout: float = 25.0,
        retries: int = 2,
    ) -> None:
        self.delay_range = delay_range
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en;q=0.8,*;q=0.5",
            }
        )
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}

    # -- public API ---------------------------------------------------------

    def get_html(self, url: str) -> str:
        """Fetch a page and return its text, honouring robots.txt and delays."""
        if not self.allowed(url):
            raise FetchError(f"robots.txt disallows {url}")

        for attempt in range(self.retries + 1):
            self._wait_turn(url)
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise FetchError(f"{url}: {exc}") from exc
                log.warning("Network error on %s (%s), retrying", url, exc)
                continue

            if resp.status_code in (403, 429):
                challenge = " (Cloudflare challenge)" if resp.headers.get("cf-mitigated") else ""
                raise BlockedError(f"{url}: HTTP {resp.status_code}{challenge}")
            if resp.status_code >= 500 and attempt < self.retries:
                log.warning("HTTP %s on %s, retrying", resp.status_code, url)
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise FetchError(f"{url}: HTTP {resp.status_code}")
            return resp.text

        raise FetchError(f"{url}: retries exhausted")  # pragma: no cover

    def allowed(self, url: str) -> bool:
        """True if robots.txt of the URL's host permits fetching it."""
        return self._robots_for(url).can_fetch(ROBOTS_AGENT_TOKEN, url)

    # -- internals ----------------------------------------------------------

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            parser = RobotFileParser()
            try:
                resp = self.session.get(f"{host}/robots.txt", timeout=self.timeout)
                # Per RFC 9309: 4xx means "no rules", 5xx means "assume disallowed".
                if resp.status_code >= 500:
                    parser.disallow_all = True
                else:
                    parser.parse(resp.text.splitlines() if resp.ok else [])
            except requests.RequestException:
                log.warning("robots.txt unreachable for %s, treating as disallowed", host)
                parser.disallow_all = True
            self._robots[host] = parser
            self._last_request[parts.netloc] = time.monotonic()
        return self._robots[host]

    def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            delay = random.uniform(*self.delay_range)
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()

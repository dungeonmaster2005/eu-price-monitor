"""Persistent cache of product identifiers (EAN, MPN, brand) keyed by URL.

Identifiers live on product pages, so collecting them costs one request per
product. They never change for a given URL, so we fetch each page once and
reuse the result on every following run.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/product_ids.json")


@dataclass
class ProductIds:
    ean: str | None = None
    model: str | None = None
    brand: str | None = None


class IdCache:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._data: dict[str, ProductIds] = {}
        self._lock = threading.Lock()  # scrapers run in parallel threads
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._data = {url: ProductIds(**ids) for url, ids in raw.items()}
            except (json.JSONDecodeError, TypeError) as exc:
                log.warning("Ignoring unreadable id cache %s: %s", path, exc)

    def get(self, url: str) -> ProductIds | None:
        return self._data.get(url)

    def put(self, url: str, ids: ProductIds) -> None:
        with self._lock:
            self._data[url] = ids

    def save(self) -> None:
        """Write sorted, pretty JSON — stable diffs, safe to commit (no secrets)."""
        with self._lock:
            payload = {url: asdict(ids) for url, ids in sorted(self._data.items())}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def __len__(self) -> int:
        return len(self._data)

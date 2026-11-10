"""Loading of config/sites.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SITES_PATH = Path("config/sites.yaml")


def load_sites(path: Path = SITES_PATH) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (defaults, site configs) with defaults merged into every site."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults: dict[str, Any] = raw.get("defaults", {})
    sites = [{**defaults, **site} for site in raw.get("sites", [])]
    return defaults, sites

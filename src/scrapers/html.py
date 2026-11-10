"""Small HTML helpers shared by scrapers."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def text_of(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())
    return text or None


def absolute(base_url: str, href: str | None) -> str | None:
    return urljoin(base_url, href) if href else None


def spec_value(soup: BeautifulSoup, *labels: str) -> str | None:
    """Value next to a label in a specification table/list.

    Works for <th>Label</th><td>Value</td>, <dt>Label</dt><dd>Value</dd> and
    label wrapped in an extra <p>/<span> inside the header cell.
    """
    wanted = re.compile(rf"^\s*(?:{'|'.join(map(re.escape, labels))})\s*:?\s*$", re.I)
    for label in soup.find_all(string=wanted):
        cell = label.parent
        while cell is not None and cell.name not in ("th", "dt", "td", "li", "div"):
            cell = cell.parent
        if cell is None:
            continue
        value = cell.find_next_sibling(("td", "dd", "div", "span"))
        if value is not None and text_of(value):
            return text_of(value)
    return None

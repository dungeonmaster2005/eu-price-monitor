"""Site scrapers and the registry that maps `scraper:` in sites.yaml to a class."""

from src.scrapers.alternate import AlternateScraper
from src.scrapers.base import BaseScraper
from src.scrapers.coolblue import CoolblueScraper
from src.scrapers.ldlc import LdlcScraper
from src.scrapers.mironet import MironetScraper
from src.scrapers.morele import MoreleScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    "morele": MoreleScraper,
    "alternate": AlternateScraper,
    "coolblue": CoolblueScraper,
    "mironet": MironetScraper,
    "ldlc": LdlcScraper,
}

__all__ = ["SCRAPERS", "BaseScraper"]

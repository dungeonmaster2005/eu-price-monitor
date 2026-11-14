"""Captcha pages served with HTTP 200 are blocks, not empty listings."""

from types import SimpleNamespace

import pytest

from src.http_client import BlockedError, PoliteClient, captcha_reason

ALTCHA_HTML = "<html><head><title>Ověřte, že nejste robot</title></head><body>Nejsem robot</body></html>"
LISTING_HTML = "<html><head><title>Bezdrátová sluchátka | Planeo</title></head><body>...</body></html>"


def test_redirect_to_altcha_is_a_captcha():
    reason = captcha_reason("https://www.mironet.cz/altcha/verify?ctx=645bd2e5", ALTCHA_HTML)
    assert reason and "altcha" in reason


def test_captcha_title_without_redirect():
    assert captcha_reason("https://shop.example/list/", ALTCHA_HTML) == "page title 'Ověřte, že nejste robot'"


def test_normal_page_is_not_a_captcha():
    assert captcha_reason("https://www.planeo.cz/bezdratova-sluchatka", LISTING_HTML) is None


def test_client_raises_blocked_on_captcha_redirect(monkeypatch):
    client = PoliteClient(delay_range=(0, 0))
    monkeypatch.setattr(client, "allowed", lambda url: True)
    response = SimpleNamespace(status_code=200, url="https://www.mironet.cz/altcha/verify?ctx=1",
                               text=ALTCHA_HTML, headers={})
    monkeypatch.setattr(client.session, "get", lambda url, timeout: response)
    with pytest.raises(BlockedError, match="captcha"):
        client.get_html("https://www.mironet.cz/audiovideo/sluchatka/bezdratova+c25216/")
    assert client.last_url == "https://www.mironet.cz/altcha/verify?ctx=1"

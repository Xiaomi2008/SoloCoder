"""ebay_search: the Browse API request it makes and the answer it gives, against a fake eBay."""

from __future__ import annotations

import base64

import httpx
import pytest

from openagent.apps.solocoder.agent import build_solocoder_tools
from openagent.runtime.permissions import ASK_BY_DEFAULT, tool_risk
from openagent.tools import ebay
from openagent.tools.ebay import ebay_search

# Captured once: `ebay.httpx` is the httpx module, so a patch is global, and a
# second patch must wrap the real client, not the first patch.
_REAL_CLIENT = httpx.AsyncClient

_LISTING = {
    "title": "Sony WH-1000XM5 Headphones",
    "price": {"value": "189.99", "currency": "USD"},
    "condition": "Used",
    "buyingOptions": ["FIXED_PRICE"],
    "seller": {"username": "audio_shop", "feedbackPercentage": "99.6", "feedbackScore": 4210},
    "itemLocation": {"country": "US"},
    "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": "USD"}}],
    "itemWebUrl": "https://www.ebay.com/itm/1234",
}


@pytest.fixture
def fake_ebay(monkeypatch):
    """Route the tool's httpx calls to a handler that records them."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 7200})
        if request.url.path == "/buy/browse/v1/item_summary/search":
            return httpx.Response(200, json={"total": 812, "itemSummaries": [_LISTING]})
        return httpx.Response(404)

    monkeypatch.setattr(ebay.httpx, "AsyncClient", lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("EBAY_CLIENT_ID", "app-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "cert-id")
    monkeypatch.setattr(ebay, "_token", None)
    return seen


async def test_a_search_asks_the_browse_api_and_reads_back_the_listing(fake_ebay):
    out = await ebay_search(query="sony xm5", max_price=200, condition="used", sort="price", limit=5)

    token, search = fake_ebay
    assert token.headers["Authorization"] == "Basic " + base64.b64encode(b"app-id:cert-id").decode()
    assert b"grant_type=client_credentials" in token.content
    assert search.headers["Authorization"] == "Bearer app-token"
    assert search.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
    params = search.url.params
    assert params["q"] == "sony xm5" and params["limit"] == "5" and params["sort"] == "price"
    assert params["filter"] == "price:[..200],priceCurrency:USD,conditions:{USED}"

    assert "1 of 812 listings" in out
    assert "Sony WH-1000XM5 Headphones" in out
    assert "189.99 USD (free shipping) · Used" in out
    assert "audio_shop (99.6% of 4210)" in out
    assert "https://www.ebay.com/itm/1234" in out


async def test_the_application_token_is_reused(fake_ebay):
    await ebay_search(query="a")
    await ebay_search(query="b")
    assert [r.url.path for r in fake_ebay].count("/identity/v1/oauth2/token") == 1


async def test_without_a_keyset_it_says_how_to_get_one(monkeypatch):
    monkeypatch.setattr(ebay, "ebay_credentials", lambda: None)
    out = await ebay_search(query="x")
    assert out.startswith("Error: no eBay keyset") and "developer.ebay.com" in out


async def test_a_refused_keyset_is_an_error_not_a_crash(fake_ebay, monkeypatch):
    refuse = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "invalid_client"}))
    monkeypatch.setattr(ebay.httpx, "AsyncClient", lambda **kw: _REAL_CLIENT(transport=refuse, **kw))
    out = await ebay_search(query="x")
    assert out.startswith("Error: eBay refused the keyset (401)")


async def test_an_unknown_sort_is_refused(fake_ebay):
    assert (await ebay_search(query="x", sort="cheapest")).startswith("Error: sort must be one of")


def test_offered_only_with_a_keyset_and_never_asks(monkeypatch):
    names = lambda: {t.__name__ for t in build_solocoder_tools(computer_use=False)}  # noqa: E731
    monkeypatch.setattr(ebay, "ebay_credentials", lambda: None)
    assert "ebay_search" not in names()
    monkeypatch.setattr(ebay, "ebay_credentials", lambda: ("id", "secret"))
    assert "ebay_search" in names()
    assert tool_risk("ebay_search") not in ASK_BY_DEFAULT, "reading public listings, like web_search"

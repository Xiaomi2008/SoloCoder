"""eBay listings through the official Browse API.

Structured results (price, condition, shipping, seller) for searching and
comparing, without the website's bot protection: the agent's browser on
ebay.com is an automated Chromium, and Akamai blocks it some of the time.

Needs a free eBay developer keyset (https://developer.ebay.com/my/keys): the
App ID and Cert ID, as ``EBAY_CLIENT_ID`` / ``EBAY_CLIENT_SECRET`` or saved with
``openagent config save-key ebay_id <App ID>`` and ``... ebay_secret <Cert ID>``.
``EBAY_ENV=sandbox`` uses eBay's sandbox. The tool is offered to the model only
once a keyset is configured.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import httpx

from ..core.tool import tool

_HOSTS = {"production": "https://api.ebay.com", "sandbox": "https://api.sandbox.ebay.com"}
_SCOPE = "https://api.ebay.com/oauth/api_scope"
_SORTS = {"best": None, "price": "price", "-price": "-price", "newest": "newlyListed"}
_CONDITIONS = {"new": "NEW", "used": "USED", "refurbished": "CERTIFIED_REFURBISHED|SELLER_REFURBISHED"}

# (token, expires_at, keyset) — an application token lasts two hours.
_token: tuple[str, float, tuple[str, str]] | None = None


def ebay_credentials() -> tuple[str, str] | None:
    """The App ID and Cert ID, or None when no keyset is configured."""
    from ..core.config import SecretManager

    client_id = os.environ.get("EBAY_CLIENT_ID") or SecretManager.get_api_key("ebay_id")
    secret = os.environ.get("EBAY_CLIENT_SECRET") or SecretManager.get_api_key("ebay_secret")
    return (client_id, secret) if client_id and secret else None


def _host() -> str:
    return _HOSTS.get(os.environ.get("EBAY_ENV", "production").lower(), _HOSTS["production"])


async def _application_token(client: httpx.AsyncClient, keyset: tuple[str, str]) -> str:
    """Client-credentials OAuth: the app's own token, no user sign-in."""
    global _token
    if _token is not None and _token[2] == keyset and time.time() < _token[1] - 60:
        return _token[0]
    basic = base64.b64encode(f"{keyset[0]}:{keyset[1]}".encode()).decode()
    response = await client.post(
        f"{_host()}/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": _SCOPE},
    )
    if response.status_code != 200:
        raise RuntimeError(f"eBay refused the keyset ({response.status_code}): {response.text[:200]}")
    payload = response.json()
    _token = (payload["access_token"], time.time() + float(payload.get("expires_in", 7200)), keyset)
    return _token[0]


def _filters(min_price: float | None, max_price: float | None, condition: str, currency: str) -> str:
    parts = []
    if min_price is not None or max_price is not None:
        low = "" if min_price is None else f"{min_price:g}"
        high = "" if max_price is None else f"{max_price:g}"
        parts += [f"price:[{low}..{high}]", f"priceCurrency:{currency}"]
    if condition in _CONDITIONS:
        parts.append(f"conditions:{{{_CONDITIONS[condition]}}}")
    return ",".join(parts)


def _money(amount: dict[str, Any] | None) -> str:
    if not amount:
        return "?"
    return f"{amount.get('value', '?')} {amount.get('currency', '')}".strip()


def _describe(index: int, item: dict[str, Any]) -> str:
    shipping = (item.get("shippingOptions") or [{}])[0].get("shippingCost")
    seller = item.get("seller") or {}
    location = item.get("itemLocation") or {}
    lines = [
        f"{index}. {item.get('title', '(untitled)')}",
        f"   {_money(item.get('price'))}"
        + (f" + {_money(shipping)} shipping" if shipping and shipping.get("value") not in (None, "0.00") else "")
        + (" (free shipping)" if shipping and shipping.get("value") == "0.00" else "")
        + f" · {item.get('condition', 'condition unknown')}"
        + f" · {', '.join(item.get('buyingOptions') or [])}",
        f"   seller {seller.get('username', '?')} ({seller.get('feedbackPercentage', '?')}% of "
        f"{seller.get('feedbackScore', '?')})"
        + (f" · ships from {location.get('country')}" if location.get("country") else ""),
        f"   {item.get('itemWebUrl', '')}",
    ]
    return "\n".join(lines)


@tool
async def ebay_search(
    query: str,
    max_price: float | None = None,
    min_price: float | None = None,
    condition: str = "any",
    sort: str = "best",
    limit: int = 10,
    marketplace: str = "EBAY_US",
) -> str:
    """Search eBay listings through eBay's official API: price, condition, shipping, seller, link.

    Prefer this to browsing ebay.com, which often blocks automated browsers.

    Args:
        query: What to search for.
        max_price: Highest item price, in the marketplace's currency.
        min_price: Lowest item price.
        condition: any, new, used, or refurbished.
        sort: best (eBay's relevance), price (lowest first), -price, or newest.
        limit: Results to return, 1-50.
        marketplace: eBay site, e.g. EBAY_US, EBAY_GB, EBAY_DE.
    """
    keyset = ebay_credentials()
    if keyset is None:
        return (
            "Error: no eBay keyset. Get one free at https://developer.ebay.com/my/keys and save it with "
            "`openagent config save-key ebay_id <App ID>` and `... ebay_secret <Cert ID>`."
        )
    if sort not in _SORTS:
        return f"Error: sort must be one of {', '.join(_SORTS)}"
    currency = {"EBAY_GB": "GBP", "EBAY_DE": "EUR", "EBAY_FR": "EUR", "EBAY_IT": "EUR", "EBAY_ES": "EUR",
                "EBAY_AU": "AUD", "EBAY_CA": "CAD"}.get(marketplace.upper(), "USD")
    params: dict[str, Any] = {"q": query, "limit": max(1, min(int(limit), 50))}
    if filters := _filters(min_price, max_price, condition.lower(), currency):
        params["filter"] = filters
    if _SORTS[sort]:
        params["sort"] = _SORTS[sort]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token = await _application_token(client, keyset)
            response = await client.get(
                f"{_host()}/buy/browse/v1/item_summary/search",
                params=params,
                headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace.upper()},
            )
    except (httpx.HTTPError, RuntimeError) as exc:
        return f"Error: {exc}"
    if response.status_code != 200:
        return f"Error: eBay search failed ({response.status_code}): {response.text[:300]}"

    payload = response.json()
    items = payload.get("itemSummaries") or []
    if not items:
        return f"No eBay listings for '{query}'" + (f" ({params['filter']})" if "filter" in params else "")
    header = f"eBay {marketplace.upper()}: {len(items)} of {payload.get('total', len(items))} listings for '{query}'"
    return "\n\n".join([header, *(_describe(i, item) for i, item in enumerate(items, 1))])

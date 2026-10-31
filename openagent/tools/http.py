"""``http_request``: call an HTTP API and read the raw response.

``web_fetch`` reads pages as text; this is for APIs: any method, headers, a
request body, and the status code and headers in the answer. The same URL
policy as the agent's browser applies (public sites and localhost, not the
private network). Methods that change something (POST, PUT, PATCH, DELETE)
are judged as ``exec`` by the permission gate; GET and HEAD as ``network``.
"""

from __future__ import annotations

import httpx

from ..core.tool import tool
from .browser import check_url

METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
READ_METHODS = frozenset({"GET", "HEAD"})
MAX_BODY_CHARS = 10_000
#: Response headers worth the tokens; the rest is noise to the model.
_SHOWN_HEADERS = ("content-type", "content-length", "location", "retry-after", "www-authenticate")


@tool
async def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Send an HTTP request to an API and return the status, key headers and body.

    Args:
        url: http:// or https:// URL
        method: GET, HEAD, POST, PUT, PATCH or DELETE
        headers: Request headers, e.g. {"Content-Type": "application/json"}
        body: Request body as text (JSON goes here as a string)
        timeout: Seconds to wait for the response

    Returns:
        Status line, selected headers, then the body (cut at 10,000 characters)
    """
    method = method.upper()
    if method not in METHODS:
        return f"Error: unsupported method '{method}'; use one of {', '.join(METHODS)}."
    refused = check_url(url)
    if refused:
        return f"Error: {refused}."
    if body is not None and method in READ_METHODS:
        return f"Error: a {method} request has no body."

    try:
        # Redirects are not followed: a public URL could redirect into the
        # private network, past the check above.
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.request(method, url, headers=headers, content=body)
    except httpx.TimeoutException:
        return f"Error: no response from {url} within {timeout:g}s."
    except httpx.HTTPError as e:
        return f"Error: request to {url} failed: {e}"

    shown = [f"{name}: {response.headers[name]}" for name in _SHOWN_HEADERS if name in response.headers]
    text = response.text
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + f"\n\n[body cut at {MAX_BODY_CHARS:,} of {len(text):,} characters]"
    parts = [f"HTTP {response.status_code} {response.reason_phrase}", *shown]
    if text:
        parts += ["", text]
    return "\n".join(parts)


__all__ = ["http_request"]

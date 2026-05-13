"""Tests for the http_request tool and SSRF protection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openagent.tools.builtin import (
    http_request,
    _parse_url,
    _is_private_ip,
    SecurityError,
)


# ============================================================================
# URL parsing
# ============================================================================


class TestParseUrl:
    def test_http_url(self):
        assert _parse_url("http://example.com") == "http://example.com"

    def test_https_url(self):
        assert _parse_url("https://example.com/api") == "https://example.com/api"

    def testftp_blocked(self):
        assert _parse_url("ftp://example.com") is None

    def test_file_blocked(self):
        assert _parse_url("file:///etc/passwd") is None

    def test_no_scheme_blocked(self):
        assert _parse_url("example.com") is None

    def test_empty_url(self):
        assert _parse_url("") is None


# ============================================================================
# SSRF protection
# ============================================================================


class TestIsPrivateIp:
    def test_public_ip(self):
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("1.1.1.1") is False
        assert _is_private_ip("203.0.113.5") is False

    def test_private_10(self):
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("10.255.255.255") is True

    def test_private_172(self):
        assert _is_private_ip("172.16.0.1") is True
        assert _is_private_ip("172.31.255.255") is True
        assert _is_private_ip("172.15.0.1") is False
        assert _is_private_ip("172.32.0.1") is False

    def test_private_192(self):
        assert _is_private_ip("192.168.0.1") is True
        assert _is_private_ip("192.168.255.255") is True

    def test_loopback(self):
        assert _is_private_ip("127.0.0.1") is True
        assert _is_private_ip("127.255.255.255") is True

    def test_link_local(self):
        assert _is_private_ip("169.254.169.254") is True

    def test_zero_multicast_reserved(self):
        assert _is_private_ip("0.0.0.0") is True
        assert _is_private_ip("224.0.0.1") is True
        assert _is_private_ip("255.255.255.255") is True

    def test_invalid_ip_blocked(self):
        assert _is_private_ip("999.999.999.999") is True
        assert _is_private_ip("not.an.ip") is True
        assert _is_private_ip("1.2.3") is True


# ============================================================================
# http_request tool
# ============================================================================


class TestHttpRequest:
    def test_unsupported_method(self):
        result = http_request("http://example.com", method="INVALID")
        assert "Unsupported method" in result

    def test_invalid_scheme(self):
        result = http_request("ftp://example.com")
        assert "http:// or https://" in result

    def test_ssrf_localhost_blocked(self):
        result = http_request("http://localhost:8080")
        assert "blocked" in result.lower() or "Error" in result

    def test_ssrf_private_ip_blocked(self):
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (2, 1, 6, "", ("10.0.0.1", 80)),
            ]
            result = http_request("http://internal-service.local")
            assert "blocked" in result.lower() or "internal" in result.lower() or "Error" in result

    def test_get_request(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.text = '{"hello": "world"}'
        mock_response.headers = MagicMock()
        mock_response.headers.items.return_value = [("content-type", "application/json")]

        with patch("httpx.request", return_value=mock_response):
            result = http_request("https://api.example.com/data", method="GET")

        assert "Status: 200 OK" in result
        assert '{"hello": "world"}' in result

    def test_post_request(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.reason_phrase = "Created"
        mock_response.text = '{"id": 1}'
        mock_response.headers = MagicMock()
        mock_response.headers.items.return_value = []

        with patch("httpx.request", return_value=mock_response) as mock_req:
            result = http_request(
                "https://api.example.com/items",
                method="POST",
                body='{"name": "test"}',
            )

        assert "Status: 201" in result
        mock_req.assert_called_once()

    def test_timeout(self):
        import httpx

        with patch("httpx.request", side_effect=httpx.TimeoutException("timeout")):
            result = http_request("https://slow.example.com", timeout=5.0)

        assert "timed out" in result.lower()

    def test_connect_error(self):
        import httpx

        with patch("httpx.request", side_effect=httpx.ConnectError("no route")):
            result = http_request("https://nonexistent.example.com")

        assert "Could not connect" in result

    def test_large_response_truncated(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason_phrase = "OK"
        mock_response.text = "x" * 20000
        mock_response.headers = MagicMock()
        mock_response.headers.items.return_value = []

        with patch("httpx.request", return_value=mock_response):
            result = http_request("https://example.com/big")

        assert "truncated" in result.lower()

    def test_valid_methods(self):
        for method in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.reason_phrase = "OK"
            mock_response.text = "ok"
            mock_response.headers = MagicMock()
            mock_response.headers.items.return_value = []

            with patch("httpx.request", return_value=mock_response):
                result = http_request("https://example.com", method=method)

            assert "Status: 200" in result, f"Method {method} should work"

"""Cloudflare-resistant sync HTTP client for the ChatGPT Codex backend.

``chatgpt.com/backend-api/codex`` sits behind Cloudflare bot-management. Hermes
already pins ``originator`` / ``User-Agent`` / ``ChatGPT-Account-ID`` headers
(see :func:`agent.auxiliary_client._codex_cloudflare_headers`) to clear the
*application-layer* originator whitelist. But from a flagged datacenter IP
(Railway, most VPS hosts, etc.) Cloudflare escalates to a *TLS-fingerprint*
(JA3/JA4) challenge that plain ``httpx`` fails — the response comes back as
Cloudflare challenge HTML, which the Codex stream parser cannot read (it
surfaces as ``'NoneType' object is not iterable`` once ``response.output`` is
``None``).

This module provides an ``httpx.Client`` whose transport performs the request
through ``curl_cffi`` with a Chrome TLS impersonation profile, so the handshake
looks like a real browser to Cloudflare while the HTTP headers (originator,
codex User-Agent, account id, bearer token) are forwarded unchanged.

Crucially the returned object is a genuine ``httpx.Client`` (built via
``httpx.Client(transport=...)``), so the *synchronous* ``OpenAI`` client accepts
it. Passing an ``httpx.AsyncClient`` here is what produces
``Invalid http_client argument; Expected an instance of httpx.Client`` — the bug
this module exists to avoid.

Scope is intentionally narrow: only the Codex client is routed here (gated on
the base URL in ``run_agent._build_keepalive_http_client``). Every other provider
keeps stock ``httpx``. If ``curl_cffi`` is unavailable or the impersonation
profile is rejected, the factory returns ``None`` and callers fall back to a
stock keepalive client — a missing optional dependency can never harden into a
hard failure.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterator, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Request headers libcurl/curl_cffi sets itself (or that describe a transfer
# framing it manages). Forwarding them causes Host mismatches, wrong
# Content-Length, or a stale Accept-Encoding that fights libcurl's automatic
# decompression.
_REQUEST_DROP_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "accept-encoding"}
)
# Response headers describing a transfer/content encoding that curl_cffi has
# ALREADY undone (libcurl auto-decompresses when impersonating). Passing them to
# httpx would trigger a second, failing decompression of already-plain bytes.
_RESPONSE_DROP_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)

# Chrome alias resolves to a recent Chrome profile in curl_cffi >= 0.7.
_DEFAULT_IMPERSONATE = (os.environ.get("CODEX_CURL_IMPERSONATE", "").strip() or "chrome")


def curl_cffi_available() -> bool:
    """True if ``curl_cffi`` can be imported in this environment."""
    try:
        import curl_cffi  # noqa: F401

        return True
    except Exception:
        return False


class _CurlByteStream(httpx.SyncByteStream):
    """Bridges curl_cffi's streamed response body into httpx's byte-stream API.

    Owns the lifetime of both the curl_cffi response and its session so that
    iterating to completion (or an explicit ``close()``) releases the handle.
    """

    def __init__(self, curl_response: Any, session: Any) -> None:
        self._resp = curl_response
        self._session = session

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._resp.iter_content():
                if chunk:
                    yield chunk
        finally:
            self.close()

    def close(self) -> None:
        for obj in (self._resp, self._session):
            try:
                closer = getattr(obj, "close", None)
                if closer is not None:
                    closer()
            except Exception:
                pass


class CurlCffiTransport(httpx.BaseTransport):
    """Sync ``httpx`` transport that routes every request through curl_cffi.

    A fresh curl_cffi session is created per request: curl_cffi sessions are not
    intended for concurrent cross-thread reuse, and Hermes drives synchronous
    OpenAI clients from asyncio executor threads. Per-request handles trade a
    little connection reuse for thread-safety, which matters more here.
    """

    def __init__(
        self,
        impersonate: str,
        proxy: Optional[str] = None,
        connect_timeout: float = 30.0,
        read_timeout: float = 600.0,
    ) -> None:
        self._impersonate = impersonate
        self._proxy = proxy
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        from curl_cffi import requests as cffi_requests

        body = request.read()  # Codex request bodies are JSON, safe to buffer.
        req_headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _REQUEST_DROP_HEADERS
        }

        session = cffi_requests.Session()
        call_kwargs: dict = {
            "method": request.method,
            "url": str(request.url),
            "headers": req_headers,
            "data": body or None,
            "stream": True,
            "impersonate": self._impersonate,
            "timeout": (self._connect_timeout, self._read_timeout),
            "allow_redirects": False,
        }
        if self._proxy:
            call_kwargs["proxies"] = {"http": self._proxy, "https": self._proxy}

        try:
            curl_resp = session.request(**call_kwargs)
        except TypeError:
            # Older curl_cffi releases differ on the timeout/proxy signatures.
            call_kwargs["timeout"] = self._read_timeout
            if "proxies" in call_kwargs:
                call_kwargs.pop("proxies", None)
                call_kwargs["proxy"] = self._proxy
            curl_resp = session.request(**call_kwargs)
        except Exception:
            session.close()
            raise

        # Debug-level visibility into what the endpoint returned — lets us tell a
        # real Codex SSE stream apart from a Cloudflare challenge (text/html +
        # cf-mitigated) or an API error (json) when diagnosing. Silent unless
        # debug logging is enabled.
        if logger.isEnabledFor(logging.DEBUG):
            try:
                _h = curl_resp.headers
                logger.debug(
                    "curl_cffi codex %s %s -> status=%s content-type=%s "
                    "cf-mitigated=%s cf-ray=%s server=%s impersonate=%s",
                    request.method, str(request.url), curl_resp.status_code,
                    _h.get("content-type"), _h.get("cf-mitigated"),
                    _h.get("cf-ray"), _h.get("server"), self._impersonate,
                )
            except Exception:
                pass

        resp_headers: List[Tuple[str, str]] = [
            (key, value)
            for key, value in curl_resp.headers.items()
            if key.lower() not in _RESPONSE_DROP_HEADERS
        ]
        return httpx.Response(
            status_code=curl_resp.status_code,
            headers=resp_headers,
            stream=_CurlByteStream(curl_resp, session),
            request=request,
        )


def build_codex_curl_client(
    base_url: str = "",
    impersonate: Optional[str] = None,
    proxy: Optional[str] = None,
) -> Optional[httpx.Client]:
    """Return an ``httpx.Client`` backed by curl_cffi Chrome TLS, or ``None``.

    ``None`` signals the caller to fall back to a stock keepalive client (e.g.
    when ``curl_cffi`` is not installed). The returned client is a real
    ``httpx.Client`` instance, so the synchronous ``OpenAI`` client accepts it.
    """
    if not curl_cffi_available():
        return None
    try:
        transport = CurlCffiTransport(
            impersonate=(impersonate or _DEFAULT_IMPERSONATE),
            proxy=proxy,
        )
        return httpx.Client(transport=transport)
    except Exception:
        return None

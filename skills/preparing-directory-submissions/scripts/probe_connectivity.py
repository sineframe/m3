#!/usr/bin/env python3
"""Check that a remote MCP endpoint is reachable the way directories need.

Plain HTTP only: one unauthenticated MCP `initialize` POST (repeated with the
Origin header of each directory's web app, https://claude.ai and
https://chatgpt.com; the directories don't publish the Origin they send), a
DELETE that closes any session an initialize opened, GETs for the OAuth
discovery documents the server points to, and for OpenAI a GET of the domain
challenge path. It never sends credentials, never signs in and never calls a
tool. It prints status codes and header names, never response bodies beyond
what the checks need.

Usage: probe_connectivity.py URL [--directory claude|openai ...] [--timeout S]
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

ORIGINS = {"claude": "https://claude.ai", "openai": "https://chatgpt.com"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "m3-directory-readiness-probe", "version": "1"},
    },
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _request(url, method="GET", body=None, headers=None, timeout=10.0):
    """(status, headers, body bytes) or (None, None, error text)."""
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("User-Agent", "m3-directory-readiness-probe")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.headers, response.read(65536)
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read(65536)
    except (urllib.error.URLError, OSError, ssl.SSLError) as error:
        reason = getattr(error, "reason", error)
        return None, None, f"{type(error).__name__}: {reason}"


def _initialize(url, origin, timeout):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }
    if origin:
        headers["Origin"] = origin
    status, response_headers, body = _request(
        url, "POST", json.dumps(INITIALIZE).encode(), headers, timeout
    )
    session = response_headers.get("Mcp-Session-Id") if response_headers else None
    if session:
        # Close the session the probe opened.
        _request(url, "DELETE", headers={"Mcp-Session-Id": session}, timeout=timeout)
    return status, response_headers, body


def _report(check, status, detail):
    print(f"{status.upper():8} {check}: {detail}")


def _public_host(host):
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        return False, f"{host} does not resolve ({error.strerror})"
    addresses = sorted({info[4][0] for info in infos})
    private = [a for a in addresses if not ipaddress.ip_address(a).is_global]
    if private:
        return False, f"{host} resolves to non-public address(es) {', '.join(private)}"
    return True, f"{host} resolves to public address(es) {', '.join(addresses)}"


def _resource_metadata(www_authenticate):
    match = re.search(r'resource_metadata="?([^",\s]+)"?', www_authenticate or "")
    return match.group(1) if match else None


def _json(body):
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


def _is_initialize_result(body):
    """True when the body is a JSON-RPC initialize result, as JSON or SSE."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else ""
    candidates = [text] + [
        line[5:].strip() for line in text.splitlines() if line.startswith("data:")
    ]
    for candidate in candidates:
        message = _json(candidate)
        if isinstance(message, dict) and isinstance(message.get("result"), dict):
            return "protocolVersion" in message["result"]
    return False


def _check_oauth(url, www_authenticate, timeout):
    pointer = _resource_metadata(www_authenticate)
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    candidates = [pointer] if pointer else []
    path = parts.path.rstrip("/")
    candidates += [
        f"{origin}/.well-known/oauth-protected-resource{path}",
        f"{origin}/.well-known/oauth-protected-resource",
    ]
    if pointer:
        _report(
            "401 metadata pointer",
            "pass",
            f"WWW-Authenticate names resource_metadata {pointer}",
        )
    else:
        _report(
            "401 metadata pointer",
            "warning",
            "WWW-Authenticate has no resource_metadata; Claude falls back to the well-known paths",
        )
    for candidate in candidates:
        status, _, body = _request(candidate, timeout=timeout)
        document = _json(body) if status == 200 else None
        servers = (
            document.get("authorization_servers")
            if isinstance(document, dict)
            else None
        )
        if servers is not None and not (
            isinstance(servers, list) and servers and isinstance(servers[0], str)
        ):
            _report(
                "protected resource metadata",
                "fail",
                f"{candidate}: authorization_servers must be a non-empty array of URLs (RFC 9728)",
            )
            return
        if servers:
            server = servers[0]
            _report(
                "protected resource metadata",
                "pass",
                f"{candidate} lists authorization server {server}",
            )
            _check_authorization_server(server, timeout)
            return
        if candidate == pointer:
            # Clients follow the pointer first; a working fallback below
            # doesn't make a broken pointer right.
            _report(
                "resource_metadata pointer",
                "warning",
                f"{candidate} -> {status or body}: the pointer doesn't serve the metadata",
            )
            continue
        _report(
            "protected resource metadata", "info", f"{candidate} -> {status or body}"
        )
    _report(
        "protected resource metadata",
        "fail",
        "no reachable document with authorization_servers; sign-in can't start",
    )


def _check_authorization_server(server, timeout):
    parts = urlsplit(server)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    for candidate in (
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}/.well-known/openid-configuration{path}",
        urljoin(server.rstrip("/") + "/", ".well-known/openid-configuration"),
    ):
        status, _, body = _request(candidate, timeout=timeout)
        document = _json(body) if status == 200 else None
        if isinstance(document, dict) and document.get("authorization_endpoint"):
            if document.get("token_endpoint"):
                _report(
                    "authorization server metadata",
                    "pass",
                    f"{candidate} lists authorization and token endpoints",
                )
            else:
                _report(
                    "authorization server metadata",
                    "fail",
                    f"{candidate} lists no token_endpoint",
                )
            return
    _report(
        "authorization server metadata",
        "fail",
        f"no RFC 8414 or OpenID discovery document found for {server}",
    )


def _check_challenge(url, timeout):
    host = urlsplit(url).hostname
    challenge = f"https://{host}/.well-known/openai-apps-challenge"
    status, headers, body = _request(challenge, timeout=timeout)
    if status != 200:
        _report(
            "openai domain challenge",
            "warning",
            f"{challenge} -> {status or body}; serve the portal's token here (or on an eligible parent domain)",
        )
        return
    text = body.decode("utf-8", "replace").strip() if isinstance(body, bytes) else ""
    content_type = headers.get("Content-Type", "") if headers else ""
    if (
        not text
        or "\n" in text
        or text.startswith(("{", "[", "<"))
        or "html" in content_type.lower()
    ):
        _report(
            "openai domain challenge",
            "fail",
            f"{challenge} -> 200 but the body is not a single plain-text token ({content_type})",
        )
    else:
        _report(
            "openai domain challenge",
            "pass",
            f"{challenge} -> 200, one plain-text token of {len(text)} characters ({content_type})",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument(
        "--directory", action="append", choices=sorted(ORIGINS), default=[]
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    directories = args.directory or sorted(ORIGINS)
    parts = urlsplit(args.url)

    if parts.scheme != "https" or not parts.hostname:
        _report("public https", "fail", f"{args.url} is not an https URL")
        return 1
    public, detail = _public_host(parts.hostname)
    _report("public https", "pass" if public else "fail", detail)
    if not public:
        return 1

    status, headers, body = _initialize(args.url, None, args.timeout)
    if status is None:
        _report("unauthenticated initialize", "fail", f"no HTTP response: {body}")
        return 1
    www_authenticate = headers.get("WWW-Authenticate") if headers else None
    if status == 401 and www_authenticate:
        _report(
            "unauthenticated initialize",
            "pass",
            "401 Unauthorized with WWW-Authenticate",
        )
        _check_oauth(args.url, www_authenticate, args.timeout)
    elif status == 401:
        _report(
            "unauthenticated initialize",
            "fail",
            "401 Unauthorized without WWW-Authenticate; Claude needs the header to find the sign-in metadata",
        )
        _check_oauth(args.url, None, args.timeout)
    elif status == 200 and _is_initialize_result(body):
        _report(
            "unauthenticated initialize",
            "pass",
            "200 with an initialize result: authless, or lazy authentication that "
            "gates only some tool calls (read the code to tell)",
        )
    elif status == 200:
        _report(
            "unauthenticated initialize",
            "fail",
            "200 but the body is not an MCP initialize result; is this the MCP endpoint?",
        )
    elif status in (301, 302, 303, 307, 308):
        location = headers.get("Location", "") if headers else ""
        _report(
            "unauthenticated initialize",
            "warning",
            f"{status} redirect to {location}; submit the final URL, and if this is a "
            "sign-in page, Claude can't start sign-in (it needs a 401)",
        )
    elif status == 400:
        _report(
            "unauthenticated initialize",
            "warning",
            "400 Bad Request: the server may speak only MCP 2026-07-28, which drops "
            "the initialize handshake this probe sends; check its protocol version",
        )
    else:
        _report(
            "unauthenticated initialize",
            "fail",
            f"HTTP {status}; expected 401 (sign-in) or 200 (authless or lazy auth)",
        )

    for directory in directories:
        origin = ORIGINS[directory]
        if status not in (200, 401):
            _report(
                f"origin {origin}",
                "not checked",
                f"the request without Origin got {status}, so there is nothing to compare",
            )
            continue
        origin_status, _, origin_body = _initialize(args.url, origin, args.timeout)
        if origin_status == status:
            _report(
                f"origin {origin}",
                "pass",
                f"same response as without Origin ({origin_status})",
            )
        elif origin_status == 403:
            _report(
                f"origin {origin}",
                "warning",
                "403 only when this Origin is sent; the server's Origin allowlist refuses it",
            )
        else:
            _report(
                f"origin {origin}",
                "warning",
                f"{origin_status or origin_body} with this Origin, {status} without",
            )

    if "openai" in directories:
        _check_challenge(args.url, args.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

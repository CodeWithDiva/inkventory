"""Fetching user-supplied URLs safely (blocks requests to internal networks - SSRF protection)."""

import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx

MAX_BYTES = 25 * 1024 * 1024


class UnsafeURLError(ValueError):
    pass


def check_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURLError("Only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        raise UnsafeURLError("The host name could not be resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURLError("URLs pointing to private or local networks are not allowed")


def google_sheets_csv_url(url: str) -> str:
    """Turn a Google Sheets share/edit link into its CSV export link (other URLs are returned unchanged)."""
    match = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]+)", url)
    if not match:
        return url
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")


def fetch(url: str, *, transport: httpx.BaseTransport | None = None) -> bytes:
    check_public_url(url)
    with httpx.Client(timeout=30, follow_redirects=False, transport=transport) as client:
        for _ in range(5):  # follow redirects manually so every hop is checked
            response = client.get(url)
            if response.is_redirect:
                url = str(response.next_request.url) if response.next_request else ""
                check_public_url(url)
                continue
            if response.status_code >= 400:
                raise ValueError(f"The URL returned HTTP {response.status_code}. Is the sheet shared publicly?")
            if len(response.content) > MAX_BYTES:
                raise ValueError("The file is larger than 25 MB")
            return response.content
    raise ValueError("Too many redirects")

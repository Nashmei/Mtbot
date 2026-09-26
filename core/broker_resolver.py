"""Resolve an MT5 server name to broker access points without exposing credentials."""

import json
import urllib.parse
import urllib.request

DEFAULT_RESOLVER = "https://mt5.mtapi.io"


def _keyword(server_name: str) -> str:
    return server_name.split("-", 1)[0].strip()


def _parse(body: str, server_name: str) -> list[str]:
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    target = server_name.strip().lower()
    found = []
    for company in data:
        if not isinstance(company, dict):
            continue
        for row in company.get("results") or []:
            if str(row.get("name", "")).strip().lower() != target:
                continue
            for endpoint in row.get("access") or []:
                endpoint = str(endpoint).strip()
                if endpoint and endpoint not in found:
                    found.append(endpoint)
    return found


def resolve_server(server_name: str, timeout: float = 8.0) -> list[str]:
    """Return exact-match host:port endpoints. Credentials are never transmitted."""
    name = str(server_name or "").strip()
    if not name:
        return []
    query = urllib.parse.quote(_keyword(name))
    url = f"{DEFAULT_RESOLVER}/Search?company={query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return _parse(response.read().decode("utf-8", "replace"), name)
    except Exception:
        return []

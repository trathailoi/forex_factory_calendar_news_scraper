"""Server-side normalized market-data fetcher with a fallback chain.

Order: TwelveData (multi-key rotation) -> alternate free source per asset_class
(Frankfurter for forex, Finnhub for stock/index/commodity). All HTTP goes through
an injected ``http_get`` so tests never touch the network. Every source failure is
caught and treated as "this source is unavailable"; the orchestrator moves on or
returns ``None``.
"""
from __future__ import annotations

import os
from datetime import datetime

import requests

TD_TIME_SERIES_URL = "https://api.twelvedata.com/time_series"
FRANKFURTER_URL = "https://api.frankfurter.app/latest"
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"

HTTP_TIMEOUT = 15

# SPY/QQQ/USO are liquid ETF proxies for the underlying index/commodity, since
# Finnhub's free /quote endpoint does not cover the raw index/futures symbols.
_FINNHUB_PROXY = {
    "SPX": "SPY",
    "NDX": "QQQ",
    "CL": "USO",  # WTI crude oil
}


def _td_keys() -> list[str]:
    """Return TwelveData API keys, preferring the comma-separated TD_API_KEYS."""
    raw = os.environ.get("TD_API_KEYS", "")
    if not raw.strip():
        raw = os.environ.get("TD_API_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def _parse_epoch(dt_str: str) -> int:
    """Parse a TwelveData datetime ("YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DD")."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(dt_str, fmt).timestamp())
        except (ValueError, TypeError):
            continue
    return 0


def _is_td_credit_failure(status_code: int, body) -> bool:
    """True if a TD response indicates a rate/credit problem (advance to next key)."""
    if status_code == 429:
        return True
    if not isinstance(body, dict):
        return False
    if body.get("code") == 429:
        return True
    if body.get("status") == "error":
        msg = str(body.get("message", "")).lower()
        if "credit" in msg or "limit" in msg:
            return True
    return False


def _fetch_twelvedata(td_symbol: str, http_get) -> dict | None:
    """Try each TD key in turn. Returns normalized dict or None if all fail."""
    for key in _td_keys():
        params = {
            "symbol": td_symbol,
            "interval": "5min",
            "outputsize": "300",
            "apikey": key,
        }
        try:
            resp = http_get(TD_TIME_SERIES_URL, params=params, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            continue  # network error on this key — try the next one
        try:
            body = resp.json()
        except ValueError:
            continue
        if _is_td_credit_failure(resp.status_code, body):
            continue
        values = body.get("values") if isinstance(body, dict) else None
        if not values:
            continue  # no usable history — try next key

        # TD returns newest-first; candles must be oldest-first.
        ordered = list(reversed(values))
        candles = [
            {
                "time": _parse_epoch(v["datetime"]),
                "value": float(v["close"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
            }
            for v in ordered
        ]
        newest_close = float(values[0]["close"])
        oldest_close = float(values[-1]["close"])
        change24h = (
            (newest_close - oldest_close) / oldest_close * 100 if oldest_close else 0.0
        )
        return {
            "price": newest_close,
            "change24h": change24h,
            "candles": candles,
            "source": "twelvedata",
        }
    return None


def _fetch_frankfurter(td_symbol: str, http_get) -> dict | None:
    """Keyless ECB forex source. Daily data only, so no intraday change/candles.

    We deliberately do NOT do fragile date math: return current price with
    change24h=0.0 and candles=[]. The client seeds a flat/last-known chart.
    """
    base, _, quote = td_symbol.partition("/")
    if not base or not quote:
        return None
    params = {"from": base, "to": quote}
    try:
        resp = http_get(FRANKFURTER_URL, params=params, timeout=HTTP_TIMEOUT)
        body = resp.json()
    except (requests.RequestException, ValueError):
        return None
    rates = body.get("rates") if isinstance(body, dict) else None
    if not rates or quote not in rates:
        return None
    return {
        "price": float(rates[quote]),
        "change24h": 0.0,
        "candles": [],
        "source": "frankfurter",
    }


def _fetch_finnhub(td_symbol: str, asset_class: str, http_get) -> dict | None:
    """Finnhub /quote for stocks; ETF proxies for index/commodity. No history."""
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        return None
    symbol = td_symbol.upper()
    if asset_class in ("index", "commodity"):
        symbol = _FINNHUB_PROXY.get(symbol, symbol)
    params = {"symbol": symbol, "token": key}
    try:
        resp = http_get(FINNHUB_QUOTE_URL, params=params, timeout=HTTP_TIMEOUT)
        body = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    current = body.get("c")
    prev_close = body.get("pc")
    if not current:  # 0 or missing => no quote
        return None
    change24h = (
        (current - prev_close) / prev_close * 100 if prev_close else 0.0
    )
    return {
        "price": float(current),
        "change24h": change24h,
        "candles": [],
        "source": "finnhub",
    }


def fetch_market(
    asset_id: str,
    td_symbol: str,
    asset_class: str,
    *,
    http_get=requests.get,
) -> dict | None:
    """Fetch normalized market data with the TwelveData -> free-source fallback chain."""
    td = _fetch_twelvedata(td_symbol, http_get)
    if td is not None:
        return td

    if asset_class == "forex":
        return _fetch_frankfurter(td_symbol, http_get)
    # stock / index / commodity all route through Finnhub (with proxies).
    return _fetch_finnhub(td_symbol, asset_class, http_get)

"""FastAPI server: serves the dashboard and proxies keyed market/news APIs."""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ff_calendar_toolkit.market_data import fetch_market
from ff_calendar_toolkit.news_api import load_dashboard_news
from ff_calendar_toolkit.runtime import load_env_file

load_env_file()

ROOT = Path(__file__).parent
TD_API_KEY = os.environ.get("TD_API_KEY", "")
CMC_API_KEY = os.environ.get("CMC_API_KEY", "")
NEWS_DIR = Path(os.environ.get("FF_OUTPUT_DIR", "news"))

TD_BASE = "https://api.twelvedata.com"
CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html", media_type="text/html")


@app.get("/api/news")
def api_news() -> JSONResponse:
    now_ms = int(time.time() * 1000)
    return JSONResponse(load_dashboard_news(NEWS_DIR, now_ms))


def _passthrough(resp: requests.Response) -> JSONResponse:
    try:
        body = resp.json()
    except ValueError:
        body = {"error": "upstream returned non-JSON"}
    return JSONResponse(body, status_code=resp.status_code)


# These proxies forward arbitrary client query params upstream with the server's
# API key attached. That is safe only because the app binds to 127.0.0.1 (see the
# __main__ block) — do NOT expose it on a public interface without an allowlist of
# param names and rate limiting, or the API quota becomes a free open relay.
@app.get("/api/td/quote")
def td_quote(request: Request) -> JSONResponse:
    params = dict(request.query_params)
    params["apikey"] = TD_API_KEY
    resp = requests.get(f"{TD_BASE}/quote", params=params, timeout=15)
    return _passthrough(resp)


@app.get("/api/td/time_series")
def td_time_series(request: Request) -> JSONResponse:
    params = dict(request.query_params)
    params["apikey"] = TD_API_KEY
    resp = requests.get(f"{TD_BASE}/time_series", params=params, timeout=15)
    return _passthrough(resp)


@app.get("/api/market/{asset_id}")
def api_market(asset_id: str, td_symbol: str, asset_class: str) -> JSONResponse:
    data = fetch_market(asset_id, td_symbol, asset_class)
    if data is None:
        # Client treats a null source as "simulate + tag SIM".
        return JSONResponse({"source": None}, status_code=200)
    return JSONResponse(data)


@app.get("/api/cmc/quotes")
def cmc_quotes(request: Request) -> JSONResponse:
    params = dict(request.query_params)
    params.setdefault("convert", "USD")
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    resp = requests.get(CMC_QUOTES_URL, params=params, headers=headers, timeout=15)
    return _passthrough(resp)


if __name__ == "__main__":
    import uvicorn

    # Defaults to 127.0.0.1 for local dev (keeps the keyed proxies off the network).
    # In Docker, set DASHBOARD_HOST=0.0.0.0 so the published port is reachable — there
    # the safety boundary is the published-port/network config, not the bind address.
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)

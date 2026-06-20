"""Build dashboard-ready news from the scraper's last_run JSON output."""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HIGH_IMPACT = {"red", "orange"}


def _latest_last_run_file(news_dir: Path) -> Path | None:
    last_run = Path(news_dir) / "last_run"
    if not last_run.is_dir():
        return None
    files = glob.glob(str(last_run / "*.json"))
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def _scheduled_ms(date_str: str, time_str: str, tz_name: str) -> int | None:
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return int(dt.timestamp() * 1000)
    except (ValueError, KeyError):
        return None


def load_dashboard_news(news_dir, now_ms: int) -> list[dict]:
    """Return future high-impact events as dashboard records, sorted ascending.

    Each record: {title, currency, impact, source, cat, scheduledTime}.
    """
    path = _latest_last_run_file(Path(news_dir))
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for rec in raw:
        impact = str(rec.get("impact", "")).lower()
        if impact not in HIGH_IMPACT:
            continue
        sched = _scheduled_ms(rec.get("date", ""), rec.get("time", ""), rec.get("timezone", "UTC"))
        if sched is None or sched < now_ms:
            continue
        out.append({
            "title": rec.get("event", ""),
            "currency": rec.get("currency", ""),
            "impact": impact,
            "source": "ForexFactory",
            "cat": "FX",
            "scheduledTime": sched,
        })
    out.sort(key=lambda e: e["scheduledTime"])
    return out

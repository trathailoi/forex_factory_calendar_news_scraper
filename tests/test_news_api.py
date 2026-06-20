import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from ff_calendar_toolkit.news_api import load_dashboard_news

FIXTURE = Path(__file__).parent / "fixtures" / "dashboard_news.json"


def _make_news_dir(tmp_path):
    last_run = tmp_path / "last_run"
    last_run.mkdir()
    (last_run / "2026-05.json").write_text(FIXTURE.read_text(), encoding="utf-8")
    return tmp_path


def _ms(dt_str, tz):
    dt = datetime.strptime(dt_str, "%d/%m/%Y %H:%M").replace(tzinfo=ZoneInfo(tz))
    return int(dt.timestamp() * 1000)


def test_filters_out_gray_impact(tmp_path):
    news_dir = _make_news_dir(tmp_path)
    now_ms = _ms("20/05/2026 00:00", "Asia/Karachi")
    events = load_dashboard_news(news_dir, now_ms)
    titles = [e["title"] for e in events]
    assert "French Bank Holiday" not in titles
    assert "Federal Funds Rate" in titles
    assert "GDP q/q" in titles


def test_drops_past_events(tmp_path):
    news_dir = _make_news_dir(tmp_path)
    now_ms = _ms("20/05/2026 00:00", "Asia/Karachi")
    events = load_dashboard_news(news_dir, now_ms)
    titles = [e["title"] for e in events]
    assert "BOJ Policy Rate" not in titles


def test_builds_scheduled_time_ms_and_sorts(tmp_path):
    news_dir = _make_news_dir(tmp_path)
    now_ms = _ms("20/05/2026 00:00", "Asia/Karachi")
    events = load_dashboard_news(news_dir, now_ms)
    assert events[0]["title"] == "Federal Funds Rate"
    assert events[0]["scheduledTime"] == _ms("27/05/2026 12:30", "Asia/Karachi")
    assert events[0]["currency"] == "USD"
    assert events[0]["source"] == "ForexFactory"


def test_missing_dir_returns_empty(tmp_path):
    assert load_dashboard_news(tmp_path / "nope", 0) == []

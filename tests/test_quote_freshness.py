from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from etf_temperature import csv_cache
from etf_temperature.indicators import compute_quote_features
from etf_temperature.official_endpoints import OfficialClient
from etf_temperature.pipeline import _dedupe_quotes, _quote_refresh_gaps
from etf_temperature.quote_freshness import PRECLOSE_QUOTE_STATUS


def quote(code, captured, *, day="2026-11-05", priority=1, close=1.0):
    return {
        "etf_code": code, "date": day, "data_date": day, "retrieved_at": captured,
        "source_priority": priority, "close": close, "volume": 0, "amount": 0,
    }


def test_preclose_quote_is_a_gap_and_settled_zero_volume_is_available():
    day = "2026-11-05"
    quotes = pd.DataFrame([
        quote("589960", "2026-11-05T09:14:52"),
        quote("510300", "2026-11-05T07:00:00Z"),
        quote("510500", None),
    ])
    missing, _ = _quote_refresh_gaps(quotes, {day: ["589960", "510300", "510500"]}, day)
    assert missing == ["589960"]

    features = compute_quote_features(quotes, day).set_index("etf_code")
    assert pd.isna(features.at["589960", "close"])
    assert features.at["589960", "quote_validation_status"] == PRECLOSE_QUOTE_STATUS
    assert features.at["510300", "close"] == 1.0
    assert features.at["510300", "volume"] == 0
    assert features.at["510500", "close"] == 1.0


def test_settled_quote_wins_cache_upsert_over_preclose_source(tmp_path, monkeypatch):
    monkeypatch.setitem(csv_cache.CACHE_FILES, "quotes", tmp_path / "quotes.csv")
    early = pd.DataFrame([quote("589960", "2026-11-05T09:14:52", close=0.715)])
    final = pd.DataFrame([quote("589960", "2026-11-06T13:11:39", priority=2, close=0.760)])
    csv_cache.upsert_timeseries_cache("quotes", early)
    assert csv_cache.upsert_timeseries_cache("quotes", final).iloc[0]["close"] == 0.760
    assert csv_cache.upsert_timeseries_cache("quotes", early).iloc[0]["close"] == 0.760


def test_blank_close_does_not_replace_preclose_evidence(tmp_path, monkeypatch):
    day = "2026-11-05"
    monkeypatch.setitem(csv_cache.CACHE_FILES, "quotes", tmp_path / "quotes.csv")
    early = pd.DataFrame([quote("589960", f"{day}T09:14:52", close=0.715)])
    blank = pd.DataFrame([quote("589960", f"{day}T16:00:00", close=None)])
    csv_cache.upsert_timeseries_cache("quotes", early)
    merged = csv_cache.upsert_timeseries_cache("quotes", blank)
    assert merged.iloc[0]["close"] == 0.715
    window = pd.concat([early, blank], ignore_index=True)
    assert _dedupe_quotes(window).iloc[0]["close"] == 0.715
    missing, _ = _quote_refresh_gaps(window, {day: ["589960"]}, day)
    assert missing == ["589960"]
    assert compute_quote_features(merged, day).iloc[0]["quote_validation_status"] == PRECLOSE_QUOTE_STATUS


def test_quote_raw_cache_rejects_preclose_fallback_and_records_later_confirmation(tmp_path, monkeypatch):
    client = OfficialClient(raw_dir=tmp_path, retries=1)
    url = "https://example.com/quote"
    payload = '{"volume": 0}'
    cutoff = time.time() - 60
    early = client._save_raw(url, payload, "quote")
    os.utime(early, (cutoff - 1, cutoff - 1))

    def unavailable(*args, **kwargs):
        raise OSError("source unavailable")

    monkeypatch.setattr(client, "_get_text_with_playwright", unavailable)
    client.session = SimpleNamespace(get=unavailable, close=lambda: None)
    with pytest.raises(RuntimeError, match="GET failed"):
        client.get_json(url, raw_prefix="quote", cache_not_before=cutoff)

    response = SimpleNamespace(text=payload, raise_for_status=lambda: None)
    client.session = SimpleNamespace(get=lambda *args, **kwargs: response)
    assert client.get_json(url, raw_prefix="quote", cache_not_before=cutoff) == {"volume": 0}
    assert len(list(tmp_path.glob("*.txt"))) == 2
    client.session = SimpleNamespace(get=unavailable, close=lambda: None)
    assert client.get_json(url, raw_prefix="quote", cache_not_before=cutoff) == {"volume": 0}

from __future__ import annotations

from etf_temperature import sse_official


class _FakeClient:
    """Serve canned per-date responses: newest date only answers the latest query."""

    def __init__(self, available: dict[str, list[dict]]):
        self._available = available

    def get_json(self, url, referer=None, raw_prefix=None):
        if "STAT_DATE=" not in url:
            return {}
        raw_date = url.split("STAT_DATE=")[1].split("&")[0]
        # `quote('')` round-trips to an empty token for the unfiltered "latest" query.
        date = "" if raw_date in ("", "%20", "+") else raw_date
        rows = self._available.get(date or "latest", [])
        return {"result": rows}


def _row(code: str, date: str) -> dict:
    return {"SEC_CODE": code, "TOT_VOL": 1000.0, "STAT_DATE": date}


def test_missing_newest_date_is_topped_up_from_latest_query():
    client = _FakeClient(
        {
            "2026-10-08": [_row("510300", "2026-10-08"), _row("510500", "2026-10-08")],
            "latest": [_row("510300", "2026-10-09"), _row("510500", "2026-10-09")],
        }
    )
    frame = sse_official.fetch_share_history(client, ["2026-10-08", "2026-10-09"])
    assert sorted(frame["date"].astype(str).unique()) == ["2026-10-08", "2026-10-09"]
    assert len(frame) == 4


def test_topup_ignores_dates_outside_requested_window():
    client = _FakeClient({"latest": [_row("510300", "2026-09-01")]})
    frame = sse_official.fetch_share_history(client, ["2026-10-09"])
    assert frame.empty


def test_no_topup_when_all_dates_already_covered():
    calls: list[str] = []

    class _SpyClient(_FakeClient):
        def get_json(self, url, referer=None, raw_prefix=None):
            calls.append(url.split("STAT_DATE=")[1])
            return super().get_json(url, referer=referer, raw_prefix=raw_prefix)

    client = _SpyClient({"2026-10-09": [_row("510300", "2026-10-09")]})
    frame = sse_official.fetch_share_history(client, ["2026-10-09"])
    assert len(frame) == 1
    assert calls == ["2026-10-09"]

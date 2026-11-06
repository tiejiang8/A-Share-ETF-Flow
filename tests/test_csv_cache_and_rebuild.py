from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from etf_temperature import csv_cache
from etf_temperature.pipeline import PipelineResult, rebuild_history


def _row(code: str, date: str, close: float, priority: int, retrieved_at: str) -> dict:
    return {
        "etf_code": code,
        "date": date,
        "close": close,
        "source_priority": priority,
        "retrieved_at": retrieved_at,
        "source": "test",
    }


def test_csv_cache_upserts_by_date_and_etf_code(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "quotes.csv"
    monkeypatch.setitem(csv_cache.CACHE_FILES, "quotes", path)

    first = pd.DataFrame([_row("159915", "2026-07-01", 2.0, 2, "2026-07-02T08:00:00")])
    second = pd.DataFrame(
        [
            _row("159915", "2026-07-01", 2.1, 1, "2026-07-02T09:00:00"),
            _row("510050", "2026-07-01", 3.0, 1, "2026-07-02T09:00:00"),
        ]
    )

    csv_cache.upsert_timeseries_cache("quotes", first)
    result = csv_cache.upsert_timeseries_cache("quotes", second)

    assert len(result) == 2
    assert result.duplicated(["date", "etf_code"]).sum() == 0
    assert result.loc[result["etf_code"].eq("159915"), "close"].iloc[0] == 2.1
    reloaded = csv_cache.read_timeseries_cache("quotes")
    assert set(reloaded["etf_code"]) == {"159915", "510050"}


def test_csv_cache_prefers_newer_row_at_same_priority(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "shares.csv"
    monkeypatch.setitem(csv_cache.CACHE_FILES, "shares", path)

    old = pd.DataFrame([_row("510300", "2026-07-02", 1.0, 1, "2026-07-03T08:00:00")])
    new = pd.DataFrame([_row("510300", "2026-07-02", 2.0, 1, "2026-07-03T09:00:00")])
    csv_cache.upsert_timeseries_cache("shares", old)
    result = csv_cache.upsert_timeseries_cache("shares", new)

    assert len(result) == 1
    assert result.iloc[0]["close"] == 2.0


def test_share_cache_compaction_removes_non_flow_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "shares.csv"
    monkeypatch.setitem(csv_cache.CACHE_FILES, "shares", path)
    rows = pd.DataFrame(
        [
            {
                "etf_code": "159915",
                "date": "2026-07-17",
                "shares": 100.0,
                "validation_status": "current_share_only_no_history",
            },
            {
                "etf_code": "510300",
                "date": "2026-07-17",
                "shares": 200.0,
                "validation_status": "ok",
            },
        ]
    )
    rows.to_csv(path, index=False, encoding="utf-8-sig")

    compacted = csv_cache.compact_timeseries_cache("shares")

    assert compacted["etf_code"].tolist() == ["510300"]
    assert csv_cache.read_timeseries_cache("shares")["etf_code"].tolist() == ["510300"]


def test_rebuild_prefetches_once_then_uses_local_market_cache(monkeypatch, tmp_path: Path) -> None:
    import etf_temperature.pipeline as pipeline

    calls = {"prefetch": 0, "run": []}

    def fake_prefetch(start: str, end: str, client=None):
        calls["prefetch"] += 1
        return {"start_date": start, "end_date": end, "quote_cache_hit": False, "share_cache_hit": False}

    def fake_run(date_text: str, client=None, **kwargs):
        calls["run"].append((date_text, kwargs))
        return PipelineResult(
            report_date=date_text,
            report_path=None,
            chart_dir=None,
            metrics={},
            summary={},
            representatives=pd.DataFrame(),
            cluster_daily=pd.DataFrame(),
            unresolved_issues=[],
        )

    monkeypatch.setattr(pipeline, "prefetch_rebuild_market_data", fake_prefetch)
    monkeypatch.setattr(pipeline, "run_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "_local_snapshot_available", lambda date_text: False)
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", tmp_path)

    results = rebuild_history("20260629", "20260703")

    assert len(results) == 5
    assert calls["prefetch"] == 1
    assert [item[0] for item in calls["run"]] == [
        "2026-06-29",
        "2026-06-30",
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    ]
    assert all(kwargs["market_data_cache_only"] is True for _, kwargs in calls["run"])


def test_rebuild_checks_market_cache_then_renders_when_no_data_changed(monkeypatch, tmp_path: Path) -> None:
    import etf_temperature.pipeline as pipeline

    def fake_prefetch(*args, **kwargs):
        return {
            "fetched_quote_rows": 0,
            "fetched_share_rows": 0,
            "fetched_nav_rows": 0,
        }

    def fake_render(date_text: str, *, cache_reason: str = "") -> PipelineResult:
        return PipelineResult(
            report_date=date_text,
            report_path=None,
            chart_dir=None,
            metrics={"render_mode": "cache"},
            summary={},
            representatives=pd.DataFrame(),
            cluster_daily=pd.DataFrame(),
            unresolved_issues=[],
            used_cache=True,
            cache_reason=cache_reason,
        )

    monkeypatch.setattr(pipeline, "prefetch_rebuild_market_data", fake_prefetch)
    monkeypatch.setattr(pipeline, "_local_snapshot_available", lambda date_text: True)
    monkeypatch.setattr(pipeline, "render_report_from_cache", fake_render)
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", tmp_path)

    results = rebuild_history("20260629", "20260703")

    assert [item.report_date for item in results] == [
        "2026-06-29",
        "2026-06-30",
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
    ]
    assert all(item.used_cache for item in results)
    assert '"fetched_nav_rows":0' in next(tmp_path.glob("rebuild_prefetch_*.json")).read_text(encoding="utf-8")


def test_rebuild_recompute_uses_local_market_cache_even_when_snapshots_exist(monkeypatch, tmp_path: Path) -> None:
    import etf_temperature.pipeline as pipeline

    calls = {"run": []}

    def fail_prefetch(*args, **kwargs):
        raise AssertionError("prefetch should be skipped when recompute uses local cache")

    def fail_render(*args, **kwargs):
        raise AssertionError("render cache should be skipped when recompute is requested")

    def fake_run(date_text: str, client=None, **kwargs):
        calls["run"].append((date_text, kwargs))
        return PipelineResult(
            report_date=date_text,
            report_path=None,
            chart_dir=None,
            metrics={"source_refresh_mode": "csv_date_code_cache"},
            summary={},
            representatives=pd.DataFrame(),
            cluster_daily=pd.DataFrame(),
            unresolved_issues=[],
        )

    monkeypatch.setattr(pipeline, "prefetch_rebuild_market_data", fail_prefetch)
    monkeypatch.setattr(pipeline, "render_report_from_cache", fail_render)
    monkeypatch.setattr(pipeline, "_local_snapshot_available", lambda date_text: True)
    monkeypatch.setattr(pipeline, "run_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", tmp_path)

    results = rebuild_history("20260629", "20260703", recompute=True)

    assert len(results) == 5
    assert all(kwargs["market_data_cache_only"] is True for _, kwargs in calls["run"])
    assert '"recompute":true' in next(tmp_path.glob("rebuild_prefetch_*.json")).read_text(encoding="utf-8")


def test_raw_cache_reuses_same_historical_url_without_new_file(tmp_path: Path) -> None:
    from etf_temperature.official_endpoints import OfficialClient

    class Response:
        text = '{"ok": true}'

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Session:
        def __init__(self) -> None:
            self.calls = 0
            self.headers = {}

        def get(self, url, headers=None, timeout=None):
            self.calls += 1
            return Response()

        @staticmethod
        def close() -> None:
            return None

    client = OfficialClient(raw_dir=tmp_path, retries=1)
    session = Session()
    client.session = session
    url = (
        "https://www.szse.cn/api/report/ShowReport/data?"
        "CATALOGID=1815_stock_snapshot&txtBeginDate=2026-06-29&txtEndDate=2026-07-03&PAGENO=1"
    )

    assert client.get_text(url, raw_prefix="szse_quote_p1") == '{"ok": true}'
    assert client.get_text(url, raw_prefix="szse_quote_p1") == '{"ok": true}'
    assert session.calls == 1
    assert len(list(tmp_path.glob("*.txt"))) == 1


def test_raw_binary_cache_reuses_same_historical_xlsx_url(tmp_path: Path) -> None:
    from etf_temperature.official_endpoints import OfficialClient

    class Response:
        content = b"PK\x03\x04test-workbook"

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Session:
        def __init__(self) -> None:
            self.calls = 0
            self.headers = {}

        def get(self, url, headers=None, timeout=None):
            self.calls += 1
            return Response()

        @staticmethod
        def close() -> None:
            return None

    client = OfficialClient(raw_dir=tmp_path, retries=1)
    session = Session()
    client.session = session
    url = (
        "https://fund.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&"
        "CATALOGID=scsj_fund_jjgm&txtStart=2026-01-01&txtEnd=2026-03-31"
    )

    assert client.get_bytes(url, raw_prefix="szse_share_xlsx") == Response.content
    assert client.get_bytes(url, raw_prefix="szse_share_xlsx") == Response.content
    assert session.calls == 1
    assert len(list(tmp_path.glob("*.bin"))) == 1


@pytest.mark.parametrize("extension", ["txt", "bin"])
def test_raw_response_index_reads_legacy_files_and_keeps_refresh_fallback(tmp_path, extension) -> None:
    from etf_temperature.official_endpoints import OfficialClient

    url = "https://example.com/source"
    prefix = "example_source"
    digest = sha1(url.encode()).hexdigest()[:16]

    def store(stamp, payload):
        path = tmp_path / f"{stamp}_{prefix}_{digest}.{extension}"
        path.write_bytes(f"URL: {url}\n\n".encode() + payload if extension == "txt" else payload)
        return path

    store("20260703_090000_000000", b"older")
    latest = store("20260703_091000_000000", b"latest")
    client = OfficialClient(raw_dir=tmp_path, retries=1)
    worker = OfficialClient(raw_dir=tmp_path, retries=1)
    get = worker.get_text if extension == "txt" else worker.get_bytes

    def expected(value):
        return value.decode() if extension == "txt" else value

    def fail_get(*args, **kwargs):
        raise OSError("source unavailable")

    worker.session = SimpleNamespace(get=fail_get, close=lambda: None)
    assert get(url, raw_prefix=prefix, cache_ttl_seconds=-1) == expected(b"latest")
    assert get(url, raw_prefix=prefix, force_refresh=True) == expected(b"latest")
    latest.unlink()
    assert get(url, raw_prefix=prefix, cache_ttl_seconds=-1) == expected(b"older")
    save = client._save_raw if extension == "txt" else client._save_raw_bytes
    saved = save(url, expected(b"updated"), prefix)
    assert save(url, expected(b"updated"), prefix) == saved
    assert get(url, raw_prefix=prefix, cache_ttl_seconds=-1) == expected(b"updated")
    assert len(list(tmp_path.glob(f"*.{extension}"))) == 2


def test_raw_response_index_scans_once_across_parallel_writes(tmp_path, monkeypatch) -> None:
    from etf_temperature import official_endpoints

    clients = [official_endpoints.OfficialClient(raw_dir=tmp_path) for _ in range(4)]
    scans = []
    scandir = official_endpoints.os.scandir

    def track_scans(directory):
        scans.append(Path(directory))
        return scandir(directory)

    monkeypatch.setattr(official_endpoints.os, "scandir", track_scans)

    def save(i):
        url = f"https://example.com/source/{i // 2}"
        return clients[i % len(clients)]._save_raw(url, "response", "example")

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(save, range(16)))

    assert len(set(paths)) == 8
    assert len(scans) == 1


def test_szse_quote_history_caps_bulk_pages(monkeypatch) -> None:
    from etf_temperature import szse_official

    calls: list[str] = []

    def fake_get_json_with_base_fallback(
        client, path_and_query, *, referer_path, raw_prefix, preferred_base=None
    ):
        calls.append(path_and_query)
        page = 1
        if "PAGENO=" in path_and_query:
            page = int(path_and_query.rsplit("PAGENO=", 1)[1])
        return (
            [
                {
                    "metadata": {"tabkey": "tab2", "pagecount": 2082},
                    "data": [
                        {
                            "jyrq": "2026-07-03",
                            "zqdm": f"159{page:03d}",
                            "qss": "1.00",
                            "ks": "1.00",
                            "zg": "1.01",
                            "zd": "0.99",
                            "ss": "1.00",
                            "sdf": "0.00",
                            "cjgs": "1.00",
                            "cjje": "1.00",
                        }
                    ],
                }
            ],
            "https://www.szse.cn",
        )

    monkeypatch.setattr(szse_official, "_get_json_with_base_fallback", fake_get_json_with_base_fallback)
    monkeypatch.setattr(szse_official.time, "sleep", lambda seconds: None)
    szse_official.LAST_WARNINGS.clear()

    result = szse_official.fetch_quote_history(object(), "2026-07-02", "2026-07-03", max_pages=2)

    assert len(calls) == 2
    assert all("PAGENO=3" not in item for item in calls)
    assert len(result) == 2
    assert any("pagecount 2082 exceeds cap 2" in warning for warning in szse_official.LAST_WARNINGS)


def test_szse_quote_history_queries_target_codes(monkeypatch) -> None:
    from etf_temperature import szse_official

    calls: list[str] = []

    def fake_get_json_with_base_fallback(
        client, path_and_query, *, referer_path, raw_prefix, preferred_base=None
    ):
        calls.append(path_and_query)
        assert "txtDMorJC=" in path_and_query
        code = path_and_query.split("txtDMorJC=", 1)[1].split("&", 1)[0]
        return (
            [
                {
                    "metadata": {"tabkey": "tab2", "pagecount": 1},
                    "data": [
                        {
                            "jyrq": "2026-07-03",
                            "zqdm": code,
                            "qss": "1.00",
                            "ks": "1.00",
                            "zg": "1.01",
                            "zd": "0.99",
                            "ss": "1.00",
                            "sdf": "0.00",
                            "cjgs": "1.00",
                            "cjje": "1.00",
                        }
                    ],
                }
            ],
            "https://www.szse.cn",
        )

    monkeypatch.setattr(szse_official, "_get_json_with_base_fallback", fake_get_json_with_base_fallback)
    szse_official.LAST_WARNINGS.clear()

    result = szse_official.fetch_quote_history(
        object(),
        "2026-07-03",
        "2026-07-03",
        codes=["159915", "159949"],
        max_workers=1,
    )

    assert len(calls) == 2
    assert all("CATALOGID=1815_stock_snapshot" in item for item in calls)
    assert set(result["etf_code"]) == {"159915", "159949"}

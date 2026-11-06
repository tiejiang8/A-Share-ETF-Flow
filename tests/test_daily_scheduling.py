from __future__ import annotations

import json
import os

import pytest

from etf_temperature import cli, official_endpoints, pipeline
from etf_temperature import _atomic


def test_auto_reuses_only_complete_formal_report(tmp_path, monkeypatch):
    day = "2026-11-05"
    output = tmp_path / day
    chart_dir = output / "charts"
    chart_dir.mkdir(parents=True)
    report = output / f"A股ETF资金温度日报_{day}.md"
    appendix = output / f"A股ETF资金温度日报_技术附录_{day}.md"
    render_outputs = (report, appendix, chart_dir / "index.html")
    for path in (output / "validation.md", output / "daily_etf.parquet", output / "daily_cluster.parquet"):
        path.touch()
    (output / "summary.json").write_text(
        json.dumps({"asof": day, "formal_report_allowed": True}), encoding="utf-8",
    )
    snapshot = output / "daily_snapshot.json"
    payload = {"date": day, "validation": {"formal_report_allowed": True, "formal_blockers": []}}
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    for path in render_outputs:
        path.touch()
    monkeypatch.setattr(pipeline, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "discover_report_date", lambda client: day)
    monkeypatch.setattr(pipeline, "should_refresh_report_date", lambda *args, **kwargs: (False, "cache"))

    def fail_if_called():
        raise AssertionError("reuse must not start the official pipeline")

    monkeypatch.setattr(pipeline, "write_probe_outputs", fail_if_called)
    result = pipeline.run_pipeline("auto", object(), reuse_formal=True)
    assert result.used_cache and result.report_path == report

    # A failed rerun wrote a new snapshot but never finished the appendix.
    snapshot_time = snapshot.stat().st_mtime
    os.utime(appendix, (snapshot_time - 10, snapshot_time - 10))
    assert pipeline._reuse_formal_daily_report(day) is None

    payload["validation"]["formal_report_allowed"] = False
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    for path in render_outputs:
        path.touch()
    assert pipeline._reuse_formal_daily_report(day) is None
    payload["validation"]["formal_report_allowed"] = True
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    report.unlink()
    assert pipeline._reuse_formal_daily_report(day) is None


def test_cli_rejects_second_daily_writer_before_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "OUTPUTS_DIR", tmp_path)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("second instance must not enter the pipeline")

    monkeypatch.setattr(cli, "run_pipeline", fail_if_called)
    with _atomic.exclusive_file_lock(tmp_path / ".daily_run.lock", timeout=0):
        assert cli.main(["run", "--date", "auto"]) == 75
    assert json.loads(capsys.readouterr().out)["status"] == "already_running"


def test_daily_source_metadata_stays_in_runtime_outputs(tmp_path, monkeypatch):
    docs, outputs = tmp_path / "docs", tmp_path / "outputs"
    docs.mkdir()
    outputs.mkdir()
    reference = docs / "official_endpoints.md"
    reference.write_text("published reference", encoding="utf-8")
    monkeypatch.setattr(official_endpoints, "DOCS_DIR", docs)
    monkeypatch.setattr(official_endpoints, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(official_endpoints, "ensure_dirs", lambda: None)

    official_endpoints.write_probe_outputs(update_reference=False)

    assert reference.read_text(encoding="utf-8") == "published reference"
    assert (outputs / "source_probe.md").is_file()
    assert json.loads((outputs / "source_probe.json").read_text(encoding="utf-8"))["endpoints"]


def test_failed_render_replace_keeps_previous_markdown(tmp_path, monkeypatch):
    report = tmp_path / "report.md"
    report.write_text("complete report", encoding="utf-8")
    written_at = report.stat().st_mtime_ns

    def interrupted(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(_atomic, "atomic_replace", interrupted)
    with pytest.raises(OSError, match="simulated write failure"):
        _atomic.atomic_write_text(report, "replacement report")

    assert report.read_text(encoding="utf-8") == "complete report"
    assert report.stat().st_mtime_ns == written_at
    assert list(tmp_path.iterdir()) == [report]

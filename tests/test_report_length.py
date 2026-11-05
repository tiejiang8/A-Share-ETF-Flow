import pandas as pd

from etf_temperature.report import generate_report


def _cluster_rows():
    return pd.DataFrame(
        [
            {"cluster_id": f"C{i:02d}", "cluster_name": f"集群{i:02d}", "member_count": 1, "total_size_100m": 100, "estimated_net_subscription_100m": i - 16, "one_day_flow_to_size": (i - 16) / 100, "estimated_net_subscription_5d_100m": i, "weighted_pct_change": i / 10 - 2, "amount_vs_5d": 1, "cluster_temperature": 50 + i % 30, "temperature_change": i % 5 - 2, "temperature_direction": "稳定", "temperature_level": "中性", "temperature_phase": "剧烈分化", "structure_judgement": "结构分化"}
            for i in range(1, 37)
        ]
    )


def test_main_report_is_constrained(tmp_path):
    path = tmp_path / "A股ETF资金温度日报_2026-07-02.md"
    generate_report("2026-07-02", path, {"a_share_equity_etf_count": 31, "formal_report_allowed": True}, {"etf_total": 31}, _cluster_rows(), pd.DataFrame({"etf_code": [], "etf_name": [], "temperature": []}), pd.DataFrame({"cluster_id": [], "representative_code": [], "representative_name": []}), pd.DataFrame(), [])
    text = path.read_text(encoding="utf-8")
    assert "真实申购" not in text
    assert "今日总判断" in text
    assert "数据状态与全市场概览" in text
    assert "资金迁移Top20" in text
    subscription_section = text.split("### 单日估算净申购", 1)[1].split("### 单日估算净赎回", 1)[0]
    assert len([line for line in subscription_section.splitlines() if line.startswith("|")]) == 22
    assert "仅列示15个净赎回集群" in text
    assert "观察集群各类排名" not in text
    assert "单只ETF重点排行榜Top20" not in text
    assert "重点ETF综合观察Top20" in text
    assert "方法、样本与使用边界" in text
    assert "abnormal_score" not in text
    assert len(text) < 30_000
    appendix = path.with_name("A股ETF资金温度日报_技术附录_2026-07-02.md").read_text(encoding="utf-8")
    assert "观察集群完整字段" in appendix
    assert "单只ETF全量Top50排行" in appendix


def test_market_overview_tolerates_missing_net_subscription_column(tmp_path):
    """Regression guard for report.py::_market_overview_table.

    The overview table once read the flow column with ``etf_daily.get(col)``.  When the
    column was absent, ``get`` returned None, ``pd.to_numeric(None, errors="coerce")``
    produced a bare ``numpy.float64`` nan, and the chained ``.dropna()`` raised
    ``AttributeError``.  Sparse frames are a supported input (see the minimal fixtures in
    this module), so the table must degrade to zero gross flow instead of crashing.
    """
    path = tmp_path / "A股ETF资金温度日报_2026-07-03.md"
    etf_daily = pd.DataFrame({"etf_code": [], "etf_name": [], "temperature": []})
    generate_report(
        "2026-07-03",
        path,
        {"a_share_equity_etf_count": 31, "formal_report_allowed": True},
        {"etf_total": 31},
        _cluster_rows(),
        etf_daily,
        pd.DataFrame({"cluster_id": [], "representative_code": [], "representative_name": []}),
        pd.DataFrame(),
        [],
    )
    text = path.read_text(encoding="utf-8")
    assert "数据状态与全市场概览" in text
    assert "单日整体流入" in text
    assert "单日整体流出" in text

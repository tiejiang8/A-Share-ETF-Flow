import pandas as pd

from etf_temperature.aggregation import build_abnormal_pool
from etf_temperature.charts import select_chart_codes
from etf_temperature.report import generate_report


def test_abnormal_pool_keeps_research_top50():
    daily = pd.DataFrame(
        [
            {
                "date": "2026-07-02",
                "etf_code": f"51{i:04d}",
                "etf_name": f"ETF{i}",
                "tracking_index_name": "指数",
                "primary_cluster": "C01",
                "primary_cluster_name": "集群",
                "amount": (i + 1) * 100_000_000,
                "amount_vs_5d": 1 + i / 20,
                "share_daily_change": i * 1000,
                "share_daily_change_pct": i / 10,
                "estimated_net_subscription_100m": i - 10,
                "pct_change": i / 5,
                "temperature": 40 + i,
                "premium_discount": 0.0,
                "price_share_signal": "",
            }
            for i in range(60)
        ]
    )

    pool = build_abnormal_pool(daily)

    assert len(pool) == 50
    assert pool["tracking_priority"].tolist() == list(range(1, 51))
    assert pool["abnormal_score"].is_monotonic_decreasing


def test_chart_selection_prefers_abnormal_top20():
    abnormal = pd.DataFrame(
        {
            "etf_code": [f"51{i:04d}" for i in range(25)],
            "abnormal_score": list(range(25)),
        }
    )
    etf_daily = pd.DataFrame(
        {
            "etf_code": [f"51{i:04d}" for i in range(40)],
            "estimated_net_subscription_100m": list(range(40)),
            "temperature": list(range(40)),
        }
    )
    representatives = pd.DataFrame({"representative_code": [f"52{i:04d}" for i in range(31)]})

    codes = select_chart_codes(representatives, etf_daily, abnormal)

    assert len(codes) == 20
    assert codes[0] == "510024"
    assert codes[-1] == "510005"


def test_appendix_keeps_each_ranking_top50(tmp_path):
    etf_daily = pd.DataFrame(
        [
            {
                "etf_code": f"51{i:04d}",
                "etf_name": f"ETF{i}",
                "date": "2026-07-02",
                "tracking_index_name": "指数",
                "primary_cluster": "C01",
                "primary_cluster_name": "集群",
                "estimated_net_subscription_100m": i - 15,
                "estimated_net_subscription_5d_100m": i - 20,
                "amount": i * 100_000_000,
                "amount_vs_5d": i / 10,
                "pct_change": i / 10 - 2,
                "share_daily_change": i * 1000,
                "share_daily_change_pct": i / 20 - 1,
                "premium_discount": i / 1000 - 0.03,
                "temperature": 40 + i,
            }
            for i in range(1, 61)
        ]
    )
    cluster_daily = pd.DataFrame(
        [
            {
                "cluster_id": f"C{i:02d}",
                "cluster_name": f"集群{i:02d}",
                "member_count": 1,
                "total_size_100m": 100,
                "estimated_net_subscription_100m": i - 16,
                "one_day_flow_to_size": (i - 16) / 100,
                "estimated_net_subscription_5d_100m": i,
                "weighted_pct_change": i / 10 - 2,
                "amount_vs_5d": 1,
                "cluster_temperature": 50 + i % 30,
                "temperature_change": i % 5 - 2,
                "temperature_direction": "稳定",
                "temperature_level": "中性",
                "temperature_phase": "剧烈分化",
                "structure_judgement": "结构分化",
            }
            for i in range(1, 32)
        ]
    )
    output = tmp_path / "A股ETF资金温度日报_2026-07-02.md"

    generate_report(
        "2026-07-02",
        output,
        {"a_share_equity_etf_count": 30, "formal_report_allowed": True},
        {"etf_total": 30},
        cluster_daily,
        etf_daily,
        pd.DataFrame({"cluster_id": [], "representative_code": [], "representative_name": []}),
        build_abnormal_pool(etf_daily),
        [],
    )
    text = output.read_text(encoding="utf-8")
    appendix = output.with_name("A股ETF资金温度日报_技术附录_2026-07-02.md").read_text(encoding="utf-8")

    assert "资金迁移Top20" in text
    assert "重点ETF综合观察Top20" in text
    main_observation = text.split("### 重点ETF综合观察Top20", 1)[1].split("### 明日观察", 1)[0]
    table_lines = [line for line in main_observation.splitlines() if line.startswith("|")]
    assert len(table_lines) == 22  # 表头、分隔行和20条ETF记录
    assert "单日估算净申购Top20" not in text
    assert "份额增长Top20" not in text
    assert "abnormal_score" not in text
    assert "单日估算净申购Top50" in appendix
    assert "单日估算净赎回Top50" in appendix
    assert "成交额Top50" in appendix
    assert "温度Top50" in appendix
    assert "去重观察池Top30" in appendix
    assert "异动评分" in appendix
    assert "份额变化率绝对值" in appendix
    assert "|份额变化率|" not in appendix
    assert appendix.count("ETF代码") >= 10

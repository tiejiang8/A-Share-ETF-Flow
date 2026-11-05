from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .official_endpoints import CONFIG_DIR


DEFAULT_REPORT_CONFIG = {
    "abnormal_pool_size": 50,
    "candidate_rule_top_n": 50,
    "main_cluster_flow_top_n": 20,
    "main_abnormal_top_n": 20,
    "appendix_top_n": 50,
    "watchlist_top_n": 30,
    "watchlist_max_per_cluster": 3,
    "main_chart_top_n": 12,
    "tracking_chart_pool_top_n": 20,
    "min_backtest_trading_days": 60,
    "min_concentration_member_count": 4,
}


@lru_cache(maxsize=8)
def load_temperature_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else CONFIG_DIR / "temperature.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    _validate_temperature_config(payload)
    return payload


def _validate_temperature_config(config: dict[str, Any]) -> None:
    required_sections = [
        "product_temperature_weights",
        "final_temperature_weights",
        "cluster_temperature_weights",
        "scaling",
        "thresholds",
        "consistency_scores",
        "history",
    ]
    missing = [section for section in required_sections if section not in config]
    if missing:
        raise ValueError(f"temperature config missing sections: {missing}")

    for section in [
        "product_temperature_weights",
        "final_temperature_weights",
        "cluster_temperature_weights",
    ]:
        total = sum(float(value) for value in config[section].values())
        if abs(total - 1.0) > 0.000001:
            raise ValueError(f"{section} weights must sum to 1.0, got {total}")


def get_threshold(name: str, default: float | int | None = None) -> float:
    config = load_temperature_config()
    value = config.get("thresholds", {}).get(name, default)
    if value is None:
        raise KeyError(f"missing threshold: {name}")
    return float(value)


def get_report_config() -> dict[str, int]:
    config = load_temperature_config()
    report_config = dict(DEFAULT_REPORT_CONFIG)
    report_config.update(config.get("report", {}) or {})
    return {key: int(value) for key, value in report_config.items()}

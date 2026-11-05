from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from .config import get_report_config

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def select_chart_codes(
    representatives: pd.DataFrame,
    etf_daily: pd.DataFrame,
    abnormal_pool: pd.DataFrame,
    max_codes: int = 20,
) -> list[str]:
    codes: list[str] = []
    if not abnormal_pool.empty:
        pool = abnormal_pool.copy()
        if "abnormal_score" in pool:
            pool = pool.sort_values("abnormal_score", ascending=False)
        codes.extend(pool["etf_code"].dropna().astype(str).head(max_codes).tolist())
    for column, ascending, count in [
        ("estimated_net_subscription_100m", False, 10),
        ("estimated_net_subscription_100m", True, 10),
        ("temperature", False, 10),
    ]:
        if column in etf_daily:
            codes.extend(etf_daily.sort_values(column, ascending=ascending)["etf_code"].head(count).astype(str).tolist())
    if not representatives.empty:
        codes.extend(representatives["representative_code"].dropna().astype(str).tolist())
    unique = []
    for code in codes:
        if code not in unique:
            unique.append(code)
    return unique[:max_codes]


def plot_kline_for_code(history: pd.DataFrame, code: str, name: str, output_dir: Path) -> Path | None:
    group = history[history["etf_code"].astype(str).eq(str(code))].dropna(subset=["date", "open", "high", "low", "close"]).copy()
    if group.empty:
        return None
    group = group.sort_values("date").tail(20)
    fig, ax = plt.subplots(figsize=(9, 4))
    xs = range(len(group))
    for x, (_, row) in zip(xs, group.iterrows()):
        color = "#c00000" if row["close"] >= row["open"] else "#007a3d"
        ax.vlines(x, row["low"], row["high"], color=color, linewidth=1)
        bottom = min(row["open"], row["close"])
        height = abs(row["close"] - row["open"]) or max(row["close"] * 0.001, 0.001)
        ax.add_patch(plt.Rectangle((x - 0.3, bottom), 0.6, height, color=color, alpha=0.75))
    ax.set_title(f"{code} {name}")
    ax.set_xticks(list(xs)[:: max(1, len(group) // 6)])
    ax.set_xticklabels(group["date"].tolist()[:: max(1, len(group) // 6)], rotation=30, ha="right")
    ax.grid(True, axis="y", alpha=0.2)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{code}_kline.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def generate_charts(
    history: pd.DataFrame,
    etf_daily: pd.DataFrame,
    representatives: pd.DataFrame,
    abnormal_pool: pd.DataFrame,
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_path in output_dir.glob("*_kline.png"):
        old_path.unlink()
    report_config = get_report_config()
    codes = select_chart_codes(
        representatives,
        etf_daily,
        abnormal_pool,
        max_codes=min(int(report_config["tracking_chart_pool_top_n"]), 5),
    )
    name_map = etf_daily.set_index("etf_code")["etf_name"].astype(str).to_dict() if not etf_daily.empty else {}
    paths = []
    for code in codes:
        path = plot_kline_for_code(history, code, name_map.get(code, ""), output_dir)
        if path:
            paths.append(path)
    write_chart_index(paths, output_dir)
    write_cover_image(paths, output_dir)
    return paths


def write_cover_image(paths: list[Path], output_dir: Path) -> Path | None:
    cover = output_dir / "cover.png"
    if not paths:
        cover.unlink(missing_ok=True)
        return None
    shutil.copyfile(paths[0], cover)
    return cover


def write_chart_index(paths: list[Path], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "<!doctype html>",
        "<html lang=\"zh-CN\">",
        "<head><meta charset=\"utf-8\"><title>ETF K线图索引</title>",
        "<style>body{font-family:Arial,'Microsoft YaHei',sans-serif;margin:24px;color:#222} .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px} img{max-width:100%;border:1px solid #ddd} figure{margin:0} figcaption{font-size:13px;margin:6px 0 12px}</style>",
        "</head><body>",
        "<h1>ETF K线图索引</h1>",
        "<div class=\"grid\">",
    ]
    for path in paths:
        rel = path.name
        code = path.stem.replace("_kline", "")
        lines.append(f"<figure><img src=\"{rel}\" alt=\"{code} K线图\"><figcaption>{code}</figcaption></figure>")
    lines.extend(["</div>", "</body></html>"])
    index = output_dir / "index.html"
    index.write_text("\n".join(lines), encoding="utf-8")
    return index

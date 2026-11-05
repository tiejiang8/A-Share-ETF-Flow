from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .official_endpoints import CONFIG_DIR


@dataclass
class ClusterRule:
    cluster_id: str
    cluster_name: str
    include_index_codes: list[str]
    include_index_keywords: list[str]
    include_etf_keywords: list[str]
    exclude_keywords: list[str]
    classification_priority: int
    representative_policy: str = "size_liquidity_completeness"
    exact_index_codes: list[str] | None = None
    exact_index_names: list[str] | None = None
    index_name_patterns: list[str] | None = None
    etf_name_patterns: list[str] | None = None
    exclude_index_codes: list[str] | None = None
    exclude_name_patterns: list[str] | None = None
    priority: int | None = None
    manual_overrides: list[str] | None = None
    notes: str = ""


def load_cluster_rules(path: Path | None = None) -> list[ClusterRule]:
    path = path or CONFIG_DIR / "cluster_rules.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = []
    for item in payload["clusters"]:
        item = dict(item)
        item.setdefault("include_index_codes", item.get("exact_index_codes") or [])
        item.setdefault("include_index_keywords", item.get("index_name_patterns") or [])
        item.setdefault("include_etf_keywords", item.get("etf_name_patterns") or [])
        item.setdefault("exclude_keywords", item.get("exclude_name_patterns") or [])
        item.setdefault("classification_priority", item.get("priority", 999))
        rules.append(ClusterRule(**item))
    return sorted(rules, key=lambda rule: rule.priority if rule.priority is not None else rule.classification_priority)


def load_manual_overrides(path: Path | None = None) -> dict[str, dict[str, str]]:
    path = path or CONFIG_DIR / "cluster_manual_overrides.yaml"
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = payload.get("overrides") or []
    return {str(item.get("etf_code", "")).zfill(6): item for item in overrides if item.get("etf_code")}


def cluster_frame(rules: list[ClusterRule] | None = None) -> pd.DataFrame:
    rules = rules or load_cluster_rules()
    return pd.DataFrame(
        [
            {
                "cluster_id": rule.cluster_id,
                "cluster_name": rule.cluster_name,
                "classification_priority": rule.priority if rule.priority is not None else rule.classification_priority,
                "representative_policy": rule.representative_policy,
            }
            for rule in sorted(rules, key=lambda r: r.cluster_id)
        ]
    )


def _contains_any(text: str, keywords: list[str]) -> str | None:
    upper = text.upper()
    for keyword in keywords:
        if keyword and keyword.upper() in upper:
            return keyword
    return None


def _normalized_rule_list(values: list[str] | None) -> list[str]:
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _match_rule(row: pd.Series, rule: ClusterRule) -> tuple[bool, str, float]:
    index_code = str(row.get("tracking_index_code") or "").strip()
    index_name = str(row.get("tracking_index_name") or "")
    etf_text = " ".join(str(row.get(col) or "") for col in ["etf_name", "expanded_name"])
    all_name_text = f"{index_name} {etf_text}"
    exact_index_codes = set(_normalized_rule_list(rule.exact_index_codes or rule.include_index_codes))
    exact_index_names = set(_normalized_rule_list(rule.exact_index_names))
    index_patterns = _normalized_rule_list(rule.index_name_patterns or rule.include_index_keywords)
    etf_patterns = _normalized_rule_list(rule.etf_name_patterns or rule.include_etf_keywords)
    exclude_index_codes = set(_normalized_rule_list(rule.exclude_index_codes))
    exclude_patterns = _normalized_rule_list(rule.exclude_name_patterns or rule.exclude_keywords)

    if index_code and index_code in exclude_index_codes:
        return False, "", 0.0
    if _contains_any(all_name_text, exclude_patterns):
        return False, "", 0.0
    if index_code and index_code in exact_index_codes:
        return True, f"index_code:{index_code}", 0.98
    if index_name and index_name in exact_index_names:
        return True, f"index_name_exact:{index_name}", 0.96
    keyword = _contains_any(index_name, index_patterns)
    if keyword:
        return True, f"index_name_keyword:{keyword}", 0.92
    keyword = _contains_any(etf_text, etf_patterns)
    if keyword:
        return True, f"etf_keyword:{keyword}", 0.74
    return False, "", 0.0


def classify_universe(universe: pd.DataFrame, rules: list[ClusterRule] | None = None) -> pd.DataFrame:
    rules = rules or load_cluster_rules()
    overrides = load_manual_overrides()
    rule_by_cluster = {rule.cluster_id: rule for rule in rules}
    records: list[dict[str, Any]] = []
    version = datetime.now().strftime("%Y%m%d")
    for _, row in universe[universe["is_a_share_equity_etf"]].iterrows():
        code = str(row["etf_code"]).zfill(6)
        matches: list[tuple[ClusterRule, str, float]] = []
        override = overrides.get(code)
        if override:
            rule = rule_by_cluster.get(str(override.get("primary_cluster") or ""))
            if rule:
                matches.append((rule, f"manual_override:{override.get('reason', '')}", 1.0))
        if not matches:
            for rule in rules:
                ok, basis, confidence = _match_rule(row, rule)
                if ok:
                    matches.append((rule, basis, confidence))
            matches = sorted(
                matches,
                key=lambda item: (
                    -item[2],
                    item[0].priority if item[0].priority is not None else item[0].classification_priority,
                ),
            )
        primary = matches[0] if matches else None
        secondaries = [m[0].cluster_id for m in matches[1:]]
        records.append(
            {
                "etf_code": code,
                "exchange": row["exchange"],
                "etf_name": row.get("etf_name", ""),
                "expanded_name": row.get("expanded_name", ""),
                "tracking_index_code": row.get("tracking_index_code", ""),
                "tracking_index_name": row.get("tracking_index_name", ""),
                "primary_cluster": primary[0].cluster_id if primary else "",
                "primary_cluster_name": primary[0].cluster_name if primary else "",
                "secondary_tags": ",".join(secondaries),
                "classification_basis": primary[1] if primary else "unclassified",
                "confidence": primary[2] if primary else 0.0,
                "manual_review_required": not bool(primary) or (primary[2] < 0.8),
                "classification_version": version,
                "source": "config/cluster_manual_overrides.yaml" if override and primary else "config/cluster_rules.yaml",
                "source_priority": 1,
                "source_url": str(CONFIG_DIR / "cluster_rules.yaml"),
                "retrieved_at": datetime.now().isoformat(timespec="seconds"),
                "data_date": version,
                "is_official": False,
                "validation_status": "ok" if primary else "unclassified",
            }
        )
    return pd.DataFrame(records)


def validate_membership(membership: pd.DataFrame, rules: list[ClusterRule] | None = None) -> dict:
    rules = rules or load_cluster_rules()
    expected = {rule.cluster_id for rule in rules}
    found = set(membership.loc[membership["primary_cluster"].ne(""), "primary_cluster"].unique()) if not membership.empty else set()
    duplicates = int(membership.duplicated("etf_code").sum()) if not membership.empty else 0
    return {
        "expected_cluster_count": len(expected),
        "clusters_with_members": len(found),
        "missing_clusters": sorted(expected - found),
        "duplicate_primary_assignments": duplicates,
        "unclassified_count": int((membership["primary_cluster"] == "").sum()) if not membership.empty else 0,
        "is_valid": len(expected - found) == 0 and duplicates == 0,
    }



def semantic_membership_issues(
    membership: pd.DataFrame,
    rules: list[ClusterRule] | None = None,
) -> pd.DataFrame:
    """Return rows whose assigned cluster no longer matches its configured rule.

    Manual overrides are treated as authoritative. This catches stale membership
    files after a rule change, such as a 科创ETF remaining in 创业板 because of
    an ambiguous short keyword.
    """
    if membership.empty:
        return pd.DataFrame(
            columns=[
                "etf_code",
                "etf_name",
                "tracking_index_code",
                "tracking_index_name",
                "primary_cluster",
                "primary_cluster_name",
                "classification_basis",
                "issue",
            ]
        )

    rules = rules or load_cluster_rules()
    rule_by_cluster = {rule.cluster_id: rule for rule in rules}
    issues: list[dict[str, Any]] = []
    for _, row in membership.iterrows():
        cluster_id = str(row.get("primary_cluster") or "")
        if not cluster_id:
            continue
        basis = str(row.get("classification_basis") or "")
        source = str(row.get("source") or "")
        if basis.startswith("manual_override:") or "cluster_manual_overrides" in source:
            continue
        rule = rule_by_cluster.get(cluster_id)
        if rule is None:
            issues.append(
                {
                    **{key: row.get(key, "") for key in [
                        "etf_code", "etf_name", "tracking_index_code",
                        "tracking_index_name", "primary_cluster",
                        "primary_cluster_name", "classification_basis",
                    ]},
                    "issue": "unknown_cluster",
                }
            )
            continue
        matched, _, _ = _match_rule(row, rule)
        if not matched:
            issues.append(
                {
                    **{key: row.get(key, "") for key in [
                        "etf_code", "etf_name", "tracking_index_code",
                        "tracking_index_name", "primary_cluster",
                        "primary_cluster_name", "classification_basis",
                    ]},
                    "issue": "assigned_cluster_no_longer_matches_rule",
                }
            )

    return pd.DataFrame(issues)

def membership_review_markdown(membership: pd.DataFrame, rules: list[ClusterRule] | None = None) -> str:
    validation = validate_membership(membership, rules)
    lines = ["# ETF集群归类复核", ""]
    for key, value in validation.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## 需要人工复核")
    review = membership[membership["manual_review_required"]] if not membership.empty else membership
    if review.empty:
        lines.append("无。")
    else:
        cols = ["etf_code", "etf_name", "tracking_index_name", "primary_cluster_name", "classification_basis", "confidence"]
        lines.append(review[cols].head(200).to_markdown(index=False))
    return "\n".join(lines) + "\n"

"""Extraction quality report builders."""


def _build_review_recommendations(
    unassigned: int,
    missing_evidence: int,
    uncertain: int,
    missing: int,
    *,
    provisional_groups: int = 0,
    sample_card_fill_rate: float = 1.0,
    rough_source_count: int = 0,
) -> list[str]:
    recs = []
    if unassigned > 0:
        recs.append("需人工复核：存在未归属事实")
    if missing_evidence > 0:
        recs.append("证据不足：部分记录缺少原文证据")
    if uncertain > 0:
        recs.append(f"需人工复核：有 {uncertain} 条存疑记录")
    if missing > 0:
        recs.append(f"覆盖不足：有 {missing} 条缺失关键字段")
    if provisional_groups > 0:
        recs.append(f"样品组存疑：有 {provisional_groups} 个 provisional 样品组")
    if sample_card_fill_rate < 0.6:
        recs.append("不完整：样品卡背景字段填充率偏低")
    if rough_source_count > 0:
        recs.append("证据定位不足：部分来源位置过粗")
    if not recs:
        recs.append("可入库，但建议抽查证据文本和来源位置")
    return recs


def build_extraction_report(
    paper_metadata: dict,
    sample_count: int,
    group_count: int,
    fact_count: int,
    assigned_count: int,
    unassigned_count: int,
    record_count: int,
    missing_evidence_count: int,
    uncertain_count: int,
    missing_count: int,
    pending_count: int,
    approved_count: int,
    category_counts: dict[str, int],
    provisional_groups: int = 0,
    sample_card_fill_rate: float = 1.0,
    rough_source_count: int = 0,
    extra_metrics: dict | None = None,
) -> dict:
    report = {
        "文献标题": paper_metadata.get("paper_title", ""),
        "DOI": paper_metadata.get("doi_or_url", ""),
        "期刊": paper_metadata.get("journal", ""),
        "发表年份": paper_metadata.get("year", ""),
        "识别样品数": sample_count,
        "样品组数": group_count,
        "提取事实总数": fact_count,
        "成功归属数": assigned_count,
        "未归属事实数": unassigned_count,
        "生成记录数": record_count,
        "缺少证据记录数": missing_evidence_count,
        "待审核数": pending_count,
        "存疑数": uncertain_count,
        "缺失数": missing_count,
        "通过数": approved_count,
        "各性能类别记录数": category_counts,
        "推荐人工复核项": _build_review_recommendations(
            unassigned_count,
            missing_evidence_count,
            uncertain_count,
            missing_count,
            provisional_groups=provisional_groups,
            sample_card_fill_rate=sample_card_fill_rate,
            rough_source_count=rough_source_count,
        ),
    }
    if extra_metrics:
        report.update(extra_metrics)
    return report

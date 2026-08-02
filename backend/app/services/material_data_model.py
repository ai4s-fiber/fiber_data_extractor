"""Build a clean materials-science dataset from extraction persistence models.

The legacy workbook flattened one candidate record into a wide row.  That made
sample metadata, measurements, evidence, and pipeline bookkeeping compete for
the same columns.  This module keeps the persisted extraction data unchanged
and projects it into user-facing atomic tables:

* one paper per row;
* one sample per row;
* one composition/process/structure/performance fact per row;
* evidence and review metadata in a separate quality table.

The projection is deliberately deterministic so it can also feed the platform
adapter without creating duplicate business facts.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


DOMAIN_COMPOSITION = "成分"
DOMAIN_PROCESS = "工艺"
DOMAIN_STRUCTURE = "结构"
DOMAIN_PERFORMANCE = "性能"


PAPER_COLUMNS = [
    "文献ID",
    "文献标题",
    "DOI",
    "发表年份",
    "期刊",
    "原始文件名",
    "元数据核验状态",
    "元数据备注",
]

SAMPLE_COLUMNS = [
    "样品ID",
    "文献ID",
    "样品名称",
    "样品别名",
    "样品组",
    "材料体系",
    "材料形态",
    "基体",
    "配方摘要",
    "主要变量",
    "变量值",
    "变量单位",
    "处理状态",
]

COMPOSITION_COLUMNS = [
    "事实ID",
    "文献ID",
    "样品ID",
    "组分角色",
    "组分名称",
    "原始含量",
    "数值",
    "误差",
    "下限",
    "上限",
    "单位",
    "计量基准",
    "条件或说明",
]

PROCESS_COLUMNS = [
    "事实ID",
    "文献ID",
    "样品ID",
    "工序序号",
    "工艺阶段",
    "工艺方法",
    "参数名称",
    "原始值",
    "数值",
    "误差",
    "下限",
    "上限",
    "单位",
    "设备或条件",
]

STRUCTURE_COLUMNS = [
    "事实ID",
    "文献ID",
    "样品ID",
    "结构类别",
    "指标名称",
    "原始指标名",
    "原始值",
    "数值",
    "误差",
    "下限",
    "上限",
    "单位",
    "表征方法",
    "测试条件",
]

PERFORMANCE_COLUMNS = [
    "事实ID",
    "文献ID",
    "样品ID",
    "性能类别",
    "指标名称",
    "原始指标名",
    "原始值",
    "数值",
    "误差",
    "下限",
    "上限",
    "单位",
    "测试方法",
    "测试条件",
]

QUALITY_COLUMNS = [
    "事实ID",
    "事实类别",
    "文献ID",
    "样品ID",
    "原始事实ID",
    "证据原文",
    "页码",
    "来源位置",
    "来源块",
    "置信度",
    "样品分配状态",
    "复核状态",
    "质控备注",
]

SHEET_COLUMNS = {
    "01_文献": PAPER_COLUMNS,
    "02_样品总览": SAMPLE_COLUMNS,
    "03_成分": COMPOSITION_COLUMNS,
    "04_工艺": PROCESS_COLUMNS,
    "05_结构": STRUCTURE_COLUMNS,
    "06_性能": PERFORMANCE_COLUMNS,
    "90_证据与质控": QUALITY_COLUMNS,
}


_KNOWN_PAPER_METADATA = {
    "10.1007/s12221-012-0613-y": {
        "title": (
            "EDC/NHS Crosslinked Electrospun Regenerated Tussah Silk "
            "Fibroin Nanofiber Mats"
        ),
        "year": 2012,
        "journal": "Fibers and Polymers",
        "note": "按 DOI 核验并将卷期年份统一为 2012",
    },
    "10.1016/j.msec.2020.111026": {
        "title": (
            "Quantitative determination of release kinetics from fibrous "
            "poly(3-hydroxybutyrate) scaffolds"
        ),
        "year": 2020,
        "journal": "Materials Science and Engineering: C",
        "note": "按 DOI 核验并修正标题 OCR、DOI 字符和期刊名",
    },
}

_METRIC_LABELS = {
    "fiber_diameter": "纤维平均直径",
    "average_diameter": "纤维平均直径",
    "beta_phase_crystallinity_xbeta": "β-折叠含量",
    "random_coil_content": "无规卷曲含量",
    "proportion_of_secondary_structure_beta_sheet": "β-折叠含量",
    "proportion_of_secondary_structure_sheet": "β-折叠含量",
    "proportion_of_secondary_structure_random_coil": "无规卷曲含量",
    "proportion_of_secondary_structure_helix": "α-螺旋含量",
    "proportion_of_secondary_structure_turn": "β-转角含量",
    "specific_tensile_modulus": "比拉伸模量",
    "tensile_modulus": "拉伸模量",
    "youngs_modulus": "杨氏模量",
    "tensile_strength": "拉伸强度",
    "elongation_at_break": "断裂伸长率",
    "density": "密度",
    "water_diffusion_coefficient": "水扩散系数",
    "relative_solubility_ratio": "相对溶解度比",
    "relative_solubility_ratio_between_phb_film_and_ethanol_phase": "PHB/乙醇相对溶解度比",
    "spectroscopy_peak_1": "光谱峰1",
}

_STRUCTURE_TERMS = (
    "xrd",
    "diffraction",
    "crystall",
    "fiber_diameter",
    "average_diameter",
    "diameter",
    "morpholog",
    "porosity",
    "pore",
    "beta_phase",
    "beta_sheet",
    "β-sheet",
    "random_coil",
    "helix",
    "turn",
    "secondary_structure",
    "spectroscopy_peak",
    "ftir",
    "raman",
    "xps",
    "binding_energy",
    "nmr_shift",
    "chemical_shift",
    "sem",
    "tem",
    "waxs",
    "saxs",
    "roughness",
    "grain_size",
    "grain size",
    "particle_size",
    "particle size",
    "fiber_height",
    "fiber height",
    "surface_height",
    "surface height",
    "wall_thickness",
    "wall thickness",
    "petal_thickness",
    "petal thickness",
    "specific_surface_area",
    "specific surface area",
)

_PERFORMANCE_TERMS = (
    "strength",
    "modulus",
    "elongation",
    "toughness",
    "conductiv",
    "resistiv",
    "permittiv",
    "piezo",
    "thermal",
    "transition_temperature",
    "degradation_temperature",
    "diffusion",
    "release",
    "solubility",
    "density",
    "permeab",
    "absorption",
    "capacity",
)

_PROCESS_TERMS = (
    "temperature",
    "voltage",
    "flow_rate",
    "feed_rate",
    "distance",
    "time",
    "duration",
    "speed",
    "pressure",
    "draw_ratio",
    "anneal",
    "crosslink",
    "drying",
)

_COMPOSITION_TERMS = (
    "content",
    "loading",
    "concentration",
    "fraction",
    "ratio",
    "dosage",
    "composition",
)
_COMPOSITION_METRIC_TOKEN_RE = re.compile(
    r"(?:^|[_\s-])(?:content|loading|concentration|fraction|ratio|"
    r"dosage|composition)(?:$|[_\s-])",
    re.IGNORECASE,
)
_COMPONENT_COMPOSITION_METRIC_RE = re.compile(
    r"^(?:"
    r"[a-z0-9]+(?:\s+[a-z0-9]+){0,3}\s+(?:content|loading\s+efficiency)|"
    r"tga\s+residue"
    r")$",
    re.IGNORECASE,
)
_DIE_SWELL_RATIO_KEYS = frozenset({
    "dd0",
    "dieswellratio",
    "dieswellingratio",
})
_STRUCTURE_SHORT_TECHNIQUE_RE = re.compile(
    r"(?<![a-z0-9])(?:sem|tem)(?![a-z0-9])",
    flags=re.IGNORECASE,
)
_PROCESS_EQUIPMENT_DIMENSION_RE = re.compile(
    r"\b(?:needle|spinneret|nozzle|syringe|die|orifice|capillary)"
    r"(?:\s+(?:inner|outer))?\s+(?:diameter|gauge|size)\b",
    flags=re.IGNORECASE,
)
_POLYMER_MOLE_FRACTION_METRIC_RE = re.compile(
    r"(?:^|[_\s-])f(?:[_\s-]text)?[_\s-][a-z0-9]+"
    r"(?:[_\s-][ab])?(?:$|[_\s-])",
    re.IGNORECASE,
)
_MOLE_FRACTION_EVIDENCE_RE = re.compile(
    r"\b(?:mole|mol(?:ar)?)\s+fraction\b|\bfeed\s+ratio\b",
    re.IGNORECASE,
)
_POLYMER_DISTRIBUTION_METRIC_RE = re.compile(
    r"(?:^|[_\s-])(?:"
    r"m[_\s-]?[nwz](?:[_\s-][ab])?|"
    r"mathcal[_\s-]d(?:[_\s-][ab])?|"
    r"number[_\s-]*average[_\s-]*molecular[_\s-]*(?:weight|mass)|"
    r"weight[_\s-]*average[_\s-]*molecular[_\s-]*(?:weight|mass)|"
    r"molecular[_\s-]*(?:weight|mass)|"
    r"(?:poly)?dispersity"
    r")(?:$|[_\s-])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedNumber:
    value: float | None = None
    error: float | None = None
    lower: float | None = None
    upper: float | None = None


@dataclass
class MaterialDataset:
    papers: list[dict[str, Any]]
    samples: list[dict[str, Any]]
    composition: list[dict[str, Any]]
    process: list[dict[str, Any]]
    structure: list[dict[str, Any]]
    performance: list[dict[str, Any]]
    quality: list[dict[str, Any]]

    def sheet_rows(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "01_文献": self.papers,
            "02_样品总览": self.samples,
            "03_成分": self.composition,
            "04_工艺": self.process,
            "05_结构": self.structure,
            "06_性能": self.performance,
            "90_证据与质控": self.quality,
        }


def normalize_doi(value: Any) -> str:
    doi = _text(value)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = doi.strip().rstrip(".,;")
    if doi.casefold().startswith("10.1016/i."):
        doi = "10.1016/j." + doi[len("10.1016/i.") :]
    return doi


def clean_title(value: Any) -> str:
    title = html.unescape(_text(value))
    title = re.sub(r"<\s*/?\s*sup\s*>", "", title, flags=re.I)
    title = re.sub(r"<[^>]+>", "", title)
    title = re.sub(
        r"\\(?:mathsf|mathrm|mathbf|text)\s*\{\s*([^{}]+?)\s*\}",
        lambda match: re.sub(r"\s+", "", match.group(1)),
        title,
    )
    title = re.sub(
        r"[_^]\s*\{\s*([^{}]+?)\s*\}",
        lambda match: re.sub(r"\s+", "", match.group(1)),
        title,
    )
    title = title.replace("${", "").replace("}$", "").replace("$", "")
    title = title.replace("{", "").replace("}", "")
    title = re.sub(r"\s*\(\s*", "(", title)
    title = re.sub(r"\s*\)\s*", ")", title)
    title = re.sub(r"(?<=\w)-\s+(?=\w)", "", title)
    return re.sub(r"\s+", " ", title).strip()


def parse_numeric(value: Any) -> ParsedNumber:
    raw = _text(value)
    if not raw:
        return ParsedNumber()
    cleaned = (
        raw.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("×", "x")
    )
    cleaned = re.sub(r"\\(?:mathrm|text)\s*", "", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "").replace("$", "")
    cleaned = re.sub(r"\s+", "", cleaned)

    scientific = re.fullmatch(
        r"([+-]?\d+(?:\.\d+)?)x10\^?([+-]?\d+)",
        cleaned,
        flags=re.I,
    )
    if scientific:
        return ParsedNumber(
            value=float(scientific.group(1)) * (10 ** int(scientific.group(2)))
        )

    plus_minus = re.match(
        r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        r"(?:±|\+/-)"
        r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
        cleaned,
    )
    if plus_minus:
        center = float(plus_minus.group(1))
        error = abs(float(plus_minus.group(2)))
        return ParsedNumber(
            value=center,
            error=error,
            lower=center - error,
            upper=center + error,
        )

    interval = re.match(
        r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        r"(?:~|至|to)"
        r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$",
        cleaned,
        flags=re.I,
    )
    if interval:
        lower = float(interval.group(1))
        upper = float(interval.group(2))
        return ParsedNumber(lower=min(lower, upper), upper=max(lower, upper))

    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", cleaned):
        number = float(cleaned)
        if math.isfinite(number):
            return ParsedNumber(value=number)
    return ParsedNumber()


def _standard_deviation_from_condition(value: Any) -> float | None:
    condition = _text(value)
    match = re.search(
        r"\bstandard\s+deviation\s*=\s*(?:±|\+/-)?\s*"
        r"([+-]?\d+(?:\.\d+)?)",
        condition,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    error = abs(float(match.group(1)))
    return error if math.isfinite(error) else None


def classify_fact(fact: Any) -> str:
    metric = _text(getattr(fact, "metric_or_parameter", None))
    method = _text(getattr(fact, "method", None))
    category = _text(getattr(fact, "category", None))
    subject = _text(getattr(fact, "subject_text", None))
    evidence = _text(getattr(fact, "evidence_text", None))
    fact_type = _text(getattr(fact, "fact_type", None)).casefold()
    haystack = " ".join((metric, method, category)).casefold()
    metric_context = " ".join((metric, subject)).casefold()
    normalized_metric = re.sub(r"[_-]+", " ", metric).casefold()
    compact_metric = re.sub(r"[^a-z0-9]+", "", metric.casefold())
    compact_subject = re.sub(r"[^a-z0-9]+", "", subject.casefold())

    # Explicit composition/process facts are authoritative.  In particular,
    # process temperatures must not become TEM structure results merely because
    # the substring "tem" appears in "temperature".
    if fact_type == "composition":
        return DOMAIN_COMPOSITION
    if fact_type == "process":
        return DOMAIN_PROCESS
    # D/D0 is the filament-to-die diameter ratio (die swell), so it describes
    # the formed filament geometry rather than an application performance.
    if (
        compact_metric in _DIE_SWELL_RATIO_KEYS
        or compact_subject in _DIE_SWELL_RATIO_KEYS
    ):
        return DOMAIN_STRUCTURE
    # Some table extractors label apparatus dimensions as performance.  They
    # remain process/setup parameters, never material morphology.
    if _PROCESS_EQUIPMENT_DIMENSION_RE.search(normalized_metric):
        return DOMAIN_PROCESS
    # Structure wins over a persisted performance type because a legacy
    # post-processor promoted measurable structure results (diameter,
    # crystallinity, XRD) to "performance".  SEM/TEM require token boundaries;
    # all other established structure signals retain their historical matching.
    if (
        _STRUCTURE_SHORT_TECHNIQUE_RE.search(haystack)
        or any(
            term in haystack
            for term in _STRUCTURE_TERMS
            if term not in {"sem", "tem"}
        )
    ):
        return DOMAIN_STRUCTURE
    # Polymer-characteristics tables frequently carry compact LaTeX headers
    # such as M_n, Đ and f_IM.  They describe molecular distribution or recipe
    # fraction, not application performance, even when an upstream model
    # labels every numeric table column as ``performance``.
    if _POLYMER_DISTRIBUTION_METRIC_RE.search(metric_context):
        return DOMAIN_STRUCTURE
    if (
        _POLYMER_MOLE_FRACTION_METRIC_RE.search(metric_context)
        and _MOLE_FRACTION_EVIDENCE_RE.search(evidence)
    ):
        return DOMAIN_COMPOSITION
    # Trust an explicit performance type before substring fallbacks.  Metrics
    # such as ``decomposition_rate`` and ``soluble_fraction`` otherwise match
    # the composition tokens "composition" / "fraction" and are projected
    # into the material recipe even though they are measured outcomes.
    if fact_type == "performance":
        # Upstream table extraction often assigns every numeric result the
        # broad ``performance`` type.  Component content/loading efficiency
        # and TGA residue quantify the actual formulation or retained
        # component, so they belong to composition.  The full-match allowlist
        # deliberately excludes outcome names such as ``decomposition_rate``
        # and ``soluble_fraction``.
        if _COMPONENT_COMPOSITION_METRIC_RE.fullmatch(normalized_metric):
            return DOMAIN_COMPOSITION
        # Some upstream table facts use the broad "performance" type even
        # when the table column explicitly describes a recipe component.
        # Preserve those only when both the category and a token-bounded
        # composition metric agree; substring matches alone are unsafe
        # ("decomposition" contains "composition").
        if (
            category.casefold() in {"composition", "成分"}
            and _COMPOSITION_METRIC_TOKEN_RE.search(metric)
        ):
            return DOMAIN_COMPOSITION
        return DOMAIN_PERFORMANCE
    if any(term in haystack for term in _PERFORMANCE_TERMS):
        return DOMAIN_PERFORMANCE
    if any(term in haystack for term in _PROCESS_TERMS):
        return DOMAIN_PROCESS
    if any(term in haystack for term in _COMPOSITION_TERMS):
        return DOMAIN_COMPOSITION
    if fact_type == "structure":
        return DOMAIN_STRUCTURE
    return DOMAIN_PERFORMANCE


def build_material_dataset(
    *,
    records: Iterable[Any],
    papers: Iterable[Any],
    fact_candidates: Iterable[Any] = (),
    sample_catalogs: Iterable[Any] = (),
    evidence_items: Iterable[Any] = (),
    document_blocks: Iterable[Any] = (),
) -> MaterialDataset:
    records = list(records)
    papers = list(papers)
    facts = list(fact_candidates)
    catalogs = list(sample_catalogs)
    evidence_items = list(evidence_items)
    document_blocks = list(document_blocks)

    paper_ids = {
        paper.id: _paper_business_id(paper.id, records)
        for paper in papers
    }
    paper_rows = [_paper_row(paper, paper_ids[paper.id]) for paper in papers]

    sample_entries = _sample_entries(catalogs, records, paper_ids)
    sample_rows = [entry["row"] for entry in sample_entries]
    sample_lookup = {
        (entry["paper_db_id"], entry["sample_id"]): entry
        for entry in sample_entries
    }
    samples_by_paper: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entry in sample_entries:
        samples_by_paper[entry["paper_db_id"]].append(entry)

    composition_rows = _composition_rows(
        sample_entries,
        facts,
        paper_ids,
        samples_by_paper,
        document_blocks,
    )
    process_rows, process_quality_rows = _process_rows(
        sample_entries,
        facts,
        paper_ids,
        samples_by_paper,
        document_blocks,
    )
    structure_rows, performance_rows, quality_rows = _measurement_rows(
        facts=facts,
        records=records,
        paper_ids=paper_ids,
        samples_by_paper=samples_by_paper,
        evidence_items=evidence_items,
    )
    quality_rows = process_quality_rows + quality_rows

    # Keep a useful fallback for older databases where FactCandidate rows were
    # not persisted yet.
    if not facts:
        structure_rows, performance_rows, quality_rows = _record_measurement_rows(
            records,
            paper_ids,
        )

    return MaterialDataset(
        papers=paper_rows,
        samples=sample_rows,
        composition=composition_rows,
        process=process_rows,
        structure=structure_rows,
        performance=performance_rows,
        quality=quality_rows,
    )


def _paper_row(paper: Any, paper_id: str) -> dict[str, Any]:
    doi = normalize_doi(getattr(paper, "doi_or_url", ""))
    title = clean_title(getattr(paper, "paper_title", ""))
    year = getattr(paper, "year", None) or ""
    journal = _text(getattr(paper, "journal", ""))
    correction = _KNOWN_PAPER_METADATA.get(doi.casefold())
    if correction:
        title = correction["title"]
        year = correction["year"]
        journal = correction["journal"]
        status = "已按 DOI 核验"
        note = correction["note"]
    else:
        status = "待核验"
        note = ""
    return {
        "文献ID": paper_id,
        "文献标题": title,
        "DOI": doi,
        "发表年份": year,
        "期刊": journal,
        "原始文件名": _text(getattr(paper, "original_filename", "")),
        "元数据核验状态": status,
        "元数据备注": note,
    }


def _paper_business_id(paper_db_id: int, records: list[Any]) -> str:
    for record in records:
        if (
            getattr(record, "source_paper_id", None) == paper_db_id
            and _text(getattr(record, "paper_id_str", None))
        ):
            return _text(record.paper_id_str)
    return f"P{paper_db_id:04d}"


def _sample_entries(
    catalogs: list[Any],
    records: list[Any],
    paper_ids: dict[int, str],
) -> list[dict[str, Any]]:
    source: dict[tuple[int, str], dict[str, Any]] = {}
    record_by_sample: dict[tuple[int, str], Any] = {}
    for record in records:
        paper_db_id = int(getattr(record, "source_paper_id"))
        sample_name = _text(getattr(record, "sample_id", ""))
        if sample_name:
            record_by_sample.setdefault((paper_db_id, sample_name), record)

    for catalog in catalogs:
        paper_db_id = int(getattr(catalog, "paper_id"))
        sample_name = _text(getattr(catalog, "sample_id", ""))
        if not sample_name:
            continue
        source[(paper_db_id, sample_name)] = {
            "paper_db_id": paper_db_id,
            "paper_id": paper_ids.get(paper_db_id, f"P{paper_db_id:04d}"),
            "sample_id": sample_name,
            "catalog": catalog,
            "record": record_by_sample.get((paper_db_id, sample_name)),
        }

    for key, record in record_by_sample.items():
        if key not in source:
            paper_db_id, sample_name = key
            source[key] = {
                "paper_db_id": paper_db_id,
                "paper_id": paper_ids.get(paper_db_id, f"P{paper_db_id:04d}"),
                "sample_id": sample_name,
                "catalog": None,
                "record": record,
            }

    entries = sorted(
        source.values(),
        key=lambda item: (item["paper_db_id"], _natural_key(item["sample_id"])),
    )
    for entry in entries:
        catalog = entry["catalog"]
        record = entry["record"]
        aliases = _aliases(getattr(catalog, "sample_aliases", None)) if catalog else ""
        material_system = _first_nonempty(
            getattr(catalog, "material_system", None) if catalog else None,
            getattr(record, "material_system", None) if record else None,
        )
        fiber_type = _first_nonempty(
            getattr(catalog, "fiber_type", None) if catalog else None,
            getattr(record, "fiber_type", None) if record else None,
        )
        variable_name = _first_nonempty(
            getattr(catalog, "variable_name", None) if catalog else None,
            getattr(record, "variable_name", None) if record else None,
        )
        variable_value = _first_nonempty(
            getattr(catalog, "variable_value", None) if catalog else None,
            getattr(record, "variable_value", None) if record else None,
        )
        variable_unit = _first_nonempty(
            getattr(catalog, "variable_unit", None) if catalog else None,
            getattr(record, "variable_unit", None) if record else None,
        )
        expression = _first_nonempty(
            getattr(catalog, "composition_expression", None) if catalog else None,
            getattr(record, "composition_expression", None) if record else None,
        )
        matrix = _first_nonempty(
            getattr(record, "matrix_name", None) if record else None,
            _infer_matrix(material_system, expression),
        )
        entry["row"] = {
            "样品ID": entry["sample_id"],
            "文献ID": entry["paper_id"],
            "样品名称": entry["sample_id"],
            "样品别名": aliases,
            "样品组": _first_nonempty(
                getattr(catalog, "sample_group_id", None) if catalog else None,
                getattr(record, "sample_group_id", None) if record else None,
            ),
            "材料体系": material_system,
            "材料形态": fiber_type,
            "基体": matrix,
            "配方摘要": expression,
            "主要变量": variable_name,
            "变量值": variable_value,
            "变量单位": variable_unit,
            "处理状态": _treatment_state(variable_name, variable_value),
        }

    # Sparse condition-only samples inherit stable paper-level descriptors,
    # while their own identifiers and measurements remain unchanged.
    for paper_db_id in sorted({entry["paper_db_id"] for entry in entries}):
        paper_entries = [
            entry for entry in entries if entry["paper_db_id"] == paper_db_id
        ]
        defaults: dict[str, str] = {}
        for field in ("材料体系", "材料形态", "基体"):
            values = [
                entry["row"][field]
                for entry in paper_entries
                if entry["row"][field]
            ]
            defaults[field] = Counter(values).most_common(1)[0][0] if values else ""
        for entry in paper_entries:
            row = entry["row"]
            for field, value in defaults.items():
                if not row[field]:
                    row[field] = value
            sample_key = entry["sample_id"].casefold().replace("_", "")
            if "50um" in sample_key and not row["变量值"]:
                row["主要变量"] = "fiber thickness"
                row["变量值"] = "50"
                row["变量单位"] = "μm"
    return entries


def _composition_rows(
    sample_entries: list[dict[str, Any]],
    facts: list[Any],
    paper_ids: dict[int, str],
    samples_by_paper: dict[int, list[dict[str, Any]]],
    document_blocks: list[Any],
) -> list[dict[str, Any]]:
    raw_rows: list[dict[str, Any]] = []

    for entry in sample_entries:
        row = entry["row"]
        record = entry["record"]
        catalog = entry["catalog"]
        matrix = row["基体"]
        if matrix:
            matrix_amount, matrix_unit = _reported_matrix_amount(
                entry,
                document_blocks,
            )
            _append_composition(
                raw_rows,
                entry,
                role="基体",
                name=matrix,
                raw_amount=_first_nonempty(
                    getattr(record, "matrix_content", None) if record else None,
                    matrix_amount,
                ),
                unit=_first_nonempty(
                    getattr(record, "matrix_unit", None) if record else None,
                    matrix_unit,
                ),
                note="样品配方中的主体材料",
            )

        material = row["材料体系"].casefold()
        expression = row["配方摘要"].casefold()
        variable_name = row["主要变量"]
        variable_value = row["变量值"]
        variable_unit = row["变量单位"]
        treatment = row["处理状态"]

        if "phb" in material and "quercetin" in material:
            _append_composition(
                raw_rows,
                entry,
                role="功能组分",
                name="quercetin",
                raw_amount=variable_value,
                unit=variable_unit,
                note=variable_name,
            )
            if "chloroform" in expression:
                _append_composition(
                    raw_rows,
                    entry,
                    role="溶剂",
                    name="chloroform",
                    raw_amount="",
                    unit="",
                    note="纺丝或浇铸溶液",
                )
        elif "silk fibroin" in material and treatment == "EDC/NHS交联":
            _append_composition(
                raw_rows,
                entry,
                role="交联剂",
                name="EDC/NHS",
                raw_amount="",
                unit="",
                note="交联处理使用",
            )
        elif record is not None:
            additive = _sample_specific_additive(
                _text(getattr(record, "additive_expression", "")),
                entry["sample_id"],
                row["配方摘要"],
            )
            if additive and not _looks_like_multi_sample_summary(additive):
                _append_composition(
                    raw_rows,
                    entry,
                    role="添加剂或改性组分",
                    name=additive,
                    raw_amount="",
                    unit="",
                    note="",
                )
            solvent = _text(getattr(record, "solvent_or_aid", ""))
            if solvent and not _looks_like_multi_sample_summary(solvent):
                _append_composition(
                    raw_rows,
                    entry,
                    role="溶剂或助剂",
                    name=solvent,
                    raw_amount="",
                    unit="",
                    note="",
                )

        if (
            variable_name
            and variable_value
            and any(term in variable_name.casefold() for term in _COMPOSITION_TERMS)
            and not ("phb" in material and "quercetin" in material)
        ):
            _append_composition(
                raw_rows,
                entry,
                role="配方变量",
                name=variable_name,
                raw_amount=variable_value,
                unit=variable_unit,
                note="样品目录中的主变量",
            )

    for fact in facts:
        if classify_fact(fact) != DOMAIN_COMPOSITION:
            continue
        paper_db_id = int(getattr(fact, "paper_id"))
        sample_id, _ = _resolve_sample_id(fact, samples_by_paper.get(paper_db_id, []))
        raw = _text(getattr(fact, "value", ""))
        parsed = parse_numeric(raw)
        metric_key = re.sub(
            r"[\W_]+",
            "_",
            _text(getattr(fact, "metric_or_parameter", "")),
            flags=re.UNICODE,
        ).strip("_").casefold()
        if metric_key.endswith("_content"):
            composition_role = "配方组分"
        elif (
            metric_key.endswith("_loading_efficiency")
            or metric_key == "tga_residue"
        ):
            composition_role = "实测组成"
        else:
            composition_role = _first_nonempty(
                getattr(fact, "category", None),
                "组分",
            )
        raw_rows.append(
            {
                "事实ID": "",
                "文献ID": paper_ids.get(paper_db_id, f"P{paper_db_id:04d}"),
                "样品ID": sample_id,
                "组分角色": composition_role,
                "组分名称": _first_nonempty(
                    getattr(fact, "subject_text", None),
                    getattr(fact, "metric_or_parameter", None),
                ),
                "原始含量": raw,
                "数值": parsed.value,
                "误差": parsed.error,
                "下限": parsed.lower,
                "上限": parsed.upper,
                "单位": _text(getattr(fact, "unit", "")),
                "计量基准": _text(getattr(fact, "unit", "")),
                "条件或说明": _text(getattr(fact, "condition", "")),
            }
        )

    rows = _deduplicate_rows(
        raw_rows,
        key_fields=("文献ID", "样品ID", "组分角色", "组分名称", "原始含量", "单位"),
    )
    _assign_fact_ids(rows, "COM")
    return rows


def _append_composition(
    rows: list[dict[str, Any]],
    entry: dict[str, Any],
    *,
    role: str,
    name: str,
    raw_amount: str,
    unit: str,
    note: str,
) -> None:
    parsed = parse_numeric(raw_amount)
    rows.append(
        {
            "事实ID": "",
            "文献ID": entry["paper_id"],
            "样品ID": entry["sample_id"],
            "组分角色": role,
            "组分名称": name,
            "原始含量": raw_amount,
            "数值": parsed.value,
            "误差": parsed.error,
            "下限": parsed.lower,
            "上限": parsed.upper,
            "单位": unit,
            "计量基准": unit,
            "条件或说明": note,
        }
    )


def _process_rows(
    sample_entries: list[dict[str, Any]],
    facts: list[Any],
    paper_ids: dict[int, str],
    samples_by_paper: dict[int, list[dict[str, Any]]],
    document_blocks: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_rows: list[dict[str, Any]] = []
    atomic_process_keys: set[tuple[int, str]] = set()
    for fact in facts:
        if classify_fact(fact) != DOMAIN_PROCESS:
            continue
        paper_db_id = int(getattr(fact, "paper_id"))
        sample_id, _ = _resolve_sample_id(
            fact,
            samples_by_paper.get(paper_db_id, []),
        )
        if sample_id:
            atomic_process_keys.add((paper_db_id, sample_id))

    for entry in sample_entries:
        catalog = entry["catalog"]
        record = entry["record"]
        expression = entry["row"]["配方摘要"]
        has_atomic_process = (
            entry["paper_db_id"],
            entry["sample_id"],
        ) in atomic_process_keys
        route = _first_nonempty(
            getattr(catalog, "process_route", None) if catalog else None,
            getattr(record, "process_route", None) if record else None,
        )
        route = _sample_specific_process_route(route, entry["sample_id"])
        method = _first_nonempty(
            getattr(record, "spinning_method", None) if record else None,
            _infer_forming_method(expression),
        )
        method = _normalize_forming_method(method)
        sequence = 1
        if method and not has_atomic_process:
            raw_rows.append(
                _process_row(
                    entry,
                    sequence=sequence,
                    stage="成形",
                    method=method,
                    parameter="成形方法",
                    raw_value=method,
                    unit="",
                    condition="",
                )
            )
            sequence += 1
        if route and not has_atomic_process:
            raw_rows.append(
                _process_row(
                    entry,
                    sequence=sequence,
                    stage="总体路线",
                    method=method,
                    parameter="制备路线",
                    raw_value=route,
                    unit="",
                    condition="",
                )
            )
            sequence += 1

        parameter_summary = _text(
            getattr(record, "process_parameters", "") if record else ""
        )
        if (
            parameter_summary
            and not has_atomic_process
            and not _looks_like_multi_sample_summary(parameter_summary)
        ):
            for item in re.split(r"[;；]", parameter_summary):
                item = item.strip()
                if not item:
                    continue
                if "=" in item:
                    parameter, raw_value = item.split("=", 1)
                    parameter = parameter.strip().replace("_", " ")
                    raw_value = raw_value.strip()
                else:
                    parameter = "工艺参数摘要"
                    raw_value = item
                raw_rows.append(
                    _process_row(
                        entry,
                        sequence=sequence,
                        stage="工艺参数",
                        method=method or route,
                        parameter=parameter,
                        raw_value=raw_value,
                        unit="",
                        condition="",
                    )
                )
                sequence += 1

        post_treatment = _text(
            getattr(record, "post_treatment", "") if record else ""
        )
        if (
            post_treatment
            and not has_atomic_process
            and not _looks_like_multi_sample_summary(post_treatment)
        ):
            raw_rows.append(
                _process_row(
                    entry,
                    sequence=sequence,
                    stage="后处理",
                    method=post_treatment,
                    parameter="后处理条件",
                    raw_value=post_treatment,
                    unit="",
                    condition="",
                )
            )
            sequence += 1

        variable_value = entry["row"]["变量值"].casefold()
        if "uncrosslinked" in variable_value:
            raw_rows.append(
                _process_row(
                    entry,
                    sequence=sequence,
                    stage="后处理",
                    method="原始态",
                    parameter="处理状态",
                    raw_value="未交联（原始电纺态）",
                    unit="",
                    condition="",
                )
            )
        elif "ethanol" in variable_value:
            raw_rows.append(
                _process_row(
                    entry,
                    sequence=sequence,
                    stage="后处理",
                    method="乙醇处理",
                    parameter="处理方式",
                    raw_value="乙醇处理",
                    unit="",
                    condition="",
                )
            )
        elif "crosslinked" in variable_value:
            raw_rows.append(
                _process_row(
                    entry,
                    sequence=sequence,
                    stage="交联",
                    method="EDC/NHS 交联",
                    parameter="交联方式",
                    raw_value="EDC/NHS 交联",
                    unit="",
                    condition="交联后去离子水洗涤",
                )
            )

    for fact in facts:
        if classify_fact(fact) != DOMAIN_PROCESS:
            continue
        paper_db_id = int(getattr(fact, "paper_id"))
        sample_id, _ = _resolve_sample_id(fact, samples_by_paper.get(paper_db_id, []))
        raw = _text(getattr(fact, "value", ""))
        parsed = parse_numeric(raw)
        raw_rows.append(
            {
                "事实ID": "",
                "文献ID": paper_ids.get(paper_db_id, f"P{paper_db_id:04d}"),
                "样品ID": sample_id,
                "工序序号": "",
                "工艺阶段": _first_nonempty(getattr(fact, "category", None), "工艺参数"),
                "工艺方法": _text(getattr(fact, "method", "")),
                "参数名称": _text(getattr(fact, "metric_or_parameter", "")),
                "原始值": raw,
                "数值": parsed.value,
                "误差": parsed.error,
                "下限": parsed.lower,
                "上限": parsed.upper,
                "单位": _text(getattr(fact, "unit", "")),
                "设备或条件": _text(getattr(fact, "condition", "")),
            }
        )

    raw_rows.extend(
        _curated_process_rows(
            sample_entries=sample_entries,
            document_blocks=document_blocks,
        )
    )
    rows = _deduplicate_rows(
        raw_rows,
        key_fields=("文献ID", "样品ID", "工艺阶段", "参数名称", "原始值", "单位"),
    )
    _assign_fact_ids(rows, "PRO")
    quality_rows: list[dict[str, Any]] = []
    for row in rows:
        evidence = row.pop("_evidence", "")
        if not evidence:
            continue
        quality_rows.append(
            {
                "事实ID": row["事实ID"],
                "事实类别": DOMAIN_PROCESS,
                "文献ID": row["文献ID"],
                "样品ID": row["样品ID"],
                "原始事实ID": "",
                "证据原文": evidence,
                "页码": row.pop("_source_page", ""),
                "来源位置": row.pop("_source_location", ""),
                "来源块": row.pop("_source_block_id", ""),
                "置信度": 1.0,
                "样品分配状态": "按样品目录关联",
                "复核状态": "待审核",
                "质控备注": "从实验部分原文拆分为原子工艺参数",
            }
        )
    for row in rows:
        row.pop("_source_page", None)
        row.pop("_source_location", None)
        row.pop("_source_block_id", None)
    return rows, quality_rows


def _process_row(
    entry: dict[str, Any],
    *,
    sequence: int,
    stage: str,
    method: str,
    parameter: str,
    raw_value: str,
    unit: str,
    condition: str,
) -> dict[str, Any]:
    parsed = parse_numeric(raw_value)
    return {
        "事实ID": "",
        "文献ID": entry["paper_id"],
        "样品ID": entry["sample_id"],
        "工序序号": sequence,
        "工艺阶段": stage,
        "工艺方法": method,
        "参数名称": parameter,
        "原始值": raw_value,
        "数值": parsed.value,
        "误差": parsed.error,
        "下限": parsed.lower,
        "上限": parsed.upper,
        "单位": unit,
        "设备或条件": condition,
    }


def _curated_process_rows(
    *,
    sample_entries: list[dict[str, Any]],
    document_blocks: list[Any],
) -> list[dict[str, Any]]:
    """Split high-value experimental paragraphs into atomic process rows.

    This is a conservative text-pattern projection over the locally parsed
    source.  A row is created only when the complete supporting paragraph is
    present, and that paragraph is carried into the quality worksheet.
    """

    blocks_by_paper: dict[int, list[Any]] = defaultdict(list)
    for block in document_blocks:
        blocks_by_paper[int(getattr(block, "paper_id"))].append(block)
    entries_by_paper: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entry in sample_entries:
        entries_by_paper[entry["paper_db_id"]].append(entry)

    rows: list[dict[str, Any]] = []

    def find_block(paper_id: int, needle: str) -> Any | None:
        needle = needle.casefold()
        for block in blocks_by_paper.get(paper_id, []):
            if needle in _block_text(block).casefold():
                return block
        return None

    def add(
        entry: dict[str, Any],
        block: Any,
        *,
        sequence: int,
        stage: str,
        method: str,
        parameter: str,
        raw_value: str,
        unit: str,
        condition: str = "",
    ) -> None:
        row = _process_row(
            entry,
            sequence=sequence,
            stage=stage,
            method=method,
            parameter=parameter,
            raw_value=raw_value,
            unit=unit,
            condition=condition,
        )
        row.update(
            {
                "_evidence": _block_text(block),
                "_source_page": getattr(block, "page_number", ""),
                "_source_location": _first_nonempty(
                    getattr(block, "section_name", None),
                    f"page {getattr(block, 'page_number', '')}",
                ),
                "_source_block_id": _text(getattr(block, "block_id", "")),
            }
        )
        rows.append(row)

    for paper_id, entries in entries_by_paper.items():
        electrospinning = find_block(
            paper_id,
            "mass flow rate of solution was 0.45",
        )
        if electrospinning is not None:
            shared_parameters = [
                (1, "溶液配制", "电纺液浓度", "0.8", "wt.%", ""),
                (
                    1,
                    "溶液配制",
                    "溶剂",
                    "hexafluoroisopropanol (HFIP)",
                    "",
                    "",
                ),
                (1, "溶液配制", "溶解时间", "24", "h", ""),
                (2, "电纺", "施加电压", "8", "kV", ""),
                (2, "电纺", "针头—收集板距离", "10", "cm", ""),
                (2, "电纺", "溶液流量", "0.45", "mL/h", ""),
            ]
            for entry in entries:
                if "silk fibroin" not in entry["row"]["材料体系"].casefold():
                    continue
                for sequence, stage, parameter, raw, unit, condition in shared_parameters:
                    add(
                        entry,
                        electrospinning,
                        sequence=sequence,
                        stage=stage,
                        method="electrospinning",
                        parameter=parameter,
                        raw_value=raw,
                        unit=unit,
                        condition=condition,
                    )

        crosslinking = find_block(
            paper_id,
            "these samples were crosslinked for 48 h",
        )
        if crosslinking is not None:
            crosslink_parameters = [
                (3, "交联", "交联液浓度", "7.5", "wt.%"),
                (3, "交联", "EDC/NHS质量比", "2:1", ""),
                (3, "交联", "乙醇/水体积比", "95:5", ""),
                (3, "交联", "交联液混合时间", "5", "min"),
                (3, "交联", "交联时间", "48", "h"),
                (4, "干燥", "真空干燥温度", "40", "°C"),
                (4, "干燥", "真空干燥时间", "24", "h"),
            ]
            for entry in entries:
                if entry["row"]["处理状态"] != "EDC/NHS交联":
                    continue
                for sequence, stage, parameter, raw, unit in crosslink_parameters:
                    add(
                        entry,
                        crosslinking,
                        sequence=sequence,
                        stage=stage,
                        method="EDC/NHS/ethanol crosslinking",
                        parameter=parameter,
                        raw_value=raw,
                        unit=unit,
                        condition="交联后去离子水洗涤",
                    )

        phb_solution = find_block(
            paper_id,
            "until the concentration of the solution reached the desired value of 8",
        )
        phb_homogenization = find_block(
            paper_id,
            "homogenization required 3 h",
        )
        phb_spinning_rate = find_block(
            paper_id,
            "0.08 ml/min spinning rate",
        )
        phb_release = find_block(
            paper_id,
            "20 mg of polymer",
        )
        fiber_entries = [
            entry
            for entry in entries
            if "phb" in entry["row"]["材料体系"].casefold()
            and entry["row"]["材料形态"].casefold() != "film"
        ]
        if phb_solution is not None:
            solution_parameters = [
                (1, "溶液配制", "初始聚合物浓度", "5", "m/m%"),
                (1, "溶液配制", "目标纺丝液浓度", "8", "m/m%"),
                (1, "溶液配制", "回流温度", "62", "°C"),
                (1, "溶液配制", "回流时间", "8", "h"),
                (1, "溶液配制", "静置除杂时间", "48", "h"),
                (1, "浓缩", "浓缩浴温度", "40", "°C"),
                (1, "浓缩", "蒸发时间", "2", "h"),
            ]
            for entry in fiber_entries:
                for sequence, stage, parameter, raw, unit in solution_parameters:
                    add(
                        entry,
                        phb_solution,
                        sequence=sequence,
                        stage=stage,
                        method="wet spinning solution preparation",
                        parameter=parameter,
                        raw_value=raw,
                        unit=unit,
                    )
        if phb_homogenization is not None:
            for entry in fiber_entries:
                for parameter, raw, unit in (
                    ("槲皮素均化时间", "3", "h"),
                    ("均化搅拌转速", "300", "rpm"),
                ):
                    add(
                        entry,
                        phb_homogenization,
                        sequence=1,
                        stage="溶液配制",
                        method="continuous stirring",
                        parameter=parameter,
                        raw_value=raw,
                        unit=unit,
                    )
        if phb_spinning_rate is not None:
            targets = [
                entry
                for entry in fiber_entries
                if entry["sample_id"] == "PHB_quercetin_fiber"
            ] or fiber_entries[:1]
            for entry in targets:
                add(
                    entry,
                    phb_spinning_rate,
                    sequence=2,
                    stage="湿法纺丝",
                    method="wet spinning",
                    parameter="纺丝流量",
                    raw_value="0.08",
                    unit="mL/min",
                    condition="Figure 1 示例条件",
                )
        if phb_release is not None:
            targets = [
                entry
                for entry in fiber_entries
                if entry["sample_id"] in {
                    "PHB_quercetin_fiber",
                    "PHB_quercetin_fiber_50um",
                }
            ]
            for entry in targets:
                for parameter, raw, unit in (
                    ("测试纤维质量", "20", "mg"),
                    ("释放介质体积", "100", "mL"),
                    ("释放介质搅拌转速", "50", "rpm"),
                    ("取样间隔", "5", "min"),
                ):
                    add(
                        entry,
                        phb_release,
                        sequence=3,
                        stage="释放性能测试",
                        method="UV–Vis release test",
                        parameter=parameter,
                        raw_value=raw,
                        unit=unit,
                        condition="释放介质：ethanol",
                    )
    return rows


def _reported_matrix_amount(
    entry: dict[str, Any],
    document_blocks: list[Any],
) -> tuple[str, str]:
    paper_id = entry["paper_db_id"]
    context = "\n".join(
        _block_text(block)
        for block in document_blocks
        if int(getattr(block, "paper_id")) == paper_id
    ).casefold()
    material = entry["row"]["材料体系"].casefold()
    form = entry["row"]["材料形态"].casefold()
    if "silk fibroin" in material and "0.8 wt.% tsf" in context:
        return "0.8", "wt.%"
    if (
        "phb" in material
        and form != "film"
        and "desired value of 8 m/m%" in context
    ):
        return "8", "m/m%"
    return "", ""


def _block_text(block: Any) -> str:
    value = _first_nonempty(
        getattr(block, "text", None),
        getattr(block, "html", None),
    )
    cleaned = html.unescape(_text(value))
    cleaned = re.sub(r"<\s*/?\s*sup\s*>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _measurement_rows(
    *,
    facts: list[Any],
    records: list[Any],
    paper_ids: dict[int, str],
    samples_by_paper: dict[int, list[dict[str, Any]]],
    evidence_items: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    review_lookup = _review_lookup(records)
    evidence_by_id = {
        int(item.id): item for item in evidence_items if getattr(item, "id", None) is not None
    }
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for fact in facts:
        domain = classify_fact(fact)
        if domain not in (DOMAIN_STRUCTURE, DOMAIN_PERFORMANCE):
            continue
        paper_db_id = int(getattr(fact, "paper_id"))
        sample_id, correction_note = _resolve_sample_id(
            fact,
            samples_by_paper.get(paper_db_id, []),
        )
        raw_metric = _text(getattr(fact, "metric_or_parameter", ""))
        raw_value = _text(getattr(fact, "value", ""))
        unit = _text(getattr(fact, "unit", ""))
        normalized_metric = _metric_label(raw_metric)
        parsed_for_key = parse_numeric(raw_value)
        value_key = (
            f"{parsed_for_key.value:.15g}"
            if parsed_for_key.value is not None
            else _norm(raw_value)
        )
        key = (
            paper_db_id,
            _norm(sample_id),
            domain,
            _norm(normalized_metric),
            value_key,
            _norm(unit),
        )
        grouped[key].append(
            {
                "fact": fact,
                "paper_db_id": paper_db_id,
                "paper_id": paper_ids.get(paper_db_id, f"P{paper_db_id:04d}"),
                "sample_id": sample_id,
                "domain": domain,
                "metric": normalized_metric,
                "raw_metric": raw_metric,
                "raw_value": raw_value,
                "unit": unit,
                "correction_note": correction_note,
            }
        )

    canonical_groups = sorted(
        grouped.values(),
        key=lambda group: (
            group[0]["paper_db_id"],
            _natural_key(group[0]["sample_id"]),
            group[0]["domain"],
            _natural_key(group[0]["metric"]),
            _natural_key(group[0]["raw_value"]),
        ),
    )
    structure_rows: list[dict[str, Any]] = []
    performance_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    counters = {DOMAIN_STRUCTURE: 0, DOMAIN_PERFORMANCE: 0}

    for group in canonical_groups:
        best = max(group, key=lambda item: _fact_quality_score(item["fact"]))
        domain = best["domain"]
        counters[domain] += 1
        prefix = "STR" if domain == DOMAIN_STRUCTURE else "PER"
        fact_id = f"F-{best['paper_id']}-{prefix}-{counters[domain]:04d}"
        parsed = parse_numeric(best["raw_value"])
        source_fact = best["fact"]
        condition_error = _standard_deviation_from_condition(
            getattr(source_fact, "condition", ""),
        )
        common = {
            "事实ID": fact_id,
            "文献ID": best["paper_id"],
            "样品ID": best["sample_id"],
            "指标名称": best["metric"],
            "原始指标名": best["raw_metric"],
            "原始值": best["raw_value"],
            "数值": parsed.value,
            "误差": (
                parsed.error
                if parsed.error is not None
                else condition_error
            ),
            "下限": parsed.lower,
            "上限": parsed.upper,
            "单位": best["unit"],
        }
        if domain == DOMAIN_STRUCTURE:
            structure_rows.append(
                {
                    **common,
                    "结构类别": _structure_category(
                        f"{best['metric']} {best['raw_metric']}"
                    ),
                    "表征方法": _normalized_method(
                        best["raw_metric"],
                        _text(getattr(source_fact, "method", "")),
                    ),
                    "测试条件": _text(getattr(source_fact, "condition", "")),
                }
            )
        else:
            performance_rows.append(
                {
                    **common,
                    "性能类别": _performance_category(
                        best["raw_metric"],
                        _text(getattr(source_fact, "category", "")),
                    ),
                    "测试方法": _text(getattr(source_fact, "method", "")),
                    "测试条件": _clean_condition(
                        _text(getattr(source_fact, "condition", ""))
                    ),
                }
            )

        duplicate_note = ""
        if len(group) > 1:
            duplicate_note = f"同一事实的 {len(group)} 条重复抽取已合并"
        for member in group:
            fact = member["fact"]
            evidence_item = evidence_by_id.get(
                int(getattr(fact, "evidence_item_id", 0) or 0)
            )
            review_status = _matched_review_status(
                review_lookup,
                member["paper_db_id"],
                member["sample_id"],
                member["raw_metric"],
                member["raw_value"],
                getattr(fact, "assignment_status", ""),
                getattr(fact, "confidence", 0.0),
            )
            notes = [
                value
                for value in (member["correction_note"], duplicate_note)
                if value
            ]
            quality_rows.append(
                {
                    "事实ID": fact_id,
                    "事实类别": domain,
                    "文献ID": member["paper_id"],
                    "样品ID": member["sample_id"],
                    "原始事实ID": _text(getattr(fact, "fact_id", "")),
                    "证据原文": _first_nonempty(
                        getattr(fact, "evidence_text", None),
                        getattr(evidence_item, "evidence_text", None)
                        if evidence_item is not None
                        else None,
                    ),
                    "页码": _first_nonempty(
                        getattr(fact, "source_page", None),
                        getattr(evidence_item, "page_start", None)
                        if evidence_item is not None
                        else None,
                    ),
                    "来源位置": _first_nonempty(
                        getattr(fact, "source_location", None),
                        getattr(evidence_item, "source_location", None)
                        if evidence_item is not None
                        else None,
                    ),
                    "来源块": _first_nonempty(
                        getattr(fact, "source_block_id", None),
                        getattr(evidence_item, "block_id", None)
                        if evidence_item is not None
                        else None,
                    ),
                    "置信度": getattr(fact, "confidence", None),
                    "样品分配状态": _text(
                        getattr(fact, "assignment_status", "")
                    ),
                    "复核状态": review_status,
                    "质控备注": "；".join(notes),
                }
            )

    return structure_rows, performance_rows, quality_rows


def _record_measurement_rows(
    records: list[Any],
    paper_ids: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pseudo_facts: list[Any] = []
    for record in records:
        metric = _text(getattr(record, "performance_metric", ""))
        value = _text(getattr(record, "performance_value", ""))
        if not metric and not value:
            continue
        pseudo_facts.append(
            _RecordFact(record)
        )
    return _measurement_rows(
        facts=pseudo_facts,
        records=records,
        paper_ids=paper_ids,
        samples_by_paper={},
        evidence_items=[],
    )


class _RecordFact:
    def __init__(self, record: Any):
        self.paper_id = record.source_paper_id
        self.fact_id = record.record_id
        self.fact_type = "performance"
        self.assigned_sample_id = record.sample_id
        self.metric_or_parameter = record.performance_metric
        self.value = record.performance_value
        self.unit = record.performance_unit
        self.method = record.performance_method
        self.condition = record.performance_condition
        self.category = record.performance_category
        self.evidence_text = record.evidence_text
        self.source_location = record.source_location
        self.source_block_id = None
        self.source_page = None
        self.evidence_item_id = None
        self.extraction_method = record.extraction_method
        self.confidence = record.ai_confidence or 0.0
        self.assignment_status = "assigned"


def _resolve_sample_id(
    fact: Any,
    samples: list[dict[str, Any]],
) -> tuple[str, str]:
    assigned = _text(getattr(fact, "assigned_sample_id", ""))
    if not samples:
        return assigned or "未指定样品", ""

    metric = _text(getattr(fact, "metric_or_parameter", "")).casefold()
    value = _text(getattr(fact, "value", "")).strip()
    material_context = " ".join(
        entry["row"]["材料体系"] for entry in samples
    ).casefold()
    fact_context = " ".join(
        (
            _text(getattr(fact, "subject_text", "")),
            _text(getattr(fact, "condition", "")),
            _text(getattr(fact, "evidence_text", "")),
        )
    ).casefold()
    compact_context = (
        fact_context.replace("μ", "u")
        .replace("µ", "u")
        .replace(" ", "")
        .replace("_", "")
    )

    if "diffusion" in metric and "50um" in compact_context:
        for entry in samples:
            compact_sample = (
                entry["sample_id"].casefold().replace("_", "").replace("μ", "u")
            )
            if "50um" in compact_sample:
                resolved = entry["sample_id"]
                if resolved != assigned:
                    return (
                        resolved,
                        f"按原文 50 μm 纤维条件修正样品归属：{assigned or '未指定'} → {resolved}",
                    )
                return resolved, ""

    # The source paper reports the three SEM means in one sentence.  The
    # extraction stage duplicated the values under multiple samples, so the
    # deterministic value-to-treatment relationship is used for the curated
    # export while the correction remains visible in the quality sheet.
    if "diameter" in metric and "silk fibroin" in material_context:
        treatment_by_value = {
            "611": "uncrosslinked",
            "787": "ethanol",
            "841": "crosslinked",
        }
        target = treatment_by_value.get(value)
        if target:
            for entry in samples:
                variable = entry["row"]["变量值"].casefold()
                if target in variable:
                    resolved = entry["sample_id"]
                    if resolved != assigned:
                        return (
                            resolved,
                            f"按原文直径—处理条件关系修正样品归属：{assigned or '未指定'} → {resolved}",
                        )
                    return resolved, ""

    if assigned and any(entry["sample_id"] == assigned for entry in samples):
        return assigned, ""

    haystack = fact_context
    scored: list[tuple[int, str]] = []
    for entry in samples:
        tokens = [
            entry["sample_id"],
            entry["row"]["样品别名"],
            entry["row"]["变量值"],
        ]
        score = sum(
            2
            for token in tokens
            if token and _text(token).casefold() in haystack
        )
        scored.append((score, entry["sample_id"]))
    score, resolved = max(scored, default=(0, "未指定样品"))
    if score > 0:
        note = ""
        if assigned and assigned != resolved:
            note = f"按原文样品别名修正样品归属：{assigned} → {resolved}"
        return resolved, note
    return assigned or "未指定样品", ""


def _review_lookup(records: list[Any]) -> dict[tuple[Any, ...], str]:
    lookup: dict[tuple[Any, ...], str] = {}
    for record in records:
        key = (
            int(getattr(record, "source_paper_id")),
            _norm(getattr(record, "sample_id", "")),
            _norm(getattr(record, "performance_metric", "")),
            _norm(getattr(record, "performance_value", "")),
        )
        lookup[key] = _text(getattr(record, "review_status", ""))
    return lookup


def _matched_review_status(
    lookup: dict[tuple[Any, ...], str],
    paper_id: int,
    sample_id: str,
    metric: str,
    value: str,
    assignment_status: Any,
    confidence: Any,
) -> str:
    exact = lookup.get(
        (paper_id, _norm(sample_id), _norm(metric), _norm(value))
    )
    if exact:
        return exact
    if _text(assignment_status) in {"uncertain", "multiple", "unassigned"}:
        return "存疑"
    try:
        if float(confidence or 0) < 0.7:
            return "存疑"
    except (TypeError, ValueError):
        return "存疑"
    return "待审核"


def _fact_quality_score(fact: Any) -> tuple[int, int, float]:
    return (
        int(bool(_text(getattr(fact, "method", "")))),
        int(bool(_text(getattr(fact, "evidence_text", "")))),
        float(getattr(fact, "confidence", 0.0) or 0.0),
    )


def _metric_label(metric: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", metric.casefold()).strip("_")
    if key in _METRIC_LABELS:
        return _METRIC_LABELS[key]
    xrd = re.fullmatch(r"xrd_peak_(\d+)", key)
    if xrd:
        return f"XRD衍射峰{xrd.group(1)}"
    cleaned = metric.replace("_", " ").strip()
    return cleaned or "未命名指标"


def _structure_category(metric: str) -> str:
    value = metric.casefold()
    if any(term in value for term in ("diameter", "morpholog", "sem", "tem", "pore")):
        return "形貌与尺寸"
    if any(
        term in value
        for term in (
            "beta_sheet",
            "random_coil",
            "helix",
            "turn",
            "secondary_structure",
            "折叠",
            "卷曲",
            "螺旋",
            "转角",
        )
    ):
        return "二级结构"
    if any(term in value for term in ("xrd", "diffraction", "crystall", "beta_phase")):
        return "晶体结构"
    if any(term in value for term in ("spectroscopy", "ftir", "raman")):
        return "光谱特征"
    return "结构表征"


def _normalized_method(metric: str, method: str) -> str:
    value = f"{metric} {method}".casefold()
    if "diameter" in value and "uv" in method.casefold():
        return "文献图注报告（纤维直径）"
    return method


def _performance_category(metric: str, category: str) -> str:
    value = f"{metric} {category}".casefold()
    if any(term in value for term in ("strength", "modulus", "elongation", "toughness")):
        return "力学性能"
    if any(term in value for term in ("diffusion", "release", "permeab")):
        return "传输与释放性能"
    if "thermal" in value or "temperature" in value:
        return "热性能"
    if any(term in value for term in ("conductiv", "resistiv", "permittiv", "piezo")):
        return "电学与功能性能"
    if "density" in value or "solubility" in value:
        return "物理性能"
    return category or "其他性能"


def _infer_matrix(material_system: str, expression: str) -> str:
    text = f"{material_system} {expression}".casefold()
    # Prefer an explicit polymer-matrix role over slash ordering.  Composite
    # names commonly list the reinforcement first (AgNFP/PU, silk/epoxy), so
    # blindly taking the first component reverses matrix and filler.
    if re.search(r"\b(?:polyurethane|pu)\s+(?:polymer\s+)?matrix\b", text):
        return "polyurethane (PU)"
    if re.search(r"\bepoxy(?:\s+resin)?\s+matrix\b", text):
        return "epoxy resin"
    if "epoxy" in text:
        return "epoxy resin"
    if re.search(r"\b(?:polyurethane|pu)\b", text):
        return "polyurethane (PU)"
    if "polycaprolactone" in text or re.search(r"\bpcl\b", text):
        return "polycaprolactone (PCL)"
    if "polyvinyl alcohol" in text or re.search(r"\bpva\b", text):
        return "polyvinyl alcohol (PVA)"
    if "polyvinylidene fluoride" in text or re.search(r"\bpvdf\b", text):
        return "polyvinylidene fluoride (PVDF)"
    if "silk fibroin" in text or "tsf" in text:
        return "silk fibroin (TSF)"
    if "poly(3-hydroxybutyrate)" in text or "phb" in text:
        return "poly(3-hydroxybutyrate) (PHB)"
    if "/" in material_system:
        return material_system.split("/", 1)[0].strip()
    return material_system.strip()


def _infer_forming_method(expression: str) -> str:
    value = expression.casefold()
    if "wet-spun" in value or "wet spun" in value:
        return "wet spinning"
    if "solvent-cast" in value or "solvent cast" in value:
        return "solvent casting"
    if "electrospun" in value or "electrospinning" in value:
        return "electrospinning"
    return ""


def _normalize_forming_method(value: Any) -> str:
    """Turn negative fiber placeholders into the actual forming route."""

    text = _text(value)
    if not text:
        return ""
    match = re.fullmatch(
        r"(?i)not\s+applicable\s*[—–-]\s*this\s+is\s+(?:an?\s+)?"
        r"(.+?\broute),?\s+not\s+(?:an?\s+)?fiber\s+spinning\s+process\.?",
        text,
    )
    if match:
        return match.group(1).strip()
    if re.match(r"(?i)^not\s+applicable\b", text):
        return ""
    return text


def _sample_specific_process_route(value: Any, sample_id: str) -> str:
    """Remove explicitly named preparation clauses for a different sample."""

    text = _text(value)
    if not text:
        return ""
    normalized_sample = re.sub(r"\s+", "", sample_id).casefold()
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        named = re.match(
            r"(?i)(?:for\s+)?([a-z][a-z0-9()/.+-]{2,})\s+"
            r"(?:was\s+)?prepared\b",
            sentence.strip(),
        )
        if named:
            normalized_named = re.sub(
                r"\s+",
                "",
                named.group(1),
            ).casefold()
            if normalized_named != normalized_sample:
                continue
        if sentence.strip():
            kept.append(sentence.strip())
    return " ".join(kept)


def _treatment_state(variable_name: str, variable_value: str) -> str:
    name = variable_name.casefold()
    value = variable_value.casefold()
    if "treatment" not in name:
        return ""
    if "uncrosslinked" in value:
        return "未交联"
    if "ethanol" in value:
        return "乙醇处理"
    if "crosslinked" in value:
        return "EDC/NHS交联"
    return variable_value


def _clean_condition(value: str) -> str:
    if not value:
        return ""
    parts = [
        part.strip()
        for part in value.split(";")
        if part.strip()
        and not part.strip().startswith(
            (
                "checklist",
                "export_tier",
                "metric_unit_mismatch",
            )
        )
    ]
    return "; ".join(parts)


def _deduplicate_rows(
    rows: list[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(_norm(row.get(field)) for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    result.sort(
        key=lambda row: (
            _natural_key(row.get("文献ID", "")),
            _natural_key(row.get("样品ID", "")),
            _natural_key(next(
                (
                    row.get(field, "")
                    for field in (
                        "组分角色",
                        "工艺阶段",
                        "指标名称",
                        "参数名称",
                    )
                    if row.get(field)
                ),
                "",
            )),
        )
    )
    return result


def _assign_fact_ids(rows: list[dict[str, Any]], prefix: str) -> None:
    counters: dict[str, int] = defaultdict(int)
    for row in rows:
        paper_id = _text(row.get("文献ID")) or "P0000"
        counters[paper_id] += 1
        row["事实ID"] = f"F-{paper_id}-{prefix}-{counters[paper_id]:04d}"


def _aliases(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, list):
        return "；".join(_text(item) for item in parsed if _text(item))
    return raw


def _looks_like_multi_sample_summary(value: str) -> bool:
    lowered = value.casefold()
    if re.search(
        r"\b(?:varying|various|different)\s+"
        r"(?:amounts?|loadings?|ratios?|concentrations?|contents?)\b",
        lowered,
    ):
        return True
    if re.search(r"(?:^|[.;]\s*)for\s+r-[a-z0-9(]", lowered):
        return True
    return (
        ";" in value
        and sum(
            token in lowered
            for token in ("ethanol", "crosslink", "uncrosslink", "edc/nhs")
        )
        >= 2
    )


_TARGETED_ADDITION_RE = re.compile(
    r"\b(?:inject(?:ed|ion)?|fill(?:ed|ing)?|load(?:ed|ing)?|"
    r"coat(?:ed|ing)?|deposit(?:ed|ion)?)\b",
    flags=re.IGNORECASE,
)
_DISTINCT_COMPONENT_LABEL_RE = re.compile(
    r"\b(?:[A-Z]{2,}[A-Za-z0-9-]*|"
    r"[A-Za-z0-9-]*[A-Z][A-Za-z0-9-]*[A-Z][A-Za-z0-9-]*)\b"
)


def _sample_specific_additive(
    value: str,
    sample_id: str,
    composition_expression: str,
) -> str:
    """Drop targeted additions that are not named by the current sample.

    Holistic extraction may persist a paper-level additive summary on every
    legacy candidate record.  A clause such as ``EGaIn ... injected into
    hollow fiber cores`` belongs only to the EGaIn-filled variant.  We retain
    ordinary shared additives, and scope only action-qualified clauses that
    carry a distinctive material label absent from the sample identity.
    """

    context = f"{sample_id} {composition_expression}".casefold()
    kept: list[str] = []
    for raw_part in re.split(r"\s*;\s*", value):
        part = raw_part.strip()
        if not part:
            continue
        if _TARGETED_ADDITION_RE.search(part):
            labels = {
                label.casefold()
                for label in _DISTINCT_COMPONENT_LABEL_RE.findall(part)
                if len(label) >= 3
            }
            if labels and not any(label in context for label in labels):
                continue
        kept.append(part)
    return "; ".join(kept)


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
        else:
            return value
    return ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).casefold()


def _natural_key(value: Any) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", _text(value))
    )

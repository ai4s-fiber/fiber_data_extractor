"""Flat material-fact adapter for the New Materials Data Center.

The legacy platform binding stores one sample per record and puts composition,
process, structure, and performance facts in nested arrays.  The platform's
Excel exporter does not preserve those arrays reliably.  This v0.2 adapter
therefore emits one *atomic material fact* per platform record and keeps every
business field in the record's flat ``object`` payload.

The module is deliberately independent from ``platform_batch_adapter``.  It can
be introduced beside the verified v1 delivery path without changing it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


MATERIAL_FACT_SCHEMA_VERSION = "ai4s_material_fact_v0.2"
JAVASCRIPT_MAX_SAFE_INTEGER = 9_007_199_254_740_991

DOMAIN_LABELS = ("成分", "工艺", "结构", "性能")
DOMAIN_ORDER = {label: index for index, label in enumerate(DOMAIN_LABELS)}

MATERIAL_FACT_FIELDS = (
    "事实记录键",
    "文献编号",
    "文献标题",
    "DOI或链接",
    "发表年份",
    "期刊",
    "样品编号",
    "样品组编号",
    "样品别名",
    "材料体系",
    "材料形态",
    "事实类别",
    "组分角色",
    "工艺阶段",
    "指标类别",
    "指标或参数名称",
    "原始值",
    "数值",
    "最小值",
    "最大值",
    "单位",
    "方法或设备",
    "条件或说明",
    "证据原文",
    "来源位置",
    "置信度",
)

REQUIRED_FIELDS = {
    "事实记录键",
    "文献编号",
    "样品编号",
    "事实类别",
    "指标或参数名称",
    "原始值",
}

FIELD_TYPES = {
    **{name: 1 for name in MATERIAL_FACT_FIELDS},
    "发表年份": 2,
    "事实类别": 6,
    "数值": 2,
    "最小值": 2,
    "最大值": 2,
    "置信度": 2,
}

PAPER_FIELD_ALIASES = {
    "paper_id": ("paper_id", "id", "文献编号"),
    "title": ("title", "paper_title", "文献标题"),
    "doi": ("doi_or_url", "doi", "url", "DOI或链接"),
    "year": ("year", "publication_year", "发表年份"),
    "journal": ("journal", "期刊"),
}

SAMPLE_FIELD_ALIASES = {
    "sample_id": ("sample_id", "sample_code", "样品编号"),
    "group_id": ("sample_group_id", "group_id", "样品组编号"),
    "aliases": ("aliases", "sample_aliases", "样品别名"),
    "material_system": ("material_system", "材料体系"),
    "material_form": (
        "material_form",
        "fiber_type",
        "sample_form",
        "form",
        "材料形态",
    ),
}

PAPER_PROJECTION_PATHS = {
    "paper.metadata.title": "title",
    "paper.metadata.doi_or_url": "doi_or_url",
    "paper.metadata.year": "year",
    "paper.metadata.journal": "journal",
}

SAMPLE_PROJECTION_PATHS = {
    "fiber_sample.identity.sample_id": "sample_id",
    "fiber_sample.identity.group_id": "sample_group_id",
    "fiber_sample.identity.aliases": "aliases",
    "fiber_sample.identity.fiber_type": "material_form",
    "fiber_sample.composition.material_system": "material_system",
}

_DOMAIN_ALIASES = {
    "composition": "成分",
    "component": "成分",
    "成分": "成分",
    "配方": "成分",
    "process": "工艺",
    "processing": "工艺",
    "工艺": "工艺",
    "制备": "工艺",
    "structure": "结构",
    "structural": "结构",
    "结构": "结构",
    "表征": "结构",
    "performance": "性能",
    "property": "性能",
    "properties": "性能",
    "性能": "性能",
}

_STRUCTURE_TERMS = (
    "xrd",
    "ftir",
    "raman",
    "sem",
    "tem",
    "afm",
    "waxd",
    "saxs",
    "spectroscopy",
    "spectrum",
    "peak",
    "crystall",
    "晶",
    "fiber_diameter",
    "average_diameter",
    "diameter",
    "直径",
    "morpholog",
    "形貌",
    "porosity",
    "pore",
    "孔隙",
    "orientation",
    "取向",
    "random_coil",
    "helix",
    "beta_phase",
    "secondary_structure",
    "二级结构",
)

_PERFORMANCE_TERMS = (
    "tensile",
    "strength",
    "modulus",
    "elongation",
    "toughness",
    "强度",
    "模量",
    "断裂伸长",
    "conductiv",
    "电导",
    "thermal",
    "melting",
    "glass_transition",
    "degradation",
    "热",
    "diffusion",
    "permeab",
    "solubility",
    "扩散",
    "渗透",
    "溶解",
    "density",
    "密度",
    "absorption",
    "吸附",
)

_COMPOSITION_TERMS = (
    "component",
    "composition",
    "matrix",
    "polymer",
    "additive",
    "filler",
    "solvent",
    "含量",
    "组分",
    "基体",
    "填料",
    "溶剂",
    "助剂",
    "配方",
)

_PROCESS_TERMS = (
    "spinning",
    "electrospin",
    "drawing",
    "anneal",
    "crosslink",
    "treatment",
    "drying",
    "route",
    "flow_rate",
    "feed_rate",
    "voltage",
    "distance",
    "duration",
    "工艺",
    "纺丝",
    "牵伸",
    "交联",
    "后处理",
    "干燥",
    "流速",
    "电压",
)

_SCIENTIFIC_NOTATION_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[×xX]\s*10\s*"
    r"(?:\^|\*\*)?\s*([+-]?\d+)\s*$"
)


class MaterialFactAdapterError(ValueError):
    """Raised when material facts cannot be represented by the flat schema."""


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any, length: int = 24) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[
        :length
    ]


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MaterialFactAdapterError(f"{path} 必须是 JSON 对象")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise MaterialFactAdapterError(f"{path} 必须是 JSON 数组")
    return value


def _first(source: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = source.get(name)
        if _present(value):
            return value
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, Mapping):
        return _canonical_json(value)
    if isinstance(value, set):
        value = sorted(value, key=str)
    if isinstance(value, (list, tuple)):
        return "；".join(part for item in value if (part := _text(item)))
    return str(value).strip()


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        match = _SCIENTIFIC_NOTATION_RE.match(text)
        if match:
            number = float(match.group(1)) * (10 ** int(match.group(2)))
        else:
            try:
                number = float(text)
            except ValueError:
                return None
    else:
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer() and abs(number) <= 2**53:
        return int(number)
    return number


def _year(value: Any) -> int | None:
    number = _number(value)
    if number is None or float(number) % 1:
        return None
    year = int(number)
    return year if 1000 <= year <= 9999 else None


def _aliases(value: Any) -> str:
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[；;,，、]", value)]
    elif isinstance(value, (list, tuple, set)):
        parts = [_text(part) for part in value]
    else:
        parts = [_text(value)]
    return "；".join(dict.fromkeys(part for part in parts if part))


def _normalize_domain(value: Any) -> str | None:
    text = _text(value).casefold()
    return _DOMAIN_ALIASES.get(text)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    normalized = text.casefold()
    for term in terms:
        normalized_term = term.casefold()
        # Short method acronyms need token boundaries: e.g. TEM must not match
        # the middle of "temperature" and SEM must not match "measurement".
        if (
            normalized_term.isascii()
            and normalized_term.isalpha()
            and len(normalized_term) <= 4
        ):
            pattern = (
                rf"(?<![a-z0-9]){re.escape(normalized_term)}"
                rf"(?![a-z0-9])"
            )
            if re.search(pattern, normalized):
                return True
        elif normalized_term in normalized:
            return True
    return False


def _infer_domain(fact: Mapping[str, Any]) -> str:
    explicit = _first(fact, ("domain", "fact_domain", "事实类别"))
    normalized = _normalize_domain(explicit)
    if normalized:
        return normalized

    field_path = _text(
        _first(fact, ("field_path", "path", "schema_path"))
    ).casefold()
    for marker, label in (
        (".composition.", "成分"),
        (".process.", "工艺"),
        (".structure.", "结构"),
        (".performance.", "性能"),
    ):
        if marker in field_path:
            return label

    metric_context = " ".join(
        _text(_first(fact, names))
        for names in (
            ("metric", "name", "field_label", "indicator_name", "parameter_name"),
            ("method", "test_method", "characterization_method"),
            ("category", "subcategory", "indicator_category"),
        )
    )
    # Metric semantics intentionally override ``fact_type``.  Older AI4S data
    # marked some diameter, crystallinity, and spectroscopy facts as
    # "performance"; these terms are structural measurements.
    if _contains_any(metric_context, _STRUCTURE_TERMS):
        return "结构"
    if _contains_any(metric_context, _PERFORMANCE_TERMS):
        return "性能"
    if _contains_any(metric_context, _COMPOSITION_TERMS):
        return "成分"
    if _contains_any(metric_context, _PROCESS_TERMS):
        return "工艺"

    normalized = _normalize_domain(
        _first(fact, ("fact_type", "type", "category"))
    )
    if normalized:
        return normalized
    raise MaterialFactAdapterError(
        "事实缺少可识别的类别；请提供 domain/fact_type/field_path，"
        "且类别必须为成分、工艺、结构或性能"
    )


def _metric(fact: Mapping[str, Any]) -> str:
    value = _first(
        fact,
        (
            "metric",
            "name",
            "field_label",
            "indicator_name",
            "parameter_name",
            "metric_or_parameter",
            "指标或参数名称",
        ),
    )
    text = _text(value)
    if text:
        return text
    field_path = _text(_first(fact, ("field_path", "path", "schema_path")))
    if field_path:
        return field_path.rsplit(".", 1)[-1].replace("_", " ")
    raise MaterialFactAdapterError("每条事实必须提供指标或参数名称")


def _raw_value(fact: Mapping[str, Any]) -> str:
    raw = _first(
        fact,
        (
            "raw_value",
            "original_value",
            "value_text",
            "raw",
            "原始值",
        ),
    )
    if not _present(raw):
        raw = _first(fact, ("value_number", "numeric_value", "value"))
    if not _present(raw):
        lower = _first(fact, ("range_min", "min_value", "lower_bound"))
        upper = _first(fact, ("range_max", "max_value", "upper_bound"))
        if _present(lower) or _present(upper):
            raw = f"{_text(lower)}–{_text(upper)}".strip("–")
    text = _text(raw)
    if not text:
        raise MaterialFactAdapterError("每条事实必须保留非空原始值")
    return text


def _paper_id(paper: Mapping[str, Any]) -> str:
    value = _first(paper, PAPER_FIELD_ALIASES["paper_id"])
    if _present(value):
        return _text(value)
    identity = {
        "doi": _text(_first(paper, PAPER_FIELD_ALIASES["doi"])),
        "title": _text(_first(paper, PAPER_FIELD_ALIASES["title"])),
    }
    if not identity["doi"] and not identity["title"]:
        raise MaterialFactAdapterError(
            "文献至少需要 paper_id、DOI/链接或标题之一"
        )
    return f"AI4S-P-{_digest(identity, 16)}"


def _paper_lookup_keys(paper: Mapping[str, Any]) -> set[str]:
    keys = {_paper_id(paper)}
    for name in PAPER_FIELD_ALIASES["paper_id"]:
        value = paper.get(name)
        if _present(value):
            keys.add(_text(value))
    doi = _first(paper, PAPER_FIELD_ALIASES["doi"])
    if _present(doi):
        keys.add(_text(doi))
    return keys


def _sample_lookup_keys(sample: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for name in (
        "id",
        "entity_key",
        "sample_key",
        "assigned_sample_id",
        *SAMPLE_FIELD_ALIASES["sample_id"],
    ):
        value = sample.get(name)
        if _present(value):
            keys.add(_text(value))
    return keys


def _paper_reference(source: Mapping[str, Any]) -> str:
    return _text(
        _first(source, ("paper_id", "document_id", "文献编号", "doi_or_url", "doi"))
    )


def _sample_reference(source: Mapping[str, Any]) -> str:
    return _text(
        _first(
            source,
            (
                "assigned_sample_id",
                "sample_id",
                "sample_key",
                "entity_key",
                "样品编号",
            ),
        )
    )


def _evidence_fields(fact: Mapping[str, Any]) -> tuple[str, str]:
    evidence = fact.get("evidence")
    evidence_map = evidence if isinstance(evidence, Mapping) else {}
    evidence_text = _text(
        _first(
            fact,
            ("evidence_text", "evidence_quote", "证据原文"),
        )
        or _first(
            evidence_map,
            ("evidence_text", "quote", "text", "raw_text"),
        )
        or (evidence if not isinstance(evidence, Mapping) else None)
    )
    explicit_location = _text(
        _first(
            fact,
            ("source_location", "source_position", "location", "来源位置"),
        )
        or _first(
            evidence_map,
            ("source_location", "source_position", "location"),
        )
    )
    if explicit_location:
        return evidence_text, explicit_location
    page = _first(fact, ("source_page", "page")) or _first(
        evidence_map, ("source_page", "page")
    )
    block = _first(fact, ("source_block_id", "block_id")) or _first(
        evidence_map, ("source_block_id", "block_id")
    )
    parts = []
    if _present(page):
        parts.append(f"p.{_text(page)}")
    if _present(block):
        parts.append(_text(block))
    return evidence_text, " / ".join(parts)


def _composition_role(fact: Mapping[str, Any]) -> str:
    explicit = _text(
        _first(fact, ("component_role", "role", "composition_role", "组分角色"))
    )
    if explicit:
        return explicit
    path = _text(_first(fact, ("field_path", "path"))).casefold()
    if ".matrix." in path:
        return "基体"
    if ".additive" in path or ".filler" in path:
        return "填料或改性组分"
    if ".solvent" in path or ".aid" in path:
        return "溶剂或助剂"
    return ""


def _process_stage(fact: Mapping[str, Any], metric: str) -> str:
    explicit = _text(
        _first(fact, ("process_stage", "stage", "工艺阶段"))
    )
    if explicit:
        return explicit
    context = (
        metric + " " + _text(_first(fact, ("field_path", "path")))
    ).casefold()
    for terms, label in (
        (("solution", "dope", "溶液", "配液"), "溶液配制"),
        (("spinning", "electrospin", "纺丝"), "纺丝"),
        (("drawing", "stretch", "牵伸"), "牵伸"),
        (("post_treatment", "anneal", "crosslink", "后处理", "交联"), "后处理"),
    ):
        if _contains_any(context, terms):
            return label
    return ""


def _indicator_category(
    fact: Mapping[str, Any],
    domain: str,
    metric: str,
) -> str:
    explicit = _text(
        _first(
            fact,
            (
                "indicator_category",
                "property_category",
                "structure_category",
                "performance_category",
                "subcategory",
                "指标类别",
            ),
        )
    )
    if explicit:
        return explicit
    category = _text(fact.get("category"))
    if category and not _normalize_domain(category):
        return category
    context = metric.casefold()
    if domain == "结构":
        if _contains_any(context, ("diameter", "sem", "tem", "形貌", "直径")):
            return "形貌"
        if _contains_any(context, ("xrd", "crystall", "晶", "beta_phase")):
            return "晶体结构"
        if _contains_any(
            context, ("ftir", "raman", "secondary_structure", "helix", "coil")
        ):
            return "分子结构"
        return "结构表征"
    if domain == "性能":
        if _contains_any(
            context, ("tensile", "strength", "modulus", "elongation", "强度", "模量")
        ):
            return "力学性能"
        if _contains_any(context, ("diffusion", "permeab", "扩散", "渗透")):
            return "传质性能"
        if _contains_any(context, ("thermal", "melting", "degradation", "热")):
            return "热性能"
        if _contains_any(context, ("conductiv", "电导")):
            return "电学性能"
        return "物理性能"
    return ""


def _set_text(payload: dict[str, Any], name: str, value: Any) -> None:
    text = _text(value)
    if text:
        payload[name] = text


def _semantic_identity(
    paper_id: str,
    sample_key: str,
    fact: Mapping[str, Any],
    *,
    domain: str,
    metric: str,
    raw_value: str,
    value_number: int | float | None,
    range_min: int | float | None,
    range_max: int | float | None,
    unit: str,
    method: str,
    condition: str,
) -> Mapping[str, Any]:
    fact_id = _text(_first(fact, ("fact_id", "id", "record_id")))
    fact_identity: Mapping[str, Any]
    if fact_id:
        fact_identity = {"fact_id": fact_id}
    else:
        fact_identity = {
            "domain": domain,
            "metric": metric,
            "raw_value": raw_value,
            "value_number": value_number,
            "range_min": range_min,
            "range_max": range_max,
            "unit": unit,
            "method": method,
            "condition": condition,
        }
    return {
        "schema": MATERIAL_FACT_SCHEMA_VERSION,
        "paper": paper_id,
        "sample": sample_key,
        "fact": fact_identity,
    }


def _merge_text(left: str, right: str) -> str:
    parts = []
    for value in (left, right):
        parts.extend(part.strip() for part in value.split("；") if part.strip())
    return "；".join(sorted(dict.fromkeys(parts)))


def _merge_duplicate_record(
    existing: dict[str, Any],
    incoming: Mapping[str, Any],
) -> None:
    existing_object = existing["content"]["object"]
    incoming_object = incoming["content"]["object"]
    mergeable = {"证据原文", "来源位置", "置信度"}
    existing_semantic = {
        key: value for key, value in existing_object.items() if key not in mergeable
    }
    incoming_semantic = {
        key: value for key, value in incoming_object.items() if key not in mergeable
    }
    if existing_semantic != incoming_semantic:
        raise MaterialFactAdapterError(
            f"事实记录键 {existing_object['事实记录键']} 对应冲突内容"
        )
    for name in ("证据原文", "来源位置"):
        merged = _merge_text(
            _text(existing_object.get(name)),
            _text(incoming_object.get(name)),
        )
        if merged:
            existing_object[name] = merged
    confidences = [
        value
        for value in (
            _number(existing_object.get("置信度")),
            _number(incoming_object.get("置信度")),
        )
        if value is not None
    ]
    if confidences:
        existing_object["置信度"] = max(confidences)


def _build_indexes(
    papers: list[Mapping[str, Any]],
    samples: list[Mapping[str, Any]],
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[tuple[str, str], Mapping[str, Any]],
    dict[str, list[Mapping[str, Any]]],
]:
    paper_index: dict[str, Mapping[str, Any]] = {}
    for paper in papers:
        for key in _paper_lookup_keys(paper):
            previous = paper_index.get(key)
            if previous is not None and previous is not paper:
                raise MaterialFactAdapterError(f"文献索引键重复: {key}")
            paper_index[key] = paper

    sample_by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
    samples_by_key: dict[str, list[Mapping[str, Any]]] = {}
    for sample in samples:
        paper_ref = _paper_reference(sample)
        for key in _sample_lookup_keys(sample):
            if paper_ref:
                pair = (paper_ref, key)
                previous = sample_by_pair.get(pair)
                if previous is not None and previous is not sample:
                    raise MaterialFactAdapterError(
                        f"同一文献内样品索引键重复: {paper_ref}/{key}"
                    )
                sample_by_pair[pair] = sample
            samples_by_key.setdefault(key, []).append(sample)
    return paper_index, sample_by_pair, samples_by_key


def _resolve_sample(
    fact: Mapping[str, Any],
    paper_ref: str,
    sample_by_pair: Mapping[tuple[str, str], Mapping[str, Any]],
    samples_by_key: Mapping[str, list[Mapping[str, Any]]],
) -> Mapping[str, Any] | None:
    sample_ref = _sample_reference(fact)
    if not sample_ref:
        return None
    direct = sample_by_pair.get((paper_ref, sample_ref))
    if direct is not None:
        return direct
    candidates = samples_by_key.get(sample_ref, [])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise MaterialFactAdapterError(
            f"样品引用 {sample_ref} 在多篇文献中不唯一"
        )
    return None


def _record_from_fact(
    fact: Mapping[str, Any],
    paper: Mapping[str, Any],
    sample: Mapping[str, Any] | None,
) -> dict[str, Any]:
    paper_id = _paper_id(paper)
    domain = _infer_domain(fact)
    metric = _metric(fact)
    raw_value = _raw_value(fact)
    value_number = _number(
        _first(fact, ("value_number", "numeric_value", "number", "数值"))
    )
    range_min = _number(
        _first(fact, ("range_min", "min_value", "lower_bound", "最小值"))
    )
    range_max = _number(
        _first(fact, ("range_max", "max_value", "upper_bound", "最大值"))
    )
    if range_min is not None and range_max is not None and range_min > range_max:
        raise MaterialFactAdapterError(
            f"{paper_id}/{metric} 的最小值不能大于最大值"
        )
    unit = _text(_first(fact, ("unit", "units", "单位")))
    method = _text(
        _first(
            fact,
            (
                "method",
                "test_method",
                "characterization_method",
                "equipment",
                "方法或设备",
            ),
        )
    )
    condition = _text(
        _first(
            fact,
            ("condition", "conditions", "notes", "description", "条件或说明"),
        )
    )

    sample_source = sample or {}
    sample_id = _text(
        _first(sample_source, SAMPLE_FIELD_ALIASES["sample_id"])
        or _first(fact, ("sample_id", "样品编号"))
    )
    if not sample_id:
        sample_id = "未指定"
    sample_key = _text(
        _first(sample_source, ("entity_key", "sample_key", "id"))
    ) or sample_id

    identity = _semantic_identity(
        paper_id,
        sample_key,
        fact,
        domain=domain,
        metric=metric,
        raw_value=raw_value,
        value_number=value_number,
        range_min=range_min,
        range_max=range_max,
        unit=unit,
        method=method,
        condition=condition,
    )
    record_key = f"AI4S-MF-{_digest(identity)}"

    payload: dict[str, Any] = {
        "事实记录键": record_key,
        "文献编号": paper_id,
        "样品编号": sample_id,
        "事实类别": domain,
        "指标或参数名称": metric,
        "原始值": raw_value,
    }
    _set_text(payload, "文献标题", _first(paper, PAPER_FIELD_ALIASES["title"]))
    _set_text(payload, "DOI或链接", _first(paper, PAPER_FIELD_ALIASES["doi"]))
    publication_year = _year(_first(paper, PAPER_FIELD_ALIASES["year"]))
    if publication_year is not None:
        payload["发表年份"] = publication_year
    _set_text(payload, "期刊", _first(paper, PAPER_FIELD_ALIASES["journal"]))
    _set_text(
        payload,
        "样品组编号",
        _first(sample_source, SAMPLE_FIELD_ALIASES["group_id"]),
    )
    aliases = _aliases(
        _first(sample_source, SAMPLE_FIELD_ALIASES["aliases"])
    )
    if aliases:
        payload["样品别名"] = aliases
    _set_text(
        payload,
        "材料体系",
        _first(sample_source, SAMPLE_FIELD_ALIASES["material_system"]),
    )
    _set_text(
        payload,
        "材料形态",
        _first(sample_source, SAMPLE_FIELD_ALIASES["material_form"]),
    )

    if domain == "成分":
        _set_text(payload, "组分角色", _composition_role(fact))
    if domain == "工艺":
        _set_text(payload, "工艺阶段", _process_stage(fact, metric))
    indicator_category = _indicator_category(fact, domain, metric)
    _set_text(payload, "指标类别", indicator_category)

    if value_number is not None:
        payload["数值"] = value_number
    if range_min is not None:
        payload["最小值"] = range_min
    if range_max is not None:
        payload["最大值"] = range_max
    _set_text(payload, "单位", unit)
    _set_text(payload, "方法或设备", method)
    _set_text(payload, "条件或说明", condition)
    evidence, location = _evidence_fields(fact)
    _set_text(payload, "证据原文", evidence)
    _set_text(payload, "来源位置", location)
    confidence = _number(_first(fact, ("confidence", "置信度")))
    if confidence is not None:
        if not 0 <= float(confidence) <= 1:
            raise MaterialFactAdapterError(
                f"{paper_id}/{metric} 的置信度必须在 0 到 1 之间"
            )
        payload["置信度"] = confidence

    return {
        "meta": {"数据ID": record_key},
        "content": {
            "object": payload,
            "operations": [],
            "results": [],
        },
    }


def material_inputs_from_projections(
    projections: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert current AI4S projections into generic paper/sample/fact lists."""

    papers: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []

    for projection_index, raw_projection in enumerate(projections):
        projection = _mapping(
            raw_projection, f"projections[{projection_index}]"
        )
        paper = dict(_mapping(projection.get("paper"), "projection.paper"))
        paper_id = _paper_id(paper)
        paper["paper_id"] = paper_id
        sample_by_entity: dict[str, dict[str, Any]] = {}

        raw_entities = projection.get("entities", [])
        if isinstance(raw_entities, Mapping):
            entity_items = []
            for entity_key, raw_entity in raw_entities.items():
                entity = dict(_mapping(raw_entity, f"entities[{entity_key}]"))
                entity.setdefault("entity_key", str(entity_key))
                entity_items.append(entity)
        else:
            entity_items = [
                dict(_mapping(item, f"entities[{index}]"))
                for index, item in enumerate(_list(raw_entities, "entities"))
            ]
        for entity in entity_items:
            if entity.get("entity_type") != "fiber_sample":
                continue
            entity.setdefault("paper_id", paper_id)
            entity_key = _text(entity.get("entity_key"))
            sample_by_entity[entity_key] = entity

        for value_index, raw_value in enumerate(
            _list(projection.get("values", []), "projection.values")
        ):
            value = dict(
                _mapping(raw_value, f"projection.values[{value_index}]")
            )
            field_path = _text(value.get("field_path"))
            if value.get("entity_type") == "paper":
                target = PAPER_PROJECTION_PATHS.get(field_path)
                if target:
                    paper[target] = _first(
                        value, ("raw_value", "value_text", "value_number")
                    )
                continue
            if value.get("entity_type") != "fiber_sample":
                continue

            entity_key = _text(value.get("entity_key"))
            sample = sample_by_entity.setdefault(
                entity_key,
                {
                    "entity_key": entity_key,
                    "paper_id": paper_id,
                    "sample_id": value.get("sample_id"),
                },
            )
            sample_target = SAMPLE_PROJECTION_PATHS.get(field_path)
            if sample_target:
                sample[sample_target] = _first(
                    value, ("raw_value", "value_text", "value_number")
                )
                continue
            value.setdefault("paper_id", paper_id)
            value.setdefault("sample_id", sample.get("sample_id"))
            facts.append(value)

        # ``unmapped_facts`` are already represented by projection ``values``
        # with ``mapping_status=unmapped``.  ``pending_facts`` have no value
        # projection yet, so only that bucket is added separately.
        for bucket_name in ("pending_facts",):
            bucket = projection.get(bucket_name, [])
            if not isinstance(bucket, list):
                continue
            for item_index, raw_item in enumerate(bucket):
                item = dict(
                    _mapping(
                        raw_item,
                        f"projection.{bucket_name}[{item_index}]",
                    )
                )
                item.setdefault("paper_id", paper_id)
                facts.append(item)

        papers.append(paper)
        samples.extend(sample_by_entity.values())

    return papers, samples, facts


def build_material_fact_records(
    papers: Iterable[Mapping[str, Any]] | None = None,
    samples: Iterable[Mapping[str, Any]] | None = None,
    facts: Iterable[Mapping[str, Any]] | None = None,
    *,
    projections: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic flat platform records.

    Callers may provide generic ``papers``/``samples``/``facts`` dictionaries,
    or pass current AI4S projections through the keyword-only ``projections``
    argument.  Supplying both forms is rejected to avoid accidental duplicates.
    """

    if projections is not None:
        if any(source is not None for source in (papers, samples, facts)):
            raise MaterialFactAdapterError(
                "projections 不能与 papers/samples/facts 同时提供"
            )
        papers_list, samples_list, facts_list = material_inputs_from_projections(
            projections
        )
    else:
        papers_list = [
            _mapping(item, f"papers[{index}]")
            for index, item in enumerate(papers or [])
        ]
        samples_list = [
            _mapping(item, f"samples[{index}]")
            for index, item in enumerate(samples or [])
        ]
        facts_list = [
            _mapping(item, f"facts[{index}]")
            for index, item in enumerate(facts or [])
        ]

    if not facts_list:
        raise MaterialFactAdapterError("至少需要一条材料事实")
    paper_index, sample_by_pair, samples_by_key = _build_indexes(
        papers_list, samples_list
    )

    records_by_key: dict[str, dict[str, Any]] = {}
    for index, fact in enumerate(facts_list):
        paper_ref = _paper_reference(fact)
        sample = _resolve_sample(
            fact,
            paper_ref,
            sample_by_pair,
            samples_by_key,
        )
        if not paper_ref and sample is not None:
            paper_ref = _paper_reference(sample)

        paper = paper_index.get(paper_ref) if paper_ref else None
        if paper is None and len({_paper_id(item) for item in papers_list}) == 1:
            paper = papers_list[0]
        if paper is None:
            raise MaterialFactAdapterError(
                f"facts[{index}] 无法关联到唯一文献"
            )
        if sample is None:
            sample_ref = _sample_reference(fact)
            sample = (
                {
                    "paper_id": _paper_id(paper),
                    "sample_id": sample_ref,
                    "entity_key": sample_ref,
                }
                if sample_ref
                else None
            )

        record = _record_from_fact(fact, paper, sample)
        key = record["content"]["object"]["事实记录键"]
        if key in records_by_key:
            _merge_duplicate_record(records_by_key[key], record)
        else:
            records_by_key[key] = record

    records = list(records_by_key.values())
    records.sort(
        key=lambda record: (
            record["content"]["object"]["文献编号"],
            record["content"]["object"]["样品编号"],
            DOMAIN_ORDER[record["content"]["object"]["事实类别"]],
            record["content"]["object"]["指标或参数名称"],
            record["content"]["object"]["事实记录键"],
        )
    )
    return records


def _template_from_document(document: Mapping[str, Any]) -> Mapping[str, Any]:
    template = document.get("template")
    if isinstance(template, Mapping):
        return template
    if "object" in document:
        return document
    raise MaterialFactAdapterError("文档缺少 template 对象")


def validate_material_fact_template(
    document: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate that a platform template is the flat v0.2 fact schema."""

    root = _mapping(document, "document")
    template = _mapping(_template_from_document(root), "template")
    template_id = template.get("_id")
    if (
        isinstance(template_id, bool)
        or not isinstance(template_id, int)
        or template_id <= 0
    ):
        raise MaterialFactAdapterError("template._id 必须是正 JSON 整数")
    object_section = _mapping(template.get("object"), "template.object")
    blocks = _mapping(object_section.get("blocks"), "template.object.blocks")
    order = _list(blocks.get("_ord"), "template.object.blocks._ord")
    if not all(isinstance(name, str) for name in order):
        raise MaterialFactAdapterError("template.object.blocks._ord 只能包含字符串")
    if tuple(order) != MATERIAL_FACT_FIELDS:
        raise MaterialFactAdapterError(
            "平台模板字段或顺序与原子材料事实 v0.2 不一致"
        )
    for name in MATERIAL_FACT_FIELDS:
        schema = _mapping(blocks.get(name), f"template.object.blocks.{name}")
        if schema.get("t") != FIELD_TYPES[name]:
            raise MaterialFactAdapterError(
                f"字段 {name} 的类型必须为 t={FIELD_TYPES[name]}"
            )
        if name in REQUIRED_FIELDS and schema.get("r") is not True:
            raise MaterialFactAdapterError(f"字段 {name} 必须设为必填")
    fact_type_schema = _mapping(blocks["事实类别"], "事实类别 schema")
    misc = _mapping(fact_type_schema.get("misc"), "事实类别.misc")
    options = {_text(item) for item in misc.get("opt", [])}
    if options != set(DOMAIN_LABELS):
        raise MaterialFactAdapterError(
            "事实类别候选值必须恰好为：成分、工艺、结构、性能"
        )
    if _list(template.get("operations"), "template.operations"):
        raise MaterialFactAdapterError("v0.2 模板不得包含 operations 嵌套区")
    if _list(template.get("results"), "template.results"):
        raise MaterialFactAdapterError("v0.2 模板不得包含 results 嵌套区")
    return {
        "schema_version": MATERIAL_FACT_SCHEMA_VERSION,
        "template_id": template_id,
        "field_count": len(MATERIAL_FACT_FIELDS),
        "flat": True,
    }


def _validate_payload_value(
    value: Any,
    field_type: int,
    path: str,
    *,
    options: set[str] | None = None,
) -> None:
    if field_type == 1:
        if not isinstance(value, str) or not value.strip():
            raise MaterialFactAdapterError(f"{path} 必须是非空字符串")
        return
    if field_type == 2:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise MaterialFactAdapterError(f"{path} 必须是有限 JSON 数字")
        return
    if field_type == 6:
        if not isinstance(value, str) or value not in (options or set()):
            raise MaterialFactAdapterError(
                f"{path} 必须是候选值：{', '.join(sorted(options or set()))}"
            )
        return
    raise MaterialFactAdapterError(f"{path} 使用了不支持的字段类型 {field_type}")


def validate_material_fact_records(
    template_document: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate flat records against the v0.2 template."""

    template_summary = validate_material_fact_template(template_document)
    record_list = list(records)
    if not record_list:
        raise MaterialFactAdapterError("data 至少需要一条材料事实记录")
    seen_data_ids: set[str] = set()
    seen_record_keys: set[str] = set()
    for index, raw_record in enumerate(record_list):
        record = _mapping(raw_record, f"data[{index}]")
        meta = _mapping(record.get("meta"), f"data[{index}].meta")
        data_id = _text(meta.get("数据ID"))
        if not data_id:
            raise MaterialFactAdapterError(f"data[{index}].meta.数据ID 不能为空")
        if data_id in seen_data_ids:
            raise MaterialFactAdapterError(f"重复平台数据ID: {data_id}")
        seen_data_ids.add(data_id)
        content = _mapping(record.get("content"), f"data[{index}].content")
        payload = _mapping(
            content.get("object"), f"data[{index}].content.object"
        )
        unknown = sorted(set(payload) - set(MATERIAL_FACT_FIELDS))
        if unknown:
            raise MaterialFactAdapterError(
                f"data[{index}] 含模板外字段: {', '.join(unknown)}"
            )
        for name in REQUIRED_FIELDS:
            if not _present(payload.get(name)):
                raise MaterialFactAdapterError(
                    f"data[{index}].content.object.{name} 为必填"
                )
        for name, value in payload.items():
            options = set(DOMAIN_LABELS) if name == "事实类别" else None
            _validate_payload_value(
                value,
                FIELD_TYPES[name],
                f"data[{index}].content.object.{name}",
                options=options,
            )
        publication_year = payload.get("发表年份")
        if publication_year is not None and _year(publication_year) is None:
            raise MaterialFactAdapterError(
                f"data[{index}].发表年份必须是四位整数"
            )
        confidence = payload.get("置信度")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise MaterialFactAdapterError(
                f"data[{index}].置信度必须在 0 到 1 之间"
            )
        lower = payload.get("最小值")
        upper = payload.get("最大值")
        if lower is not None and upper is not None and float(lower) > float(upper):
            raise MaterialFactAdapterError(
                f"data[{index}].最小值不能大于最大值"
            )
        record_key = _text(payload.get("事实记录键"))
        if record_key != data_id:
            raise MaterialFactAdapterError(
                f"data[{index}] 的数据ID必须等于事实记录键"
            )
        if record_key in seen_record_keys:
            raise MaterialFactAdapterError(f"重复事实记录键: {record_key}")
        seen_record_keys.add(record_key)
        if _list(content.get("operations"), f"data[{index}].operations"):
            raise MaterialFactAdapterError(
                f"data[{index}] 不得包含 operations 嵌套数据"
            )
        if _list(content.get("results"), f"data[{index}].results"):
            raise MaterialFactAdapterError(
                f"data[{index}] 不得包含 results 嵌套数据"
            )
    return {
        **template_summary,
        "record_count": len(record_list),
        "unique_data_id_count": len(seen_data_ids),
        "unique_record_key_count": len(seen_record_keys),
    }


def build_material_fact_batch(
    batch_template: Mapping[str, Any],
    papers: Iterable[Mapping[str, Any]] | None = None,
    samples: Iterable[Mapping[str, Any]] | None = None,
    facts: Iterable[Mapping[str, Any]] | None = None,
    *,
    projections: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an upload-ready batch using a downloaded v0.2 platform binding."""

    root = _mapping(batch_template, "batch_template")
    validate_material_fact_template(root)
    dataset = _mapping(root.get("dataset"), "batch_template.dataset")
    dataset_id = dataset.get("_id")
    if (
        isinstance(dataset_id, bool)
        or not isinstance(dataset_id, int)
        or dataset_id <= 0
    ):
        raise MaterialFactAdapterError(
            "batch_template.dataset._id 必须是正 JSON 整数"
        )
    records = build_material_fact_records(
        papers,
        samples,
        facts,
        projections=projections,
    )
    batch = {
        "dataset": copy.deepcopy(dataset),
        "template": copy.deepcopy(_template_from_document(root)),
        "data": records,
    }
    validate_material_fact_batch(batch)
    return batch


def validate_material_fact_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete upload batch and its large integer identifiers."""

    root = _mapping(batch, "batch")
    dataset = _mapping(root.get("dataset"), "batch.dataset")
    dataset_id = dataset.get("_id")
    if (
        isinstance(dataset_id, bool)
        or not isinstance(dataset_id, int)
        or dataset_id <= 0
    ):
        raise MaterialFactAdapterError("batch.dataset._id 必须是正 JSON 整数")
    template_summary = validate_material_fact_template(root)
    records_summary = validate_material_fact_records(
        root, _list(root.get("data"), "batch.data")
    )
    return {
        **template_summary,
        **records_summary,
        "dataset_id": dataset_id,
        "ids_exceed_javascript_safe_integer": (
            dataset_id > JAVASCRIPT_MAX_SAFE_INTEGER
            or int(template_summary["template_id"]) > JAVASCRIPT_MAX_SAFE_INTEGER
        ),
    }


def dumps_material_fact_batch(
    batch: Mapping[str, Any],
    *,
    indent: int = 2,
) -> str:
    """Serialize the batch without rounding the platform's integer IDs."""

    validate_material_fact_batch(batch)
    return json.dumps(batch, ensure_ascii=False, indent=indent) + "\n"

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Callable, List, Dict, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.paper import Paper
from app.models.page_inventory import PageInventory
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.project import Project
from app.models.extraction_job import ExtractionJob  # Ensure FK table registered in metadata

from app.services.llm_client import (
    create_llm_client,
    _tolerant_parse_json,
    _format_ai_exception,
)
from app.services.validation import (
    normalize_metric_name,
    normalize_unit,
    metric_unit_compatible,
    check_value_range,
    looks_like_reviewable_sample_id,
    SAMPLE_STOPWORDS,
)
from app.services.pdf_utils import (
    extract_pdf_text,
    extract_pdf_tables_markdown,
    render_pdf_pages,
)


class V6ExtractorService:
    """High-fidelity Multi-path AI Extraction Service for Fiber Material Literature.

    1. PDF Parse & Heuristic Page Inventory Classification
    2. Mandatory Page Selection (Front, Experimental, Table/Figure, Key performance pages)
    3. Multi-path Evidence Cards Extraction (LLM + Local regex rule backup)
    4. Database Persistence of traceable 'evidence_items'
    5. Structural data group fusion into 40-column Candidate Records
    6. Automatic Systematic Quality Control Check (QC Engine)
    """

    # ------------------------------------------------------------------
    # Page inventory
    # ------------------------------------------------------------------

    @staticmethod
    def parse_pdf_and_build_inventory(
        pdf_path: str, tables: list[dict[str, str]] | None = None
    ) -> List[Dict[str, Any]]:
        """Parse PDF text, classify pages, and merge extracted tables into inventory."""
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        experimental_keywords = [
            "experimental", "preparation", "synthesis", "materials",
            "characterization", "fabrication", "spinning",
        ]
        performance_keywords = [
            "tensile strength", "modulus", "conductivity", "contact angle",
            "loi", "ul-94", "properties", "performance",
        ]

        raw_text = extract_pdf_text(pdf_path)
        if not raw_text.strip():
            raise RuntimeError(
                "PDF 未提取到可用文本。可能是扫描版或图片型 PDF，请尝试使用支持 OCR 的 PDF。"
            )

        pages = V6ExtractorService._parse_pages_from_text(raw_text)
        tables = tables or []
        # Build a lookup: page_number -> list of table markdown blocks
        table_by_page: dict[int, list[str]] = defaultdict(list)
        for t in tables:
            m = re.search(r"page\s+(\d+)", t.get("source_location", ""))
            if m:
                table_by_page[int(m.group(1))].append(t["text"])

        inventory_items = []
        for page_num, page_text in pages:
            text_lower = page_text.lower()

            has_tables = "table" in text_lower or bool(table_by_page.get(page_num))
            has_figures = "fig." in text_lower or "figure" in text_lower

            # Merge table text into page text for downstream extraction
            merged_text = page_text
            if page_num in table_by_page:
                merged_text += "\n\n" + "\n\n".join(table_by_page[page_num])

            if page_num == 1:
                page_type = "front_page"
            elif any(kw in text_lower for kw in experimental_keywords) and page_num <= 5:
                page_type = "experimental"
            elif "experimental section" in text_lower or "experimental methods" in text_lower:
                page_type = "experimental"
            elif any(kw in text_lower for kw in performance_keywords) and has_tables:
                page_type = "table_figure"
            elif any(kw in text_lower for kw in performance_keywords):
                page_type = "results_discussion"
            elif has_tables or has_figures:
                page_type = "table_figure"
            else:
                page_type = "results_discussion"

            inventory_items.append({
                "page_number": page_num,
                "page_type": page_type,
                "has_tables": has_tables,
                "has_figures": has_figures,
                "extracted_text": merged_text,
            })

        return inventory_items

    # ------------------------------------------------------------------
    # Regex-based local extraction (no-LLM fallback)
    # ------------------------------------------------------------------

    @staticmethod
    def _find_sample_ids(text: str) -> List[str]:
        patterns = [
            r"\b(?:S|A|B|C)\d+[A-Za-z0-9_\-/.%]*\b",
            r"(?<![A-Za-z0-9-])(?:PI|PAA|PET|PVDF|PAN|PP|PVA|PLA|PA6|MXene|CNT|GO)[A-Za-z0-9_\-/.]*\d+[A-Za-z0-9_\-/.%]*\b",
            r"\b[A-Z0-9]{2,}(?:[-_/][A-Z0-9]{1,}){1,4}(?:[-_/]?\d+(?:\.\d+)?%?)?\b",
            r"\b[A-Za-z]{1,6}[-_/][A-Za-z0-9]{1,10}[-_/]?\d+(?:\.\d+)?%?\b",
        ]
        found = []
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                val = match.group(0).strip(" ,.;:()[]")
                if 2 <= len(val) <= 30:
                    val_lower = val.lower()
                    if val_lower not in SAMPLE_STOPWORDS and not re.fullmatch(
                        r"[A-Z][a-z]{2,}", val
                    ):
                        found.append(val)
        return list(dict.fromkeys(found))

    @staticmethod
    def _find_performance_values(text: str) -> List[Tuple[str, str, str, str]]:
        """Extract performance values with metric-unit compatibility check."""
        metric_keywords = {
            "tensile_strength": ("tensile strength", "breaking strength", "拉伸强度"),
            "compressive_strength": ("compressive strength", "compression strength", "压缩强度"),
            "elongation_at_break": ("elongation", "断裂伸长"),
            "Youngs_modulus": ("modulus", "杨氏模量"),
            "electrical_conductivity": ("electrical conductivity", "电导率"),
            "thermal_conductivity": ("thermal conductivity", "热导率"),
            "water_contact_angle": ("contact angle", "接触角"),
            "density": ("density", "密度"),
            "limiting_oxygen_index": ("limiting oxygen index", " loi", "极限氧指数"),
        }
        value_pattern = re.compile(
            r"([-+]?\d+(?:\.\d+)?)\s*"
            r"(MPa|GPa|kPa|Pa|%|S/m|S\s*cm-?1|W\s*m-?1\s*K-?1|mW\s*m-?1\s*K-?1|"
            r"W/\(m[·.]?K\)|pC/N|V|mV|degree|degrees|°|°C|℃|mg\s*cm-?3|g/cm3|g/cm³|kg/m3)",
            re.I,
        )
        results = []
        compact = re.sub(r"\s+", " ", text)
        for match in value_pattern.finditer(compact):
            start = max(0, match.start() - 160)
            end = min(len(compact), match.end() + 160)
            snippet = compact[start:end]
            unit = match.group(2)
            for name, keywords in metric_keywords.items():
                if any(kw.lower() in snippet.lower() for kw in keywords):
                    if metric_unit_compatible(name, unit):
                        results.append((name, match.group(1), unit, snippet))
                        break
        return results

    @staticmethod
    def _snippet_around(text: str, needle: str, limit: int = 240) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        idx = compact.lower().find(needle.lower())
        if idx < 0:
            return compact[:limit]
        start = max(0, idx - limit // 2)
        end = min(len(compact), idx + limit // 2)
        return compact[start:end]

    @staticmethod
    def _parse_pages_from_text(raw_text: str) -> list[tuple[int, str]]:
        """Split [page N]-annotated text into (page_number, page_text) pairs."""
        matches = list(re.finditer(r"(?m)^\[page\s+(\d+)\]\s*$", raw_text))
        if not matches:
            return [(1, raw_text)]
        pages: list[tuple[int, str]] = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = (
                matches[idx + 1].start()
                if idx + 1 < len(matches)
                else len(raw_text)
            )
            pages.append((int(match.group(1)), raw_text[start:end].strip()))
        return pages

    # ------------------------------------------------------------------
    # Local regex evidence card extraction (no-LLM path)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_regex_evidence_cards(
        sample_text: str,
        performance_text: str,
        paper_info: dict,
    ) -> List[Dict[str, Any]]:
        """Build evidence cards using local regex rules (no LLM required)."""
        cards: list[dict] = []

        # Paper metadata from regex
        if not paper_info.get("doi_or_url"):
            doi_match = re.search(
                r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", sample_text, re.IGNORECASE
            )
            if doi_match:
                paper_info["doi_or_url"] = doi_match.group(0)
        if not paper_info.get("year") or paper_info.get("year") == 2026:
            ym = re.search(r"\b(20\d{2})\b", sample_text)
            if ym:
                paper_info["year"] = int(ym.group(1))

        # Sample IDs
        found_samples = V6ExtractorService._find_sample_ids(sample_text)
        for s_id in found_samples[:10]:
            cards.append({
                "card_type": "sample",
                "related_sample_id": s_id,
                "source_location": "experimental",
                "evidence_text": V6ExtractorService._snippet_around(sample_text, s_id, 240),
                "confidence": 0.55,
                "normalized_payload": json.dumps({
                    "sample_id": s_id,
                    "material_system": "",
                    "composition": "",
                }, ensure_ascii=False),
            })

        # Process keywords
        for kw in ["electrospinning", "spinning", "annealing", "drying", "curing",
                    "imidization", "freeze-drying", "纺丝", "热处理", "退火", "亚胺化"]:
            if kw in sample_text.lower():
                cards.append({
                    "card_type": "process",
                    "related_sample_id": found_samples[0] if found_samples else "",
                    "source_location": "experimental",
                    "evidence_text": V6ExtractorService._snippet_around(sample_text, kw, 240),
                    "confidence": 0.45,
                    "normalized_payload": json.dumps({
                        "process_route": kw,
                        "spinning_method": "electrospinning" if "electrospinning" in kw.lower() else "",
                    }),
                })
                break

        # Structure keywords
        for kw in ["SEM", "XRD", "FTIR", "DSC", "Raman", "WAXS", "SAXS",
                    "XPS", "TEM", "AFM", "TGA", "BET", "形貌", "结晶", "孔"]:
            if kw.lower() in sample_text.lower():
                cards.append({
                    "card_type": "structure",
                    "related_sample_id": found_samples[0] if found_samples else "",
                    "source_location": "characterization",
                    "evidence_text": V6ExtractorService._snippet_around(sample_text, kw, 240),
                    "confidence": 0.45,
                    "normalized_payload": json.dumps({
                        "structure_methods": kw,
                        "structure_features": "",
                    }),
                })
                break

        # Performance values
        found_perfs = V6ExtractorService._find_performance_values(performance_text)
        for metric, val, unit, snippet in found_perfs[:12]:
            closest_sample = found_samples[0] if found_samples else ""
            for s_id in found_samples:
                if s_id in snippet:
                    closest_sample = s_id
                    break
            cards.append({
                "card_type": "performance",
                "related_sample_id": closest_sample,
                "source_location": "results_discussion",
                "evidence_text": snippet,
                "confidence": 0.50,
                "normalized_payload": json.dumps({
                    "performance_metric": metric,
                    "performance_value": val,
                    "performance_unit": unit,
                }, ensure_ascii=False),
            })

        return cards

    # ------------------------------------------------------------------
    # Row-level QC
    # ------------------------------------------------------------------

    @staticmethod
    def run_row_level_qc(
        candidate: Dict[str, Any],
        all_candidates_in_paper: List[Dict[str, Any]],
    ) -> Tuple[str, List[str]]:
        """Automated quality check on a single candidate row."""
        errors = []
        warnings = []

        sample_id = candidate.get("sample_id")
        metric = candidate.get("performance_metric")
        val = candidate.get("performance_value")

        if not sample_id or sample_id.strip() == "":
            errors.append("样品名称缺失")
        if not metric or metric.strip() == "":
            errors.append("性能指标名称缺失")
        if not val or val.strip() == "":
            errors.append("性能数据缺失")

        unit = (candidate.get("performance_unit") or "").strip()
        metric_normalized = normalize_metric_name(metric or "")

        # Unit compatibility check (delegated to validation module)
        if metric_normalized and unit:
            if not metric_unit_compatible(metric_normalized, unit):
                allowed = ", ".join(
                    {"tensile_strength": "MPa/GPa", "Youngs_modulus": "GPa/MPa",
                     "electrical_conductivity": "S/m", "thermal_conductivity": "W/mK",
                     "water_contact_angle": "°", "density": "g/cm³",
                     "elongation_at_break": "%"}.get(metric_normalized, "")
                )
                warnings.append(
                    f"指标 '{metric_normalized}' 与单位 '{unit}' 可能不匹配"
                    + (f"，常用：{allowed}" if allowed else "")
                )

        # Value range check (delegated to validation module)
        if val and metric:
            range_warning = check_value_range(metric, val)
            if range_warning:
                warnings.append(range_warning)

        # Control sample detection
        has_control = any(
            term in (c.get("sample_id") or "").lower()
            for c in all_candidates_in_paper
            for term in ["control", "pure", "neat", "pristine", "0%", "0 wt", "blank"]
        )
        if not has_control:
            warnings.append("未在该文献中识别到明显的对照样品 (如 Pure, Control, Neat 等)")

        # Duplicate detection
        dups = sum(
            1 for c in all_candidates_in_paper
            if c.get("id") != candidate.get("id")
            and c.get("sample_id") == sample_id
            and c.get("performance_metric") == metric
            and c.get("performance_value") == val
        )
        if dups > 0:
            errors.append("检测到相同样品、指标和数值的重复候选行")

        if errors:
            return "missing", errors + warnings
        if warnings:
            return "uncertain", errors + warnings
        return "pending", errors + warnings

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    @staticmethod
    async def run_full_pipeline_for_paper(
        db: AsyncSession, paper_id: int,
        progress_callback: Callable[[str, int], Any] | None = None,
    ) -> Dict[str, Any]:
        """Run the V6 extraction pipeline: inventory → evidence cards → candidates → QC."""
        def _emit(step: str, pct: int):
            if progress_callback:
                progress_callback(step, pct)
        # -- Load paper and project --
        res = await db.execute(select(Paper).where(Paper.id == paper_id))
        paper = res.scalar_one_or_none()
        if not paper:
            return {"error": "Paper not found"}

        proj_res = await db.execute(
            select(Project).where(Project.id == paper.project_id)
        )
        project = proj_res.scalar_one_or_none()
        if not project:
            return {"error": "Project not found"}

        from app.core.config import settings

        pdf_path = os.path.join(settings.UPLOAD_DIR, paper.file_object_key)
        if not pdf_path or not os.path.exists(pdf_path):
            return {"error": f"PDF file not found: {paper.file_object_key}"}

        # -- Clean up old extraction data for re-extraction --
        from sqlalchemy import delete as sa_delete
        await db.execute(sa_delete(EvidenceItem).where(EvidenceItem.paper_id == paper_id))
        await db.execute(sa_delete(CandidateRecord).where(CandidateRecord.source_paper_id == paper_id))
        await db.execute(sa_delete(PageInventory).where(PageInventory.paper_id == paper_id))
        await db.commit()

        # -- Step 1: PDF parse + table extraction + page inventory --
        tables = extract_pdf_tables_markdown(pdf_path)
        try:
            pages = V6ExtractorService.parse_pdf_and_build_inventory(pdf_path, tables)
        except Exception as e:
            return {"error": f"Failed to parse PDF: {str(e)}"}

        await db.execute(
            update(Paper)
            .where(Paper.id == paper_id)
            .values(status="extracting", page_count=len(pages))
        )

        for p in pages:
            db.add(PageInventory(
                paper_id=paper_id,
                page_number=p["page_number"],
                text_length=len(p["extracted_text"]),
                has_table_signal=p["has_tables"],
                has_figure_caption=p["has_figures"],
                has_experimental_signal=(p["page_type"] == "experimental"),
                importance_score=1.0,
                summary=p["page_type"],
            ))
        await db.commit()

        _emit("inventory", 30)

        # -- Step 2: Classify pages --
        front_pages = [p for p in pages if p["page_type"] == "front_page"]
        experimental_pages = [p for p in pages if p["page_type"] == "experimental"]
        results_pages = [
            p for p in pages
            if p["page_type"] in ("results_discussion", "table_figure")
        ]

        sample_text = "\n\n".join(
            p["extracted_text"] for p in (front_pages + experimental_pages)
        )
        performance_text = "\n\n".join(
            p["extracted_text"] for p in results_pages
        )

        # -- Step 3: Build evidence cards (LLM or local regex) --
        paper_info = {
            "paper_title": paper.original_filename.replace(".pdf", ""),
            "doi_or_url": "",
            "year": 2026,
            "journal": "",
        }

        has_llm = bool(project.llm_api_key and project.llm_api_key.strip())
        raw_evidence_cards: list[dict] = []
        client = None

        if has_llm:
            try:
                client = create_llm_client(
                    provider=project.llm_provider or "openai",
                    api_key=project.llm_api_key,
                    model=project.llm_model or "gpt-4o",
                    base_url=project.llm_base_url or "https://api.openai.com/v1",
                )
            except Exception as e:
                print(f"Warning: Failed to create LLM client: {e}")
                client = None

            if client:
                # 3a: Paper metadata
                try:
                    parsed, _ = client.generate_json_tolerant(
                        "You are a professional material science assistant. Extract paper title, DOI, year, journal as JSON: "
                        "{'paper_title': '...', 'doi_or_url': '...', 'year': 2026, 'journal': '...'}",
                        f"First 10000 chars of paper:\n{sample_text[:10000]}",
                        max_tokens=1000,
                    )
                    paper_info.update(parsed)
                except Exception as e:
                    print(f"Warning: LLM paper info extraction failed: {e}")

                # 3b: Sample catalogue
                try:
                    parsed, _ = client.generate_json_tolerant(
                        "You are an AI material data architect. Extract all prepared fiber material samples "
                        "from this experimental section. For each sample, find its name (sample_id), composition, "
                        "and material_system. Output: {'samples': [{'sample_id': '...', 'material_system': '...', "
                        "'composition': '...'}, ...]}",
                        f"Experimental text:\n{sample_text[:15000]}",
                        max_tokens=2000,
                    )
                    for s in parsed.get("samples") or parsed.get("_items") or []:
                        s_id = s.get("sample_id", "")
                        if s_id:
                            raw_evidence_cards.append({
                                "card_type": "sample",
                                "related_sample_id": s_id,
                                "source_location": "experimental_text",
                                "evidence_text": V6ExtractorService._snippet_around(
                                    sample_text, s_id, 240
                                ),
                                "confidence": 0.88,
                                "normalized_payload": json.dumps(s, ensure_ascii=False),
                            })
                except Exception as e:
                    print(f"Warning: LLM sample catalogue failed: {e}")

                # 3c: Composition/process/structure (unified extract)
                try:
                    sample_hint = ", ".join(
                        c["related_sample_id"] for c in raw_evidence_cards
                        if c["card_type"] == "sample"
                    ) or "unknown"
                    parsed, _ = client.generate_json_tolerant(
                        "You extract composition, fabrication process, and structure characterization "
                        "from fiber material papers. Output as JSON: "
                        "{'composition': {'matrix_name': '...', 'additive_expression': '...', "
                        "'solvent_or_aid': '...', 'composition_expression': '...'}, "
                        "'process': {'process_route': '...', 'spinning_method': '...', "
                        "'process_parameters': '...', 'post_treatment': '...'}, "
                        "'structure': {'structure_methods': '...', 'structure_features': '...'}}",
                        f"Known samples: {sample_hint}\n\nExperimental text:\n{sample_text[:12000]}",
                        max_tokens=1500,
                    )
                    if not parsed.get("_parse_failed"):
                        for card_type, key in [
                            ("composition", "composition"),
                            ("process", "process"),
                            ("structure", "structure"),
                        ]:
                            payload = parsed.get(key, {})
                            if payload:
                                raw_evidence_cards.append({
                                    "card_type": card_type,
                                    "related_sample_id": (
                                        raw_evidence_cards[0]["related_sample_id"]
                                        if raw_evidence_cards else ""
                                    ),
                                    "source_location": "experimental",
                                    "evidence_text": json.dumps(payload, ensure_ascii=False)[:600],
                                    "confidence": 0.80,
                                    "normalized_payload": json.dumps(payload, ensure_ascii=False),
                                })
                except Exception as e:
                    print(f"Warning: LLM composition/process/structure failed: {e}")

                # 3d: Performance extraction
                sample_ids_str = ", ".join(
                    c["related_sample_id"] for c in raw_evidence_cards
                    if c["card_type"] == "sample"
                )
                if sample_ids_str:
                    try:
                        parsed, _ = client.generate_json_tolerant(
                            f"You are a fiber material data scientist. Extract ALL available performance "
                            f"metrics for these sample IDs: [{sample_ids_str}]. "
                            "Extract EVERY numerical property with units you can find, including but not "
                            "limited to: mechanical (tensile_strength, elastic_modulus, elongation_at_break, "
                            "compressive_strength, flexural_modulus, hardness), thermal (thermal_conductivity, "
                            "thermal_diffusivity, Tg, Td5%, Td10%, CTE, shrinkage), dielectric (dielectric_constant, "
                            "dielectric_loss, breakdown_strength, conductivity), physical (density, porosity, "
                            "specific_surface_area, water_contact_angle, oil_contact_angle, volume_shrinkage, "
                            "linear_shrinkage), functional (EMI_SE, transmittance, reflectance, absorptance). "
                            "For EACH sample+metric pair, report the exact numerical value and unit from the text. "
                            "If a sample has 10 metrics, output 10 entries. Do NOT skip any numerical data. "
                            "Output JSON: {{'performances': [{{'sample_id': str, 'performance_metric': str, "
                            "'performance_value': str, 'performance_unit': str, 'performance_category': "
                            "'mechanical|thermal|dielectric|physical|functional', 'performance_method': str, "
                            "'performance_condition': str, 'source_location': str}}, ...]}}",
                            f"Results text:\n{performance_text[:25000]}",
                            max_tokens=4000,
                        )
                        for p in parsed.get("performances") or parsed.get("_items") or []:
                            s_id = p.get("sample_id", "")
                            metric = p.get("performance_metric", "")
                            val = p.get("performance_value", "")
                            if s_id and metric and val:
                                raw_evidence_cards.append({
                                    "card_type": "performance",
                                    "related_sample_id": s_id,
                                    "source_location": p.get("source_location", "results_text"),
                                    "evidence_text": V6ExtractorService._snippet_around(
                                        performance_text, str(val), 240
                                    ),
                                    "confidence": 0.85,
                                    "normalized_payload": json.dumps(p, ensure_ascii=False),
                                })
                    except Exception as e:
                        print(f"Warning: LLM performance extraction failed: {e}")
        else:
            print("No LLM configured — using local regex evidence extraction.")
            raw_evidence_cards = V6ExtractorService._build_regex_evidence_cards(
                sample_text, performance_text, paper_info
            )

        _emit("extracting", 70)

        # -- Step 3e: Vision channel for figure/table-intensive pages --
        if client and has_llm:
            perf_count = sum(1 for c in raw_evidence_cards if c["card_type"] == "performance")
            if perf_count < 5:
                vision_pages = [p["page_number"] for p in results_pages[:6]]
                if vision_pages:
                    try:
                        rendered = render_pdf_pages(pdf_path, vision_pages)
                        if rendered:
                            sample_hint = ", ".join(
                                c["related_sample_id"] for c in raw_evidence_cards
                                if c["card_type"] == "sample"
                            ) or "unknown"
                            parsed, _ = client.generate_vision_json_tolerant(
                                "You are analyzing fiber material literature figures and tables. "
                                "Extract ALL performance data (tensile strength, modulus, elongation, "
                                "thermal conductivity, contact angle, density, LOI, dielectric constant, etc.) "
                                "visible in the images. For each value: sample_id, performance_metric, "
                                "performance_value, performance_unit. Output JSON: "
                                "{'vision_performances': [{'sample_id': '...', 'performance_metric': '...', "
                                "'performance_value': '...', 'performance_unit': '...', 'source_location': 'figure/table'}, ...]}",
                                f"Known samples: [{sample_hint}]. Find performance data for these or any "
                                f"additional samples visible in the figures/tables.",
                                [r["image"] for r in rendered],
                                max_tokens=2500,
                            )
                            for p in parsed.get("vision_performances") or parsed.get("_items") or []:
                                s_id = p.get("sample_id", "")
                                metric = p.get("performance_metric", "")
                                val = p.get("performance_value", "")
                                if s_id and metric and val:
                                    raw_evidence_cards.append({
                                        "card_type": "performance",
                                        "related_sample_id": s_id,
                                        "source_location": p.get("source_location", "vision_page"),
                                        "evidence_text": f"Vision-extracted: {metric}={val} {p.get('performance_unit', '')}",
                                        "confidence": 0.72,
                                        "normalized_payload": json.dumps(p, ensure_ascii=False),
                                    })
                    except Exception as e:
                        print(f"Warning: Vision extraction failed: {e}")

        # -- Step 4: Fallback if no cards at all --
        if not raw_evidence_cards and front_pages:
            paper_info["doi_or_url"] = paper_info.get("doi_or_url", "")
            paper_info["year"] = paper_info.get("year", 2026)
            first_text = front_pages[0]["extracted_text"][:2000]
            raw_evidence_cards.append({
                "card_type": "other",
                "related_sample_id": "",
                "source_location": "front_page",
                "evidence_text": first_text,
                "confidence": 0.10,
                "normalized_payload": json.dumps({
                    "note": "No structured data could be extracted. Full text available for manual review."
                }),
            })

        # -- Step 5: Save paper metadata --
        paper.paper_title = paper_info.get("paper_title", paper.original_filename)
        paper.doi_or_url = paper_info.get("doi_or_url", "")
        try:
            paper.year = int(paper_info.get("year", 2026))
        except (ValueError, TypeError):
            paper.year = 2026
        paper.journal = paper_info.get("journal", "")
        db.add(paper)

        # -- Step 6: Group evidence cards by sample_id --
        grouped_cards = defaultdict(list)
        for card in raw_evidence_cards:
            s_id = card.get("related_sample_id", "").strip()
            if s_id:
                grouped_cards[s_id].append(card)

        # -- Step 7: Build candidate records from grouped cards --
        def _merge_payloads(card_list):
            """Merge multiple evidence cards of same type — first non-empty value wins."""
            merged = {}
            for c in card_list:
                payload = _tolerant_parse_json(c["normalized_payload"])
                for k, v in payload.items():
                    if v and not merged.get(k):
                        merged[k] = v
            return merged

        candidates_to_qc = []
        idx = 0

        for s_id, cards in grouped_cards.items():
            sample_cards = [c for c in cards if c["card_type"] == "sample"]
            s_payload = _merge_payloads(sample_cards)
            sample_card = sample_cards[0] if sample_cards else None

            composition_cards = [c for c in cards if c["card_type"] == "composition"]
            c_payload = _merge_payloads(composition_cards)

            process_cards = [c for c in cards if c["card_type"] == "process"]
            p_payload = _merge_payloads(process_cards)

            structure_cards = [c for c in cards if c["card_type"] == "structure"]
            st_payload = _merge_payloads(structure_cards)

            perf_cards = [c for c in cards if c["card_type"] == "performance"]
            if not perf_cards:
                perf_cards = [{
                    "normalized_payload": json.dumps({
                        "performance_metric": "", "performance_value": "",
                        "performance_unit": "",
                    }),
                    "source_location": "experimental",
                    "evidence_text": "Sample identified in text but no performance data extracted.",
                    "confidence": 0.30,
                }]

            for p_card in perf_cards:
                idx += 1
                pf_payload = _tolerant_parse_json(p_card["normalized_payload"])

                metric = pf_payload.get("performance_metric", "")
                category = pf_payload.get("performance_category", "")
                if not category:
                    ml = metric.lower()
                    if any(k in ml for k in ("tensile", "compressive", "strength", "modulus", "elongation")):
                        category = "mechanical"
                    elif any(k in ml for k in ("thermal", "conductivity", "temperature")):
                        category = "thermal"
                    elif any(k in ml for k in ("contact_angle", "contact angle", "wca")):
                        category = "hydrophobicity"
                    elif any(k in ml for k in ("dielectric", "permittivity", "loss tangent")):
                        category = "dielectric"
                    elif any(k in ml for k in ("porosity", "density", "shrinkage")):
                        category = "physical"
                    else:
                        category = "physical"

                candidate_dict = {
                    "id": idx,
                    "source_paper_id": paper_id,
                    "project_id": paper.project_id,
                    "record_id": f"V6-EXT-{paper_id}-{idx}",
                    "paper_title": paper.paper_title or "",
                    "doi_or_url": paper.doi_or_url or "",
                    "year": str(paper.year) if paper.year else "",
                    "journal": paper.journal or "",
                    "sample_group_id": s_payload.get("sample_group_id", "Group-A"),
                    "sample_id": s_id,
                    "material_system": s_payload.get("material_system", ""),
                    "fiber_type": s_payload.get("fiber_type", ""),
                    "variable_name": s_payload.get("variable_name", ""),
                    "variable_value": s_payload.get("variable_value", ""),
                    "variable_unit": s_payload.get("variable_unit", ""),
                    "composition_expression": (
                        c_payload.get("composition_expression", "")
                        or s_payload.get("composition", "")
                        or s_payload.get("composition_expression", "")
                    ),
                    "matrix_name": c_payload.get("matrix_name", ""),
                    "matrix_content": c_payload.get("matrix_content", ""),
                    "matrix_unit": c_payload.get("matrix_unit", ""),
                    "additive_expression": c_payload.get("additive_expression", ""),
                    "solvent_or_aid": c_payload.get("solvent_or_aid", ""),
                    "process_route": p_payload.get("process_route", ""),
                    "spinning_method": p_payload.get("spinning_method", ""),
                    "process_parameters": p_payload.get("process_parameters", ""),
                    "post_treatment": p_payload.get("post_treatment", ""),
                    "structure_methods": st_payload.get("structure_methods", ""),
                    "structure_features": st_payload.get("structure_features", ""),
                    "performance_category": category,
                    "performance_metric": metric,
                    "performance_value": pf_payload.get("performance_value", ""),
                    "performance_unit": pf_payload.get("performance_unit", ""),
                    "performance_method": pf_payload.get("performance_method", ""),
                    "performance_condition": pf_payload.get("performance_condition", ""),
                    "source_location": p_card.get("source_location", ""),
                    "ai_confidence": (
                        (float(sample_card.get("confidence", 0.5))
                         + float(p_card.get("confidence", 0.5))) / 2.0
                        if sample_card
                        else float(p_card.get("confidence", 0.5))
                    ),
                }
                candidate_dict["_associated_cards"] = cards
                candidates_to_qc.append(candidate_dict)

        # -- Step 8: QC + save candidate records + evidence items --
        final_candidates_saved = []
        for c in candidates_to_qc:
            status, suggestions = V6ExtractorService.run_row_level_qc(
                c, candidates_to_qc
            )

            rec = CandidateRecord(
                project_id=c["project_id"],
                source_paper_id=c["source_paper_id"],
                record_id=c["record_id"],
                paper_title=c["paper_title"],
                doi_or_url=c["doi_or_url"],
                year=c["year"],
                journal=c["journal"],
                sample_group_id=c["sample_group_id"],
                sample_id=c["sample_id"],
                material_system=c["material_system"],
                fiber_type=c.get("fiber_type", ""),
                variable_name=c.get("variable_name", ""),
                variable_value=c.get("variable_value", ""),
                variable_unit=c.get("variable_unit", ""),
                composition_expression=c["composition_expression"],
                matrix_name=c.get("matrix_name", ""),
                matrix_content=c.get("matrix_content", ""),
                matrix_unit=c.get("matrix_unit", ""),
                additive_expression=c.get("additive_expression", ""),
                solvent_or_aid=c.get("solvent_or_aid", ""),
                process_route=c["process_route"],
                spinning_method=c["spinning_method"],
                process_parameters=c["process_parameters"],
                post_treatment=c.get("post_treatment", ""),
                structure_methods=c["structure_methods"],
                structure_features=c["structure_features"],
                performance_category=c["performance_category"],
                performance_metric=c["performance_metric"],
                performance_value=c["performance_value"],
                performance_unit=c["performance_unit"],
                performance_method=c.get("performance_method", ""),
                performance_condition=c.get("performance_condition", ""),
                source_location=c["source_location"],
                ai_confidence=c["ai_confidence"],
                review_status=status,
                reviewer_comment=(
                    "; ".join(suggestions)
                    if suggestions
                    else "已通过系统自动化质检校验"
                ),
            )
            db.add(rec)
            await db.flush()

            for associated_card in c["_associated_cards"]:
                db.add(EvidenceItem(
                    project_id=c["project_id"],
                    paper_id=c["source_paper_id"],
                    candidate_record_id=rec.id,
                    source_type=associated_card["card_type"],
                    source_location=associated_card["source_location"],
                    evidence_text=(
                        associated_card["evidence_text"][:2000]
                        if associated_card.get("evidence_text")
                        else "No text excerpt."
                    ),
                    normalized_payload=associated_card["normalized_payload"],
                    confidence=float(associated_card["confidence"]),
                ))

            final_candidates_saved.append(rec)

        _emit("saving", 90)

        paper.status = "review"
        db.add(paper)
        await db.commit()

        _emit("completed", 100)

        return {
            "success": True,
            "pages_processed": len(pages),
            "table_count": len(tables),
            "candidates_created": len(final_candidates_saved),
        }

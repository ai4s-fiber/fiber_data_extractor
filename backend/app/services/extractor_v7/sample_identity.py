"""Generic sample ID alias clustering and canonicalization (all papers)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.services.grouping import normalize_for_match, normalize_sample_id

_GENERIC_STOPWORDS = frozenset({
    "sample", "samples", "fiber", "fibers", "film", "films", "composite",
    "composites", "material", "materials", "specimen", "specimens", "the",
    "with", "and", "based", "prepared", "fabric", "fabrics", "device",
})

_LOADING_RE = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*(?:wt\.?%|wt%|vol\.?%|mol\.?%)\s*([a-z0-9][\w\-]*)"
)
_EMBEDDED_LOADING_RE = re.compile(
    r"(?i)(?:^|[_\-\s])(\d+(?:\.\d+)?)\s*wt\s*([a-z0-9][\w\-]*)"
)
_RATIO_RE = re.compile(r"(?i)(\d+)\s*:\s*(\d+)")


def _token_set(text: str) -> set[str]:
    norm = normalize_for_match(text)
    tokens = {t for t in re.split(r"[\s_/\-]+", norm) if len(t) >= 2}
    return tokens - _GENERIC_STOPWORDS


def _loading_signature(text: str) -> str | None:
    for pattern in (_LOADING_RE, _EMBEDDED_LOADING_RE):
        match = pattern.search(text or "")
        if match:
            filler = normalize_for_match(match.group(2)).rstrip("s")
            return f"{match.group(1)}wt_{filler}"
    return None


def _ratio_signature(text: str) -> str | None:
    match = _RATIO_RE.search(text or "")
    if not match:
        return None
    return f"ratio_{match.group(1)}_{match.group(2)}"


def _alias_variants(sample_id: str) -> set[str]:
    sid = normalize_sample_id(sample_id)
    if not sid:
        return set()
    variants = {sid}
    norm = normalize_for_match(sid)
    variants.add(norm)
    loading = _loading_signature(sid)
    if loading:
        variants.add(loading)
    ratio = _ratio_signature(sid)
    if ratio:
        variants.add(ratio)
    compact = re.sub(r"[^a-z0-9]+", "", norm)
    if compact:
        variants.add(compact)
    return variants


def _parse_aliases_field(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(";") if part.strip()]


def _pair_similarity(a: str, b: str) -> float:
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    la, lb = _loading_signature(a), _loading_signature(b)
    if la and lb and la == lb:
        return 0.96
    ra, rb = _ratio_signature(a), _ratio_signature(b)
    if ra and rb and ra == rb:
        return 0.92
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    union = len(ta | tb)
    return overlap / union if union else 0.0


def _canonical_score(sample_id: str, mention_count: int, has_aliases: bool) -> float:
    sid = normalize_sample_id(sample_id)
    score = mention_count * 2.0
    if has_aliases:
        score += 3.0
    if _loading_signature(sid):
        score += 2.0
    if len(sid) <= 40:
        score += 1.0
    if len(sid) > 80:
        score -= 4.0
    if sid.count(" ") > 6:
        score -= 3.0
    return score


def build_sample_alias_map(
    sample_mentions: list[dict],
    holistic_samples: list[dict] | None = None,
    sample_cards: list[dict] | None = None,
    *,
    similarity_threshold: float = 0.88,
) -> dict[str, str]:
    """Cluster similar sample IDs and return alias -> canonical_id mapping."""
    mention_counts: dict[str, int] = defaultdict(int)
    alias_sets: dict[str, set[str]] = defaultdict(set)
    all_ids: set[str] = set()

    def register(sample_id: str, aliases: list[str] | None = None) -> None:
        sid = normalize_sample_id(sample_id)
        if not sid or len(sid) < 2:
            return
        all_ids.add(sid)
        mention_counts[sid] += 1
        for alias in aliases or []:
            alias_id = normalize_sample_id(alias)
            if alias_id and alias_id != sid:
                all_ids.add(alias_id)
                alias_sets[sid].add(alias_id)
                mention_counts[alias_id] += 1

    for mention in sample_mentions:
        sid = normalize_sample_id(
            mention.get("normalized_sample_id") or mention.get("mention_text") or ""
        )
        register(sid, mention.get("aliases") or [])

    for sample in holistic_samples or []:
        register(sample.get("sample_id") or "", sample.get("aliases") or [])

    for card in sample_cards or []:
        register(card.get("sample_id") or "", _parse_aliases_field(card.get("sample_aliases")))

    ids = sorted(all_ids, key=lambda s: (-mention_counts[s], len(s)))
    parent: dict[str, str] = {sid: sid for sid in ids}

    def find(sid: str) -> str:
        while parent[sid] != sid:
            parent[sid] = parent[parent[sid]]
            sid = parent[sid]
        return sid

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        score_a = _canonical_score(ra, mention_counts[ra], bool(alias_sets[ra]))
        score_b = _canonical_score(rb, mention_counts[rb], bool(alias_sets[rb]))
        if score_a >= score_b:
            parent[rb] = ra
        else:
            parent[ra] = rb

    id_list = list(ids)
    for i, sid_a in enumerate(id_list):
        for sid_b in id_list[i + 1:]:
            if _pair_similarity(sid_a, sid_b) >= similarity_threshold:
                union(sid_a, sid_b)
                continue
            linked = False
            for alias in alias_sets.get(sid_a, ()):
                if _pair_similarity(alias, sid_b) >= similarity_threshold:
                    union(sid_a, sid_b)
                    linked = True
                    break
            if linked:
                continue
            for alias in alias_sets.get(sid_b, ()):
                if _pair_similarity(sid_a, alias) >= similarity_threshold:
                    union(sid_a, sid_b)
                    break

    clusters: dict[str, set[str]] = defaultdict(set)
    for sid in ids:
        clusters[find(sid)].add(sid)

    mapping: dict[str, str] = {}
    for canonical, members in clusters.items():
        best = max(
            members,
            key=lambda s: _canonical_score(s, mention_counts[s], bool(alias_sets[s])),
        )
        for member in members:
            mapping[member] = best
    return mapping


def apply_sample_alias_map(
    alias_map: dict[str, str],
    *,
    sample_mentions: list[dict] | None = None,
    facts: list[dict] | None = None,
    sample_cards: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Rewrite sample IDs using alias_map across pipeline artifacts."""

    def remap_id(value: str | None) -> str:
        sid = normalize_sample_id(value or "")
        if not sid:
            return ""
        return alias_map.get(sid, sid)

    mentions = []
    for mention in sample_mentions or []:
        item = dict(mention)
        raw = item.get("normalized_sample_id") or item.get("mention_text") or ""
        canonical = remap_id(raw)
        if canonical:
            item["normalized_sample_id"] = canonical
            aliases = list(item.get("aliases") or [])
            if raw and remap_id(raw) != normalize_sample_id(raw):
                aliases.append(normalize_sample_id(raw))
            item["aliases"] = sorted({normalize_sample_id(a) for a in aliases if a and normalize_sample_id(a) != canonical})
        mentions.append(item)

    updated_facts = []
    for fact in facts or []:
        item = dict(fact)
        if item.get("assigned_sample_id"):
            item["assigned_sample_id"] = remap_id(item["assigned_sample_id"])
        candidates = item.get("candidate_sample_ids") or []
        if isinstance(candidates, list):
            item["candidate_sample_ids"] = [remap_id(c) for c in candidates if remap_id(c)]
        updated_facts.append(item)

    cards_by_id: dict[str, dict] = {}
    for card in sample_cards or []:
        sid = remap_id(card.get("sample_id"))
        if not sid:
            continue
        if sid not in cards_by_id:
            new_card = dict(card)
            new_card["sample_id"] = sid
            existing_aliases = set(_parse_aliases_field(card.get("sample_aliases")))
            raw_sid = normalize_sample_id(card.get("sample_id") or "")
            if raw_sid and raw_sid != sid:
                existing_aliases.add(raw_sid)
            new_card["sample_aliases"] = json.dumps(sorted(existing_aliases), ensure_ascii=False) if existing_aliases else ""
            cards_by_id[sid] = new_card
        else:
            target = cards_by_id[sid]
            for field in (
                "material_system", "fiber_type", "composition_expression", "matrix_name",
                "process_route", "spinning_method", "process_parameters", "structure_methods",
                "structure_features", "evidence_text",
            ):
                if not target.get(field) and card.get(field):
                    target[field] = card[field]
            extra_aliases = set(_parse_aliases_field(card.get("sample_aliases")))
            raw_sid = normalize_sample_id(card.get("sample_id") or "")
            if raw_sid:
                extra_aliases.add(raw_sid)
            current = set(_parse_aliases_field(target.get("sample_aliases")))
            merged = sorted(current | extra_aliases - {sid})
            target["sample_aliases"] = json.dumps(merged, ensure_ascii=False) if merged else ""

    return mentions, updated_facts, list(cards_by_id.values())


def merge_sample_identities(
    sample_mentions: list[dict],
    facts: list[dict],
    sample_cards: list[dict],
    holistic_samples: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build alias clusters and apply canonical sample IDs."""
    alias_map = build_sample_alias_map(
        sample_mentions,
        holistic_samples=holistic_samples,
        sample_cards=sample_cards,
    )
    if not alias_map:
        return sample_mentions, facts, sample_cards
    return apply_sample_alias_map(
        alias_map,
        sample_mentions=sample_mentions,
        facts=facts,
        sample_cards=sample_cards,
    )

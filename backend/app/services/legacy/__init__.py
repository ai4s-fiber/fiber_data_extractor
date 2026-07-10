"""Deprecated V6 extractor — retained for legacy scripts and QC helpers only."""

import warnings

warnings.warn(
    "app.services.legacy.v6_extractor is deprecated; production uses extractor_v7",
    DeprecationWarning,
    stacklevel=2,
)

from app.services.legacy.v6_extractor import V6ExtractorService

__all__ = ["V6ExtractorService"]

"""Extraction pipeline exceptions."""


class ExtractionCancelled(Exception):
    """Raised when a running extraction job is cancelled by the user."""


class NoExtractableResults(RuntimeError):
    """Raised when quantitative result evidence produced no usable records."""

    error_code = "no_extractable_results"

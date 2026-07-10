"""Extraction pipeline exceptions."""


class ExtractionCancelled(Exception):
    """Raised when a running extraction job is cancelled by the user."""

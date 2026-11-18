"""Shared contracts and services for the CAD and CAE pipelines."""

from .schema import (
    CaeEnvelope,
    CadEnvelope,
    dump_cae,
    dump_cad,
    validate_cae,
    validate_cad,
)
from .config import VisualSettings
from .vision import describe, make_captioner

__all__ = [
    "CaeEnvelope",
    "CadEnvelope",
    "VisualSettings",
    "describe",
    "dump_cae",
    "dump_cad",
    "make_captioner",
    "validate_cae",
    "validate_cad",
]

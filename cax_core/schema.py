"""Canonical JSON schemas shared by every supported CAX input format."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA_VERSION = "2.1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SourceFile(StrictModel):
    role: str
    path: str
    format: str
    bytes: int
    sha256: Optional[str] = None
    hash_skipped: bool = False
    hash_skip_reason: Optional[str] = None


class ParserInfo(StrictModel):
    name: str
    version: str


class Source(StrictModel):
    primary_format: str
    files: list[SourceFile] = Field(default_factory=list)
    parser: ParserInfo
    parsed_at: str
    solver: Optional[str] = None
    dataset: Optional[dict[str, Any]] = None


class Validation(StrictModel):
    is_valid: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class Provenance(StrictModel):
    source_files: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


class Capabilities(StrictModel):
    geometry: bool = False
    mesh: bool = False
    setup: bool = False
    results: bool = False
    render: bool = False
    vision: bool = False


class ClassificationFacet(StrictModel):
    key: str
    label_zh: str
    layer: Literal["data", "metadata"]
    category: Literal[
        "geometry",
        "topology",
        "feature",
        "assembly",
        "simulation_method",
        "material",
        "boundary_condition",
        "mesh",
        "result",
        "derived_metric",
        "visual_semantic",
        "asset",
        "identity",
        "source_file",
        "technical",
        "lineage",
        "quality",
        "capability",
    ]
    subcategory: Optional[str] = None
    origin: Literal[
        "declared",
        "extracted",
        "computed",
        "derived",
        "inferred",
        "observed",
    ]
    value_type: Literal[
        "scalar",
        "string",
        "boolean",
        "table",
        "array",
    ]
    json_pointer: str
    unit: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    item_count: Optional[int] = Field(default=None, ge=0)


class ClassificationSummary(StrictModel):
    layer: Literal["data", "metadata"]
    category: str
    label_zh: str
    field_count: int = Field(ge=0)


class Classification(StrictModel):
    taxonomy_version: str
    facets: list[ClassificationFacet] = Field(default_factory=list)
    summary: list[ClassificationSummary] = Field(default_factory=list)


class Meta(StrictModel):
    validation: Validation = Field(default_factory=lambda: Validation(is_valid=True))
    provenance: Provenance = Field(default_factory=Provenance)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    tags: list[str] = Field(default_factory=list)
    domain: str = "commercial_aircraft"
    classification: Optional[Classification] = None


class BBox(StrictModel):
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float


class MassProps(StrictModel):
    volume: float
    surface_area: float


class Topology(StrictModel):
    solids: int
    shells: int
    faces: int
    edges: int
    vertices: int


class FaceTypeCount(StrictModel):
    type: str
    count: int


class Feature(StrictModel):
    id: str
    type: str
    confidence: float = Field(ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)
    face_count: int = 0


class Part(StrictModel):
    id: str
    name: str
    bbox: BBox
    mass_props: MassProps
    is_closed: bool
    topology: Topology
    face_types: list[FaceTypeCount] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)


class AssemblyInstance(StrictModel):
    id: str
    name: str
    ref: str
    placement: list[float] = Field(default_factory=list)
    children: list["AssemblyInstance"] = Field(default_factory=list)


class Assembly(StrictModel):
    instances: list[AssemblyInstance] = Field(default_factory=list)


class RenderAsset(StrictModel):
    view: str
    path: Optional[str] = None
    mime_type: Optional[str] = None
    bytes: Optional[int] = None
    sha256: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None


class VisualObject(StrictModel):
    name_zh: str = ""
    name_en: str = ""
    count: int = 1
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class VisualProfile(StrictModel):
    primary_class: str = "unknown"
    display_name_zh: str = ""
    aliases_zh: list[str] = Field(default_factory=list)
    aliases_en: list[str] = Field(default_factory=list)
    shape_traits: list[str] = Field(default_factory=list)
    functional_guess: str = ""
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    search_text_zh: str = ""
    search_text_en: str = ""
    objects: list[VisualObject] = Field(default_factory=list)


class VisualResult(StrictModel):
    kind: Literal["vlm", "rule_based", "rule_based_fallback", "not_requested"]
    summary: str
    model: str
    provider: str = "deepseek"
    analyzed_at: Optional[str] = None
    profile: Optional[VisualProfile] = None
    error: Optional[str] = None


class Product(StrictModel):
    name: Optional[str] = None
    author: Optional[str] = None
    units: Optional[str] = None
    schema_name: Optional[str] = Field(default=None, alias="schema")
    standard: Optional[str] = None


class CadContent(StrictModel):
    product: Product
    parts: list[Part]
    assembly: Assembly = Field(default_factory=Assembly)
    rendered_views: list[RenderAsset] = Field(default_factory=list)
    visual: Optional[VisualResult] = None


class CadEnvelope(StrictModel):
    schema_version: str
    data_type: Literal["cad"]
    source: Source
    meta: Meta
    content: CadContent


class Mesh(StrictModel):
    dimension: int
    nodes: int
    faces: int
    cells: int
    boundary_faces: int
    interior_faces: int
    bbox: BBox
    extent: dict[str, float] = Field(default_factory=dict)
    cell_types: dict[str, int] = Field(default_factory=dict)
    zones: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)


class FieldResult(StrictModel):
    field: str
    semantic: str
    zone: Optional[str] = None
    unit: Optional[str] = None
    note: Optional[str] = None
    phase: int = 0
    location: str
    coverage: float = 1.0
    defined_values: Optional[int] = None
    undefined_values: Optional[int] = None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    components: Optional[list[str]] = None
    component_count: Optional[int] = None
    stats: dict[str, Any] = Field(default_factory=dict)


class Analysis(StrictModel):
    physics: str = "fluid_flow"
    solver: str
    models: dict[str, Any] = Field(default_factory=dict)
    phases: list[dict[str, Any]] = Field(default_factory=list)
    time: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, Any] = Field(default_factory=dict)
    operating_conditions: dict[str, Any] = Field(default_factory=dict)


class CaeContent(StrictModel):
    analysis: Analysis
    mesh: Mesh
    setup: dict[str, Any] = Field(default_factory=dict)
    field_catalog: dict[str, Any] = Field(default_factory=dict)
    results: list[FieldResult] = Field(default_factory=list)
    zone_results: list[dict[str, Any]] = Field(default_factory=list)
    derived: dict[str, Any] = Field(default_factory=dict)
    rendered_views: list[RenderAsset] = Field(default_factory=list)
    visual: Optional[VisualResult] = None


class CaeEnvelope(StrictModel):
    schema_version: str
    data_type: Literal["cae"]
    source: Source
    meta: Meta
    content: CaeContent


Envelope = TypeAdapter[Union[CadEnvelope, CaeEnvelope]]


def validate_cad(doc: dict[str, Any]) -> CadEnvelope:
    return CadEnvelope.model_validate(doc)


def validate_cae(doc: dict[str, Any]) -> CaeEnvelope:
    return CaeEnvelope.model_validate(doc)


def dump_cad(doc: dict[str, Any]) -> dict[str, Any]:
    return validate_cad(doc).model_dump(mode="json", by_alias=True)


def dump_cae(doc: dict[str, Any]) -> dict[str, Any]:
    return validate_cae(doc).model_dump(mode="json", by_alias=True)


AssemblyInstance.model_rebuild()
Assembly.model_rebuild()

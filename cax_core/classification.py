"""Deterministic semantic classification for canonical CAX documents.

The classification is a catalog over fields in the canonical document. It does
not duplicate values; each facet points at the source value with a JSON Pointer.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Optional

TAXONOMY_VERSION = "cax-classification/1.0"

DATA_CATEGORIES = (
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
)

METADATA_CATEGORIES = (
    "identity",
    "source_file",
    "technical",
    "lineage",
    "quality",
    "capability",
)

CATEGORY_LABELS = {
    "geometry": "几何度量",
    "topology": "拓扑结构",
    "feature": "设计特征",
    "assembly": "装配关系",
    "simulation_method": "仿真方法",
    "material": "材料与介质",
    "boundary_condition": "边界与工况",
    "mesh": "网格与离散",
    "result": "仿真结果",
    "derived_metric": "派生工程指标",
    "visual_semantic": "视觉语义",
    "asset": "派生资源",
    "identity": "资源身份",
    "source_file": "来源文件",
    "technical": "技术信息",
    "lineage": "处理链路",
    "quality": "质量信息",
    "capability": "能力声明",
}

LAYER_LABELS = {
    "data": "数据",
    "metadata": "元数据",
}

_SETUP_BOUNDARY_KEYS = (
    "boundary_conditions",
    "constraints",
    "loads",
    "aero",
    "initial_conditions",
    "surface",
)


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _join(base: str, *tokens: str | int) -> str:
    parts = [base.rstrip("/")]
    parts.extend(_escape(str(token)) for token in tokens)
    return "/".join(parts)


def _resolve(doc: dict[str, Any], pointer: str) -> tuple[bool, Any]:
    current: Any = doc
    if pointer == "":
        return True, current
    if not pointer.startswith("/"):
        return False, None
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "scalar"
    if isinstance(value, dict):
        return "table"
    if isinstance(value, list):
        return "array"
    return "string"


def _item_count(value: Any) -> Optional[int]:
    if isinstance(value, (list, dict)):
        return len(value)
    return None


class _FacetBuilder:
    def __init__(self, doc: dict[str, Any]):
        self.doc = doc
        self.facets: list[dict[str, Any]] = []
        self._keys: set[str] = set()

    def add(
        self,
        key: str,
        label_zh: str,
        layer: str,
        category: str,
        pointer: str,
        *,
        subcategory: Optional[str] = None,
        origin: str = "extracted",
        unit: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> None:
        if key in self._keys:
            return
        exists, value = _resolve(self.doc, pointer)
        if not exists or value is None:
            return
        facet: dict[str, Any] = {
            "key": key,
            "label_zh": label_zh,
            "layer": layer,
            "category": category,
            "origin": origin,
            "value_type": _value_type(value),
            "json_pointer": pointer,
        }
        if subcategory:
            facet["subcategory"] = subcategory
        if unit:
            facet["unit"] = unit
        if confidence is not None:
            facet["confidence"] = max(0.0, min(1.0, float(confidence)))
        count = _item_count(value)
        if count is not None:
            facet["item_count"] = count
        self.facets.append(facet)
        self._keys.add(key)


def _cad_unit(doc: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    raw = str(
        ((doc.get("content") or {}).get("product") or {}).get("units") or ""
    ).strip().upper()
    aliases = {
        "MILLIMETRE": "mm",
        "MILLIMETER": "mm",
        "MM": "mm",
        "CENTIMETRE": "cm",
        "CENTIMETER": "cm",
        "CM": "cm",
        "METER": "m",
        "METRE": "m",
        "M": "m",
        "INCH": "in",
        "IN": "in",
    }
    unit = aliases.get(raw)
    if not unit:
        return None, None
    return unit, f"{unit}³"


def _add_visual(builder: _FacetBuilder, content: dict[str, Any]) -> None:
    visual = content.get("visual") or {}
    if not visual:
        return
    kind = str(visual.get("kind") or "")
    confidence = (visual.get("profile") or {}).get("confidence")
    origin = "inferred" if kind in {"vlm", "rule_based", "rule_based_fallback"} else "extracted"
    builder.add(
        "visual.profile",
        "视觉画像",
        "data",
        "visual_semantic",
        "/content/visual/profile",
        origin=origin,
        confidence=confidence if isinstance(confidence, (int, float)) else None,
    )
    builder.add(
        "visual.summary",
        "视觉摘要",
        "data",
        "visual_semantic",
        "/content/visual/summary",
        origin=origin,
    )
    builder.add(
        "visual.model",
        "视觉模型",
        "metadata",
        "technical",
        "/content/visual/model",
        origin="observed",
    )
    builder.add(
        "visual.provider",
        "视觉服务提供方",
        "metadata",
        "technical",
        "/content/visual/provider",
        origin="observed",
    )
    builder.add(
        "visual.analyzed_at",
        "视觉分析时间",
        "metadata",
        "lineage",
        "/content/visual/analyzed_at",
        origin="observed",
    )
    builder.add(
        "visual.error",
        "视觉分析错误",
        "metadata",
        "quality",
        "/content/visual/error",
        origin="observed",
    )


def _add_shared(doc: dict[str, Any], builder: _FacetBuilder) -> None:
    builder.add(
        "schema.version",
        "Schema 版本",
        "metadata",
        "technical",
        "/schema_version",
        origin="declared",
    )
    builder.add(
        "resource.data_type",
        "资源数据类型",
        "metadata",
        "identity",
        "/data_type",
        origin="declared",
    )

    source = doc.get("source") or {}
    builder.add(
        "source.format",
        "主输入格式",
        "metadata",
        "source_file",
        "/source/primary_format",
        origin="declared",
    )
    builder.add(
        "source.parser",
        "解析器",
        "metadata",
        "technical",
        "/source/parser",
        origin="declared",
    )
    builder.add(
        "source.parsed_at",
        "解析时间",
        "metadata",
        "lineage",
        "/source/parsed_at",
        origin="observed",
    )
    builder.add(
        "source.solver",
        "求解器",
        "metadata",
        "technical",
        "/source/solver",
        origin="declared",
    )
    builder.add(
        "source.dataset",
        "数据集上下文",
        "metadata",
        "source_file",
        "/source/dataset",
        origin="declared",
    )
    for index, item in enumerate(source.get("files") or []):
        role = str(item.get("role") or index)
        base = f"/source/files/{index}"
        for field, label, category in (
            ("path", "来源路径", "source_file"),
            ("format", "来源格式", "source_file"),
            ("bytes", "来源大小", "source_file"),
            ("sha256", "文件哈希", "source_file"),
        ):
            builder.add(
                f"source.file.{index}.{field}",
                f"{role} {label}",
                "metadata",
                category,
                f"{base}/{field}",
                origin="observed" if field in {"bytes", "sha256"} else "declared",
                unit="byte" if field == "bytes" else None,
            )

    meta = doc.get("meta") or {}
    builder.add(
        "meta.validation",
        "校验结果",
        "metadata",
        "quality",
        "/meta/validation",
        origin="observed",
    )
    builder.add(
        "meta.provenance",
        "处理溯源",
        "metadata",
        "lineage",
        "/meta/provenance",
        origin="observed",
    )
    builder.add(
        "meta.capabilities",
        "数据能力",
        "metadata",
        "capability",
        "/meta/capabilities",
        origin="declared",
    )
    builder.add(
        "meta.tags",
        "业务标签",
        "metadata",
        "identity",
        "/meta/tags",
        origin="declared",
    )
    builder.add(
        "meta.domain",
        "业务领域",
        "metadata",
        "identity",
        "/meta/domain",
        origin="declared",
    )


def _classify_cad(doc: dict[str, Any], builder: _FacetBuilder) -> None:
    content = doc.get("content") or {}
    product = content.get("product") or {}
    length_unit, volume_unit = _cad_unit(doc)
    builder.add(
        "cad.product.name",
        "产品名称",
        "metadata",
        "identity",
        "/content/product/name",
        origin="declared",
    )
    builder.add(
        "cad.product.author",
        "产品作者",
        "metadata",
        "identity",
        "/content/product/author",
        origin="declared",
    )
    builder.add(
        "cad.product.units",
        "产品单位",
        "metadata",
        "technical",
        "/content/product/units",
        origin="declared",
    )
    builder.add(
        "cad.product.schema",
        "STEP Schema",
        "metadata",
        "technical",
        "/content/product/schema",
        origin="declared",
    )
    builder.add(
        "cad.product.standard",
        "交换标准",
        "metadata",
        "technical",
        "/content/product/standard",
        origin="declared",
    )
    del product

    for index, part in enumerate(content.get("parts") or []):
        base = f"/content/parts/{index}"
        part_name = str(part.get("name") or index)
        builder.add(
            f"cad.part.{index}.name",
            f"{part_name} 名称",
            "metadata",
            "identity",
            f"{base}/name",
            origin="declared",
        )
        builder.add(
            f"cad.part.{index}.bbox",
            f"{part_name} 包围盒",
            "data",
            "geometry",
            f"{base}/bbox",
            subcategory="dimensions",
            origin="computed",
            unit=length_unit,
        )
        builder.add(
            f"cad.part.{index}.volume",
            f"{part_name} 体积",
            "data",
            "geometry",
            f"{base}/mass_props/volume",
            subcategory="mass_properties",
            origin="computed",
            unit=volume_unit,
        )
        builder.add(
            f"cad.part.{index}.surface_area",
            f"{part_name} 表面积",
            "data",
            "geometry",
            f"{base}/mass_props/surface_area",
            subcategory="mass_properties",
            origin="computed",
            unit=f"{length_unit}²" if length_unit else None,
        )
        builder.add(
            f"cad.part.{index}.is_closed",
            f"{part_name} 闭合状态",
            "data",
            "topology",
            f"{base}/is_closed",
            origin="computed",
        )
        builder.add(
            f"cad.part.{index}.topology",
            f"{part_name} 拓扑",
            "data",
            "topology",
            f"{base}/topology",
            origin="computed",
        )
        builder.add(
            f"cad.part.{index}.face_types",
            f"{part_name} 面类型",
            "data",
            "feature",
            f"{base}/face_types",
            subcategory="surface_types",
            origin="computed",
        )
        builder.add(
            f"cad.part.{index}.features",
            f"{part_name} 设计特征",
            "data",
            "feature",
            f"{base}/features",
            origin="computed",
        )

    builder.add(
        "cad.assembly",
        "装配关系",
        "data",
        "assembly",
        "/content/assembly/instances",
        origin="computed",
    )
    builder.add(
        "cad.rendered_views",
        "渲染资源",
        "data",
        "asset",
        "/content/rendered_views",
        origin="computed",
    )


def _classify_cae(doc: dict[str, Any], builder: _FacetBuilder) -> None:
    content = doc.get("content") or {}
    analysis = content.get("analysis") or {}
    setup = content.get("setup") or {}

    builder.add(
        "cae.physics",
        "物理类型",
        "data",
        "simulation_method",
        "/content/analysis/physics",
        origin="declared",
    )
    builder.add(
        "cae.models",
        "仿真模型",
        "data",
        "simulation_method",
        "/content/analysis/models",
        origin="declared",
    )
    builder.add(
        "cae.time",
        "时间模型",
        "data",
        "simulation_method",
        "/content/analysis/time",
        origin="declared",
    )
    builder.add(
        "cae.solver",
        "求解器版本",
        "metadata",
        "technical",
        "/content/analysis/solver",
        origin="declared",
    )
    builder.add(
        "cae.units",
        "单位制",
        "metadata",
        "technical",
        "/content/analysis/units",
        origin="declared",
    )
    builder.add(
        "cae.phases",
        "相与介质",
        "data",
        "material",
        "/content/analysis/phases",
        origin="declared",
    )
    builder.add(
        "cae.operating_conditions",
        "运行工况",
        "data",
        "boundary_condition",
        "/content/analysis/operating_conditions",
        origin="declared",
    )
    builder.add(
        "cae.mesh",
        "网格摘要",
        "data",
        "mesh",
        "/content/mesh",
        origin="computed",
    )
    builder.add(
        "cae.mesh.bbox",
        "计算域包围盒",
        "data",
        "geometry",
        "/content/mesh/bbox",
        subcategory="dimensions",
        origin="computed",
    )
    builder.add(
        "cae.mesh.extent",
        "计算域尺寸",
        "data",
        "geometry",
        "/content/mesh/extent",
        subcategory="dimensions",
        origin="computed",
    )
    builder.add(
        "cae.materials",
        "材料",
        "data",
        "material",
        "/content/setup/materials",
        origin="declared",
    )
    for key in _SETUP_BOUNDARY_KEYS:
        label = {
            "boundary_conditions": "边界条件",
            "constraints": "约束",
            "loads": "载荷",
            "aero": "气动设置",
            "initial_conditions": "初始条件",
            "surface": "表面条件",
        }[key]
        builder.add(
            f"cae.setup.{key}",
            label,
            "data",
            "boundary_condition",
            f"/content/setup/{key}",
            origin="declared",
        )
    builder.add(
        "cae.setup.cell_zones",
        "计算域分区",
        "data",
        "mesh",
        "/content/setup/cell_zones",
        origin="declared",
    )
    builder.add(
        "cae.setup.units",
        "设置单位",
        "metadata",
        "technical",
        "/content/setup/units",
        origin="declared",
    )
    builder.add(
        "cae.field_catalog",
        "结果场目录",
        "data",
        "result",
        "/content/field_catalog",
        origin="extracted",
    )
    builder.add(
        "cae.results",
        "结果场统计",
        "data",
        "result",
        "/content/results",
        origin="extracted",
    )
    builder.add(
        "cae.zone_results",
        "分区结果",
        "data",
        "result",
        "/content/zone_results",
        origin="extracted",
    )
    builder.add(
        "cae.derived",
        "派生工程指标",
        "data",
        "derived_metric",
        "/content/derived",
        origin="derived",
    )
    builder.add(
        "cae.rendered_views",
        "仿真渲染资源",
        "data",
        "asset",
        "/content/rendered_views",
        origin="computed",
    )
    del analysis, setup


def classify_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic classification catalog for a CAD or CAE document."""
    builder = _FacetBuilder(doc)
    _add_shared(doc, builder)
    if doc.get("data_type") == "cad":
        _classify_cad(doc, builder)
    elif doc.get("data_type") == "cae":
        _classify_cae(doc, builder)
    _add_visual(builder, doc.get("content") or {})

    counts = Counter((item["layer"], item["category"]) for item in builder.facets)
    summary = [
        {
            "layer": layer,
            "category": category,
            "label_zh": CATEGORY_LABELS[category],
            "field_count": count,
        }
        for (layer, category), count in sorted(
            counts.items(),
            key=lambda item: (
                DATA_CATEGORIES.index(item[0][1])
                if item[0][0] == "data"
                else len(DATA_CATEGORIES) + METADATA_CATEGORIES.index(item[0][1])
            ),
        )
    ]
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "facets": builder.facets,
        "summary": summary,
    }


def ensure_classification(doc: dict[str, Any]) -> dict[str, Any]:
    """Attach classification to a document in-place when it is not persisted."""
    meta = doc.setdefault("meta", {})
    if not meta.get("classification"):
        meta["classification"] = classify_document(doc)
    return meta["classification"]


def category_options() -> Iterable[dict[str, str]]:
    for category in DATA_CATEGORIES:
        yield {
            "key": category,
            "label_zh": CATEGORY_LABELS[category],
            "layer": "data",
        }
    for category in METADATA_CATEGORIES:
        yield {
            "key": category,
            "label_zh": CATEGORY_LABELS[category],
            "layer": "metadata",
        }

"""CAE（ANSYS Fluent CFF）解析管道（原型）。

管线：
  1. 数据集清单与按需访问（zip 内直接随机读 HDF5，不做全量解压）
  2. 网格解析：节点/面/单元拓扑、zone 表、包围盒、单元类型、体积/面积质量指标
  3. 设置解析：求解器版本、模型（VOF/湍流/能量/重力/表面张力）、材料、边界条件、时间步
  4. 结果场提取：压力/速度/相体积分数/密度/粘度等，全局与分区统计
  5. 派生量：速度幅值、相体积、液滴形态（直径/高度/等效球半径）、壁面润湿面积
  6. 可视化：壁面几何、过轴切片云图、场分布直方图、液滴铺展剖面
  7. 多模态描述（VLM 可插拔，规则化兜底）
  8. 统一 JSON Schema 校验 + 溯源；CLI / 批量 / HTTP API / 评测
"""

from .cff import FluentCase, FluentData, Zone, zone_label
from .cgns import build_cgns_json, CgnsFile, CgnsUnsupported
from .dataset import CaseEntry, CaeDataset
from .evaluate import run_evaluation
from .fields import build_field_catalog, derived_fields, summarize_field
from .mesh import build_mesh_summary, compute_cell_geometry
from .multimodal import describe, make_captioner
from .pipeline import build_cae_json, parse_cae_case, slugify
from .nastran import build_bdf_json, parse_bdf
from .op2 import build_op2_json, Op2Unavailable
from .exchange import bdf_to_cgns, cgns_to_bdf
from .parity import compare_cae_documents
from .render import render_all
from .schema import dump_cae, validate_cae
from .setup import extract_setup

__all__ = [
    "CaeDataset",
    "CaseEntry",
    "FluentCase",
    "FluentData",
    "Zone",
    "zone_label",
    "CgnsFile",
    "CgnsUnsupported",
    "build_cgns_json",
    "parse_bdf",
    "build_bdf_json",
    "Op2Unavailable",
    "build_op2_json",
    "bdf_to_cgns",
    "cgns_to_bdf",
    "compare_cae_documents",
    "build_mesh_summary",
    "compute_cell_geometry",
    "extract_setup",
    "build_field_catalog",
    "summarize_field",
    "derived_fields",
    "render_all",
    "describe",
    "make_captioner",
    "validate_cae",
    "dump_cae",
    "build_cae_json",
    "parse_cae_case",
    "slugify",
    "run_evaluation",
]

"""CAD STEP/IGES -> JSON 解析管道。

管线：
  1. STEP / IGES 头部元数据（文本解析）
  2. BRep 几何-拓扑统计 + 质量属性 + 包围盒（OCP / cadquery-ocp）
  3. 基础几何元素分类（面类型）
  4. 特征语义识别（圆柱/孔/凸台/阵列/圆角/倒角，带置信度）
  5. 装配结构与位姿解析
  6. 多视角渲染（VTK 离屏，失败回退 matplotlib）
  7. 多模态描述（VLM 可插拔，规则化兜底）
  8. 统一 JSON Schema 校验 + 溯源存证；CLI / 批量 / HTTP API / 评测
"""

from .pipeline import parse_cad_file, parse_cad_step, build_cad_json
from .geometry import read_iges, read_shape, read_step
from .convert import step_dir_to_iges, step_to_iges
from .iges_meta import parse_iges_header
from .schema import validate_cad, dump_cad
from .features import recognize_features
from .assembly import extract_assembly
from .multimodal import describe, make_captioner
from .api import create_app

__all__ = [
    "parse_cad_step",
    "parse_cad_file",
    "build_cad_json",
    "read_step",
    "read_iges",
    "read_shape",
    "step_to_iges",
    "step_dir_to_iges",
    "parse_iges_header",
    "validate_cad",
    "dump_cad",
    "recognize_features",
    "extract_assembly",
    "describe",
    "make_captioner",
    "create_app",
]

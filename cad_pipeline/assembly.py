"""装配结构解析：复合形（Compound）枚举子项并计算位姿矩阵。"""

from __future__ import annotations

from typing import Any

from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopoDS import TopoDS_Shape, TopoDS_Iterator


def _placement(shape: TopoDS_Shape) -> list[float]:
    """4x4 位姿矩阵（行主序 16 个浮点）。"""
    loc = shape.Location()
    if loc.IsIdentity():
        return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    trsf = loc.Transformation()
    m = [trsf.Value(i, j) for i in range(1, 5) for j in range(1, 5)]
    return [float(x) for x in m]


def _count_solids(shape: TopoDS_Shape) -> int:
    n = 0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        n += 1
        exp.Next()
    return n


def extract_assembly(shape: TopoDS_Shape, part_id: str = "P1") -> dict[str, Any]:
    """返回装配结构 {"instances": [...]}。"""
    children: list[TopoDS_Shape] = []
    # 复合形才可能有子实例
    if shape.ShapeType() == TopAbs_COMPOUND:
        it = TopoDS_Iterator(shape)
        while it.More():
            child = it.Value()
            if child.ShapeType() == TopAbs_SOLID or child.ShapeType() == TopAbs_COMPOUND:
                children.append(child)
            it.Next()

    if not children:
        # 单一实体/无子复合 -> 单实例
        return {
            "instances": [
                {"id": "I1", "name": part_id, "ref": part_id,
                 "placement": _placement(shape), "children": []}
            ]
        }

    instances = []
    for i, child in enumerate(children):
        ref = f"{part_id}_SUB{i}"
        instances.append(
            {
                "id": f"I{i + 1}",
                "name": ref,
                "ref": ref,
                "placement": _placement(child),
                "children": [],
            }
        )
    return {"instances": instances}

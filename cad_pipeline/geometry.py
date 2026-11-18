"""基于 OCP（cadquery-ocp）的 BRep 几何 / 拓扑提取。"""

from __future__ import annotations

from typing import Any

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.gp import gp_Lin
from OCP.IGESControl import IGESControl_Reader
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopoDS import TopoDS_Shape
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shell


_SURF_TYPE_NAMES = {
    GeomAbs_SurfaceType.GeomAbs_Plane: "PLANE",
    GeomAbs_SurfaceType.GeomAbs_Cylinder: "CYLINDER",
    GeomAbs_SurfaceType.GeomAbs_Cone: "CONE",
    GeomAbs_SurfaceType.GeomAbs_Sphere: "SPHERE",
    GeomAbs_SurfaceType.GeomAbs_Torus: "TORUS",
    GeomAbs_SurfaceType.GeomAbs_BezierSurface: "BEZIER",
    GeomAbs_SurfaceType.GeomAbs_BSplineSurface: "BSPLINE",
    GeomAbs_SurfaceType.GeomAbs_SurfaceOfRevolution: "REVOLUTION",
    GeomAbs_SurfaceType.GeomAbs_SurfaceOfExtrusion: "EXTRUSION",
    GeomAbs_SurfaceType.GeomAbs_OffsetSurface: "OFFSET",
    GeomAbs_SurfaceType.GeomAbs_OtherSurface: "OTHER",
}


def read_step(path: str) -> TopoDS_Shape:
    """读取 STEP 文件并返回根形状；失败抛异常。"""
    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != 1:
        raise ValueError(f"STEPControl_Reader.ReadFile 失败，状态码={status}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError("STEP 中未读到有效形状（shape is null）")
    return shape


def read_iges(path: str) -> TopoDS_Shape:
    """读取 IGES 文件并返回根形状；失败抛异常。"""
    reader = IGESControl_Reader()
    status = reader.ReadFile(path)
    if status != 1:
        raise ValueError(f"IGESControl_Reader.ReadFile 失败，状态码={status}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError("IGES 中未读到有效形状（shape is null）")
    return _rebuild_solids(shape)


def _rebuild_solids(shape: TopoDS_Shape) -> TopoDS_Shape:
    """IGES 读入的通常是面片集合（无 shell/solid），缝合后重建实体。

    不重建会让下游 `is_closed`（按 shell 数判断）误判为 False，
    同时装配/干涉等依赖实体的环节无法复用。
    """
    try:
        if _count(shape, TopAbs_SOLID) > 0:
            return shape
        box = _bbox(shape)
        diag = ((box["xmax"] - box["xmin"]) ** 2 + (box["ymax"] - box["ymin"]) ** 2
                + (box["zmax"] - box["zmin"]) ** 2) ** 0.5
        # IGES 无拓扑，缝合容差需要逐级放宽；取首个"无自由边"的结果
        sewed = None
        best_free = None
        for factor in (1e-7, 1e-6, 1e-5, 1e-4):
            sewing = BRepBuilderAPI_Sewing(max(diag * factor, 1e-9))
            sewing.Add(shape)
            sewing.Perform()
            candidate = sewing.SewedShape()
            if candidate.IsNull():
                continue
            free = sewing.NbFreeEdges()
            if sewed is None or (best_free is not None and free < best_free):
                sewed, best_free = candidate, free
            if free == 0:
                break
        if sewed is None:
            return shape

        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        n_solid = 0

        shells: list[TopoDS_Shape] = []
        if sewed.ShapeType() == TopAbs_SHELL:
            shells.append(sewed)  # TopExp_Explorer 不包含根节点自身
        else:
            exp = TopExp_Explorer(sewed, TopAbs_SHELL)
            while exp.More():
                shells.append(exp.Current())
                exp.Next()

        for raw_shell in shells:
            shell = TopoDS.Shell(raw_shell)
            try:
                if BRep_Tool.IsClosed_s(shell):
                    builder.Add(compound, BRepBuilderAPI_MakeSolid(shell).Solid())
                    n_solid += 1
                else:
                    builder.Add(compound, shell)
            except Exception:
                builder.Add(compound, shell)
        return compound if n_solid else sewed
    except Exception:
        return shape


def read_shape(path: str) -> TopoDS_Shape:
    """按扩展名分派：STEP / IGES。"""
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    if ext in ("stp", "step"):
        return read_step(path)
    if ext in ("igs", "iges"):
        return read_iges(path)
    raise ValueError(f"不支持的文件格式：.{ext}（当前支持 .step/.stp/.igs/.iges）")


def _count(shape: TopoDS_Shape, kind: int) -> int:
    n = 0
    exp = TopExp_Explorer(shape, kind)
    while exp.More():
        n += 1
        exp.Next()
    return n


def _bbox(shape: TopoDS_Shape) -> dict[str, float]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    if box.IsVoid():
        return {"xmin": 0.0, "ymin": 0.0, "zmin": 0.0, "xmax": 0.0, "ymax": 0.0, "zmax": 0.0}
    minp = box.CornerMin()
    maxp = box.CornerMax()
    return {
        "xmin": minp.X(), "ymin": minp.Y(), "zmin": minp.Z(),
        "xmax": maxp.X(), "ymax": maxp.Y(), "zmax": maxp.Z(),
    }


def _mass_props(shape: TopoDS_Shape) -> dict[str, float]:
    volume = surface = 0.0
    solids = _count(shape, TopAbs_SOLID)
    if solids:
        # 装配体按实体求和：直接对复合体求有符号体积会因内外朝向相反而抵消
        total = 0.0
        exp = TopExp_Explorer(shape, TopAbs_SOLID)
        while exp.More():
            try:
                vp = GProp_GProps()
                BRepGProp.VolumeProperties_s(exp.Current(), vp)
                total += abs(vp.Mass())
            except Exception:
                pass
            exp.Next()
        volume = total
    else:
        try:
            vp = GProp_GProps()
            BRepGProp.VolumeProperties_s(shape, vp)
            volume = vp.Mass()
        except Exception:
            volume = 0.0
    try:
        sp = GProp_GProps()
        BRepGProp.SurfaceProperties_s(shape, sp)
        surface = sp.Mass()
    except Exception:
        surface = 0.0
    return {"volume": volume, "surface_area": surface}


def _face_surface_type(face: Any) -> str:
    try:
        adaptor = BRepAdaptor_Surface(face)
        return _refine_surface_type(adaptor)
    except Exception:
        return "OTHER"


def _refine_surface_type(adaptor: Any) -> str:
    """识别面类型；对旋转面按母线还原圆柱/圆锥/球/圆环。

    IGES 往返会把解析曲面退化成旋转面（实测 CYLINDER/CONE/SPHERE ->
    REVOLUTION），若不还原，基于面类型的特征识别会整体失效。
    """
    stype = adaptor.GetType()
    name = _SURF_TYPE_NAMES.get(stype, "OTHER")
    if stype != GeomAbs_SurfaceType.GeomAbs_SurfaceOfRevolution:
        return name
    try:
        basis = adaptor.BasisCurve()
        btype = basis.GetType()
        axe = adaptor.AxeOfRevolution()
        if btype == GeomAbs_CurveType.GeomAbs_Line:
            line_dir = basis.Line().Direction()
            axis_dir = axe.Direction()
            dot = abs(line_dir.X() * axis_dir.X() + line_dir.Y() * axis_dir.Y()
                      + line_dir.Z() * axis_dir.Z())
            return "CYLINDER" if dot > 0.999 else "CONE"
        if btype == GeomAbs_CurveType.GeomAbs_Circle:
            center = basis.Circle().Location()
            distance = gp_Lin(axe).Distance(center)
            return "SPHERE" if distance < 1e-9 else "TORUS"
    except Exception:
        pass
    return name


def _cylindrical_params(face: Any) -> dict[str, float] | None:
    """圆柱面参数：半径、轴向、轴位置与轴向深度（用于孔/凸台/阵列推断）。

    除原生 CYLINDER 外，也接受 IGES 往返后退化成的「母线为平行直线的旋转面」。
    """
    try:
        adaptor = BRepAdaptor_Surface(face)
        stype = adaptor.GetType()
        if stype == GeomAbs_SurfaceType.GeomAbs_Cylinder:
            cyl = adaptor.Cylinder()
            radius = cyl.Radius()
            axis = cyl.Axis()
        elif stype == GeomAbs_SurfaceType.GeomAbs_SurfaceOfRevolution:
            basis = adaptor.BasisCurve()
            if basis.GetType() != GeomAbs_CurveType.GeomAbs_Line:
                return None
            axis = adaptor.AxeOfRevolution()
            line = basis.Line()
            axis_dir = axis.Direction()
            line_dir = line.Direction()
            dot = abs(line_dir.X() * axis_dir.X() + line_dir.Y() * axis_dir.Y()
                      + line_dir.Z() * axis_dir.Z())
            if dot <= 0.999:  # 圆锥母线，不按圆柱处理
                return None
            radius = gp_Lin(axis).Distance(line.Location())
        else:
            return None
        loc = axis.Location()
        d = axis.Direction()
        projs: list[float] = []
        vexp = TopExp_Explorer(face, TopAbs_VERTEX)
        while vexp.More():
            v = TopoDS.Vertex(vexp.Current())
            pnt = BRep_Tool.Pnt_s(v)
            projs.append((pnt.X() - loc.X()) * d.X()
                         + (pnt.Y() - loc.Y()) * d.Y()
                         + (pnt.Z() - loc.Z()) * d.Z())
            vexp.Next()
        depth = (max(projs) - min(projs)) if projs else 0.0
        return {
            "radius": radius,
            "axis_x": axis.Direction().X(),
            "axis_y": axis.Direction().Y(),
            "axis_z": axis.Direction().Z(),
            "loc_x": loc.X(),
            "loc_y": loc.Y(),
            "loc_z": loc.Z(),
            "depth": depth,
        }
    except Exception:
        return None


def extract_geometry(shape: TopoDS_Shape) -> dict[str, Any]:
    """提取拓扑统计、质量属性、包围盒、面类型分布与圆柱面列表。"""
    geo: dict[str, Any] = {}
    geo["topology"] = {
        "solids": _count(shape, TopAbs_SOLID),
        "shells": _count(shape, TopAbs_SHELL),
        "faces": _count(shape, TopAbs_FACE),
        "edges": _count(shape, TopAbs_EDGE),
        "vertices": _count(shape, TopAbs_VERTEX),
    }
    geo["bbox"] = _bbox(shape)
    geo["mass_props"] = _mass_props(shape)

    face_types: dict[str, int] = {}
    cylinders: list[dict[str, Any]] = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        tname = _face_surface_type(face)
        face_types[tname] = face_types.get(tname, 0) + 1
        cp = _cylindrical_params(face)
        if cp is not None:
            cylinders.append(cp)
        exp.Next()

    geo["face_types"] = [{"type": k, "count": v} for k, v in sorted(face_types.items(), key=lambda kv: -kv[1])]
    geo["cylinders"] = cylinders
    return geo

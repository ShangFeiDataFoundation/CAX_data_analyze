# CAX 数据解析算法原型方案

> 目标：基于 `CAX数据解析.md` 描述的业务流程，规划一个可运行、可验收的
> **CAX 数据解析算法原型**（CAD 与 CAE 双通道），把原始文件解析成结构化、语义化、
> 可追溯的 JSON 数据资源，为课题"商用飞机全生命周期数据资源统一建模与采集算法"提供算法底座。

---

## 0. 需求理解

### 0.1 输入

| 类别 | 原始格式 | 来源 | 解析示例 |
| --- | --- | --- | --- |
| CAD | STEP（CATIA 扩展，AP203/AP214） | ABC Dataset 类零件 | `image.png` 中 10 个零件 |
| CAE | Ansys 结果文件（RST/`.h5`，含 Mechanical 结构 + Fluent 流体） | Ansys 生态 | `image 1.png` 结构位移云图 |

### 0.2 解析库 / 底层能力

| 通道 | 核心库 | 作用 |
| --- | --- | --- |
| CAD | `pythonOCC`（OCP） | STEP 几何/拓扑/元数据精确解析，多视角离线渲染 |
| CAE | `ansys-dpf-core`（PyDPF） | 网格/材料/边界条件/结果场提取；`ansys-fluent-core`（PyFluent）用于 Fluent 对象 |

### 0.3 目标产物

把"原始文件 + 渲染图 + 多模态文本描述"统一整理，输出**标准化 JSON**，供后续入库、关联建模与下游标注使用。

### 0.4 需求澄清（需与项目对齐的歧义点）

- `md` 开头写"CAE（Fluent）+ PyFluent"，但流程与示例图实为 **Ansys Mechanical（结构）** 结果。
  本方案采用统一处理：**PyDPF 同时覆盖结构（RST）与流体（Fluent `.h5`）结果**，PyFluent 仅用于驱动/读取 Fluent 对象。
- "前沿技术解析能到什么程度"——本方案在第 4 节给出明确能力边界表。

---

## 1. 总体架构

```
┌─────────────── 输入层 ───────────────┐
│  CAD STEP 文件            CAE 结果文件 │
└──────────────┬─────────────┬─────────┘
               │             │
      ┌────────▼────┐  ┌─────▼────────┐
      │ CAD 适配器   │  │ CAE 适配器    │
      │ (pythonOCC) │  │  (PyDPF)     │
      └────────┬────┘  └─────┬────────┘
               │             │
      ┌────────▼─────────────▼────────┐
      │        解析核心层（可插拔）       │
      │  几何/拓扑 → 特征 → 语义        │
      │  网格/设定 → 结果场 → 统计       │
      └────────┬─────────────┬─────────┘
               │             │
      ┌────────▼────┐  ┌─────▼────────┐
      │ 渲染/可视化 │  │ 多模态描述     │
      │ OCCT 渲染  │  │  VLM 图文     │
      │ Matplotlib │  │  Qwen-VL等   │
      └────────┬────┘  └─────┬────────┘
               │             │
      ┌────────▼─────────────▼────────┐
      │      统一序列化 + JSON Schema  │
      │      （溯源、去重、版本、单位）   │
      └───────────────────────────────┘
```

设计原则：

- **双通道解耦**：CAD/CAE 独立适配，互不依赖，便于分别演进。
- **可插拔解析器**：通过配置/注册表选择解析器与字段粒度。
- **图文双模态**：结构化字段（精确）+ 图像文本描述（语义互补）。
- **可追溯**：JSON 内保留 `source`（文件哈希、解析器、时间、版本）。
- **健壮性**：单文件解析失败不中断批量，错误进 `warnings`。

---

## 2. CAD 解析管线

### 2.1 步骤

1. **文件受理与校验** — 读入 STEP，检查合法性、单位、AP 版本。
2. **元数据提取** — 产品名称/作者/单位（mm）/标准/schema/修订。
3. **BRep 几何-拓扑提取** — Solid/Shell/Face/Edge/Vertex 数量、闭合性、
   包围盒、体积、面积、质量属性（`GProp`）。
4. **基础几何元素分类** — 平面/圆柱/圆锥/球/圆环/B 样条曲面，统计各类型面数与参数。
5. **高级几何 / 特征识别** — 孔、槽、凸台、倒角、圆角、肋板、阵列等
   （几何级特征可靠；语义级设计意图为研究难点）。
6. **装配结构解析** — 产品/实例/组件层级与 4×4 位姿矩阵（Part / Instance）。
7. **多视角离线渲染** — 正交（前/上/侧）+ 等轴测 + 高亮边 + 剖面，生成 PNG。
8. **多模态描述** — 多视角图送 VLM → 零件类型、功能推测、复杂度描述。
9. **JSON 序列化**。

### 2.2 关键实现要点（pythonOCC）

- `STEPControl_Reader` 读取；`TopExp_Explorer` 遍历拓扑。
- `BRepGProp` 计算体积/面积/质心/惯性矩。
- `Bnd_Box` + `BRepBndLib` 计算包围盒。
- 曲面类型判别：`GeomAbs_SurfaceType`。
- 特征识别：形态学/拓扑规则（如圆柱面相邻孔）+ 可选有监督特征分类器。
- 渲染：OCCT `V3d_View` / `AIS_InteractiveContext` 离屏渲染，多相机位姿。

### 2.3 CAD 输出断面（JSON 片段）

```json
{
  "schema_version": "1.0",
  "data_type": "cad",
  "source": {
    "filename": "00217697.stp",
    "format": "STEP(AP214)",
    "bytes": 123456,
    "file_hash": "sha256:...",
    "parser": "pythonOCC-7.7 / OCP",
    "parsed_at": "2026-09-08T12:00:00+08:00"
  },
  "meta": {
    "product": { "name": "00217697", "author": "ABC", "units": "mm", "standard": "ISO 10303-21" },
    "validation": { "is_valid": true, "warnings": [] }
  },
  "content": {
    "assembly": {
      "instances": [
        { "id": "I1", "name": "BODY", "ref": "P1", "placement": [16 个浮点], "children": [] }
      ]
    },
    "parts": [
      {
        "id": "P1",
        "name": "BODY",
        "bbox": { "xmin": 0, "ymin": 0, "zmin": 0, "xmax": 100, "ymax": 60, "zmax": 20 },
        "volume": 12000.5, "surface_area": 4200.2, "is_closed": true,
        "geometry_stats": { "solids": 1, "shells": 1, "faces": 42, "edges": 96, "vertices": 80 },
        "face_types": [ { "type": "CYLINDER", "count": 4 }, { "type": "PLANE", "count": 38 } ],
        "features": [
          { "type": "hole", "confidence": 0.93, "face_ids": [10, 11],
            "params": { "diameter": 12.0, "axis": [0, 0, 1], "depth": 15.0 } }
        ]
      }
    ],
    "rendered_views": [
      { "view": "iso", "file": "renders/00217697_iso.png" },
      { "view": "top", "file": "renders/00217697_top.png" }
    ],
    "multimodal": {
      "caption": "带四个安装孔的矩形支架类零件，厚度约 20mm，结构较简单。",
      "model": "Qwen2.5-VL-32B",
      "part_type_hypothesis": "bracket",
      "complexity": "medium"
    }
  }
}
```

---

## 3. CAE 解析管线

### 3.1 步骤

1. **文件受理** — Ansys RST（结构）/ Fluent `.h5` / `.cas+.dat`（流体）。
2. **网格提取** — 节点数、单元数、单元类型分布、质量指标（偏斜/雅可比）。
3. **初始设定提取** — 材料属性（E、ν、ρ）、边界条件、载荷。
4. **结果场提取** —
   - 结构：位移、反力、应力（von Mises/主应力）、应变、能量。
   - 流体：速度、压力、温度 + 梯度；比热容、孔隙率、湍流参数等高阶量。
5. **统计与区域分析** — 全局/命名区域 min/max/均值、时间步列表。
6. **可视化** — Matplotlib/PyVista 云图、矢量图、流线图、剖面。
7. **多模态描述**（可选增强）— 云图 → VLM 文本解释。
8. **JSON 序列化**。

### 3.2 关键实现要点（PyDPF）

- `dpf.core` 读 RST/`h5`：`Model`, `Results`, `Fields`, `Scoping`, `Operator`.
- `mesh` 算子取拓扑与质量；`material`/`bounding`/`load` 算子取设定。
- `displacement`/`stress`/`reaction_force`/`energy` 等结果算子。
- 流体用 `ansys-fluent-core` 读取 `.cas/.dat` 或 DPF 的 Fluent `.h5` 接口。
- 可视化：`matplotlib` 云图 + `pyvista` 体/面渲染；单位归一化。

### 3.3 CAE 输出断面（JSON 片段）

```json
{
  "schema_version": "1.0",
  "data_type": "cae",
  "source": {
    "filename": "130794_fem1_sim1.rst",
    "format": "Ansys RST (HDF5)",
    "solver": "Ansys Mechanical",
    "parser": "ansys-dpf-core 0.10",
    "parsed_at": "2026-09-08T12:00:00+08:00"
  },
  "content": {
    "analysis": { "physics": "structural", "subcase": "Static Loads 1", "units": "mm/N/MPa" },
    "mesh": {
      "nodes": 189234, "elements": 892145,
      "element_types": { "TETRA4": 700000, "TETRA10": 192145 },
      "quality": { "min_skewness": 0.02, "avg_skewness": 0.31 }
    },
    "setup": {
      "materials": [ { "name": "Al-7075", "E": 71700, "nu": 0.33, "rho": 2.81e-9 } ],
      "boundary_conditions": [ { "type": "fixed_support", "location": "BottomFace" } ],
      "loads": [ { "type": "force", "value": 1500, "unit": "N", "location": "TopFace" } ]
    },
    "results": [
      {
        "field": "displacement", "component": "magnitude",
        "min": 0.0, "max": 0.109, "unit": "mm", "times": [0.0],
        "regions": [ { "name": "FullModel", "min": 0.0, "max": 0.109 } ],
        "visualization": { "type": "contour", "file": "viz/130794_disp.png" }
      }
    ],
    "multimodal": {
      "caption": "结构静力分析，最大位移 0.109 mm，位于模型中部悬伸部位，符合梁式弯曲变形特征。",
      "model": "Qwen2.5-VL-32B"
    }
  }
}
```

---

## 4. 能力边界说明（"前沿技术能做到什么程度"）

> 需求第 4 条：明确当前技术对 CAD / CAE 的解析能力上限。给出**可靠 / 部分 / 难点**三级评估。

| 维度 | 内容 | 能力 | 说明 |
| --- | --- | --- | --- |
| CAD 元数据 | 名称、作者、单位、标准、修订 | 可靠 | STEP AP214 内置 |
| CAD 几何-拓扑 | Solid/Shell/Face/Edge/Vertex、体积/面积/包围盒 | 可靠 | B-Rep 精确 |
| CAD 基础几何元素 | 平面/圆柱/圆锥/球/圆柱/B 样条曲面分类 | 可靠 | 逐面类型判定 |
| CAD 装配结构 | 产品/实例/组件层级、位姿矩阵 | 可靠 | Part/Instance 遍历 |
| CAD 特征识别 | 孔、槽、凸台、倒角、圆角、阵列 | 部分 | 几何级可靠；语义/设计意图级疑难 |
| CAD 设计意图/约束/参数 | GD&T、参数化依赖、约束关系 | 难点 | 需 AP214 语义 + 研究性建模 |
| CAD 多视角渲染 | 正交/等轴测/剖面/高亮边 | 可靠 | OCCT 离屏 |
| CAD 多模态语义描述 | 零件类型/功能/复杂度推测 | 部分 | VLM 定性，存在幻觉风险 |
| CAE 网格 | 节点/单元/类型/质量 | 可靠 | DPF mesh 算子 |
| CAE 初始设定 | 材料/边界/载荷 | 部分 | 取决于结果文件是否留存；完整模型树需 ACT/APDL |
| CAE 结果场 | 位移/反力/应力/能量（结构），速度/压力/温度/梯度/湍流（流体） | 可靠 | DPF 结果算子 |
| CAE 可视化 | 云图/矢量/流线/剖面 | 可靠 | Matplotlib/PyVista |
| 跨格式/原生格式 | CATIA `.CATPart`、Fluent 建模树 | 难点 | 需导出 STEP 或原生接口；PyOCC 只读 STEP |

**结论**：几何/拓扑/结果场级解析可靠；**特征语义与设计意图、跨工具链语义对齐**是研究前沿与差异化点，
也是原型需重点投入并体现创新性的部分。

---

## 5. 技术栈与依赖

- Python 3.10+（当前环境 3.13）
- CAD：`ocp`（pythonOCC-core）/ `cadquery-ocp`；可选 `numpy`、`scipy`（几何后处理）
- CAE：`ansys-dpf-core`、`ansys-fluent-core`
- 可视化：`matplotlib`、`pyvista`
- 数据：`numpy`、`pandas`（当前已有 numpy 2.3 / pandas 3.0）
- 多模态：Qwen-VL / InternVL / GPT-4o·Claude（通过 API 或本地推理）
- 工程化：`pydantic`（Schema 校验）、`fastapi`（可选服务化）、`uv`/`poetry`（依赖）、`pytest`（测试）

> 注：`ocp` / `ansys-dpf-core` / `pyvista` 当前环境未安装，需联网 `pip install`（受网络限制，见风险）。

---

## 6. 统一 JSON Schema（数据模型）

顶层信封统一：

```json
{
  "schema_version": "1.0",
  "data_type": "cad|cae",
  "source": { "filename", "format", "bytes", "file_hash", "parser", "parsed_at" },
  "meta": { "validation", "tags", "domain", "provenance" },
  "content": "<按类型区分的正文>",
  "assets": [ "图片/渲染/可视化文件相对路径" ]
}
```

`content` 为判别联合（discriminated union）：`cad` 走 CAD 结构，`cae` 走 CAE 结构。
用 `pydantic` 定义 `CadContent` / `CaeContent` 并做严格校验。

---

## 7. 实施阶段与里程碑

> 原型聚焦"能跑、能看、能验收"，里程碑供课题算法交付（2027.12）与研究报告（2026 三稿）复用。

| 阶段 | 内容 | 可验收产物 | 参考周期 |
| --- | --- | --- | --- |
| **P0 环境与样例** | 安装依赖、准备 ABC STEP 零件 + Ansys 结果样例 | 样例目录 + 环境配置脚本 | 第 1-2 周 |
| **P1 CAD MVP** | STEP→拓扑/几何/元数据→JSON + 多视角渲染 | 单零件 JSON + 渲染 PNG | 第 3-5 周 |
| **P2 CAD 语义增强** | 特征识别 + VLM 多模态描述 + 装配结构 | 含特征/描述的 JSON | 第 6-9 周 |
| **P3 CAE 管线** | RST/`h5`→网格/设定/结果场→JSON + 云图 | 单工况 JSON + 云图 PNG | 第 8-12 周 |
| **P4 统一与批处理** | 统一 Schema、溯源、批处理 runner、CLI + 最小 API | 批量 JSON + 调用示例 | 第 12-15 周 |
| **P5 评测与文档** | 精度/覆盖率评测，跨工具对齐，报告素材 | 评测报告 + 原型文档 | 第 15-18 周 |

---

## 8. 演示与验收设计

- **CAD 演示**：取 `image.png` 中 1-2 个零件（如 `00217697`），输出 JSON + 等轴测/三视图渲染。
- **CAE 演示**：取 `image 1.png` 对应 RST，输出位移云图 + 位移/应力极值 JSON。
- **验收口径**：
  1. 能对样例批量解析且不因单文件失败中断；
  2. 几何统计与 STEP 精度一致（可复算体积/面积）；
  3. JSON 通过 Schema 校验、字段可溯源；
  4. 特征识别给出置信度；多模态描述可解释；
  5. 提供 CLI 与文档化调用方式。

---

## 9. 风险与挑战

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 离线环境缺 `ocp`/`ansys-dpf-core` 依赖 | 无法本地跑通 | 申请并网/临时装环境；或用 OCP 预编译 wheel |
| 特征/语义识别精度 | CAD 语义层产出打折 | 混合规则+ML，输出置信度，标注不确定性 |
| CATIA 原生格式/跨工具链 | 只读 STEP，跨平台语义缺统一 | 明确 STEP 为中间交换；后续做跨工具映射 |
| Ansys 授权与文件访问 | 结果文件受限 | 用脱敏/样例结果；标明需授权 |
| VLM 描述幻觉 | 描述不可靠 | 图文互印证；描述仅作辅助字段 |
| 大文件/内存 | 大批量慢 | 流式/离线渲染；批处理 + 结果缓存 |
| 单位与标准未对齐 | 数值语义错误 | 统一单位表，JSON 记录 unit 元数据 |

---

## 10. 与课题整体关系

本原型是课题"商用飞机全生命周期数据资源统一建模与采集算法（CAX 子算法）"的**首个可运行载体**：

- 落地 CAX 解析机理与方法的**可比对、可复用实现**；
- 为"几何-拓扑-特征-装配"分层解析提供代码基础；
- 为研究报告提供实验数据与案例；
- 沉淀**统一数据表达框架**，供结构化与时序、文档与文本子算法复用顶层信封与溯源机制。

---

## 11. 【实施状态】CAE 通道已落地（2026-09）

> 本节记录按第 3 节方案实现的可运行产物与实测结果，实现代码见 `cae_pipeline/`，
> 使用说明见 `cae_pipeline/README.md`，评测结果见 `json_out/cae_evaluation_report.md`。

### 11.1 数据集实际情况

`CAE_dataset/Dataset_Numerical_study_on_microdroplets_interaction.zip`
（Brno University of Technology，液滴撞击粗糙表面 CFD 研究）：

| 项 | 值 |
| --- | --- |
| 求解器 | ANSYS Fluent 2025R1（导出为 CFF：`.cas.h5` + `.dat.h5`） |
| 规模 | zip 1.9 GB，解压后 2.12 GB，43 个条目，**18 个算例**（Dry_surface 12 / Wet_surface 6）+ 4 段撞击角度视频 |
| 单算例 | 91–93 万节点、130–152 万单元、343–385 万面；data 文件 4–223 MB |
| 物理 | VOF 两相（vzduch=air / voda=water-liquid）、k-ω SST、重力、表面张力 0.075 N/m；瞬态 dt=4 ns，共 500 步 |
| 几何 | 计算域 60×60×25 µm；壁面由多尺度粗糙度构成（面心高度带 0.8 µm 内分层） |

原型方案 §3 假设的是「RST（结构）+ PyDPF」，实际数据为**流体 VOF 结果**，
且环境无 Ansys 安装/授权，因此实施时做了如下技术路线调整（不影响 §3 的数据模型与信封）。

### 11.2 与原方案步骤的对应

| 方案步骤（§3.1） | 实现情况 | 模块 |
| --- | --- | --- |
| 1 文件受理 | 识别 `.cas.h5`/`.dat.h5`；**zip 内直接随机读，不做全量解压** | `dataset.py` / `h5stream.py` |
| 2 网格提取 | 节点/面/单元计数、单元类型分布（tet/hex/poly）、zone 表、包围盒、**单元体积与面面积质量指标**（散度定理，分块计算） | `mesh.py` |
| 3 初始设定提取 | 求解器版本、湍流/能量/多相/重力/表面张力、相与材料、边界条件（逐 zone 逐相：速度/温度/粗糙度/材料）、时间步、单位、初始液滴与液膜区域 | `setup.py` / `scheme.py` |
| 4 结果场提取 | 压力、速度三分量、VOF、密度、粘度、体积力、面质量/体积流量、壁面剪应力等（本数据集 42 个场条目/算例），全局统计 + 关键场分区统计 | `fields.py` |
| 5 统计与区域分析 | min/max/mean/std/p1..p99、覆盖度、DBL_MAX 未定义值计数；逐 zone 面场统计 | `fields.py` |
| 6 可视化 | 壁面几何（含粗糙度放大）、过轴切片云图（VOF/|U|/p）、场分布直方图、液滴铺展剖面 | `render.py` |
| 7 多模态描述 | 规则化摘要（默认）/ VLM 可插拔（云图 + 解析结果） | `multimodal.py` |
| 8 JSON 序列化 | 与 CAD 通道共用信封 + pydantic 校验 + 溯源 | `schema.py` / `pipeline.py` |

**新增的 CFD 语义派生量**（超出原方案，训练/检索价值更高）：

- 液滴形态：水相 `VOF>0.5` 单元 → 体积、最大直径、高度、等效球半径；
- **铺展系数 D/D0**：以 case 内 `kvapka_register` 球形初始液滴（r=5 µm）为基准；
- 湿表面算例用 `hladina_reg` 六面体 register 的顶面高度分离液膜与液滴；
- 壁面润湿面积（壁面 zone 中相邻单元水相 VOF>0.5 的面面积之和）；
- 相体积 `Σ VOF×V_cell`，用于守恒性校验。

### 11.3 实测与校验

- 单算例端到端（网格+设置+42 个场+4 张图+JSON）：**12.7–23.9 s**，磁盘上不产生解压副本；
- 清单（`--list`）：**< 3 s** 读出 18 个算例；
- **18 个算例全部解析成功**（含无结果文件、仅 case 的 `K45_S01`，会给出 warning 而非失败）；
- 物理自洽性检查（6 个算例、21 项）**全部通过**（湿表面算例的液滴半径回归不适用，自动跳过）：
  1. 单元体积之和 / 包围盒体积 = 0.987–0.993（散度定理与法向定向正确）；
  2. 无「非正体积」单元（自动纠正面法向定向后）；
  3. 边界条件面数之和 == 边界面总数（zone 表与拓扑一致）；
  4. 液滴等效球半径与 case 初始液滴半径误差：干表面 0.03%；湿表面 6%（1H）/ 22%（2H，
     液滴已与液膜融合，符合预期）。
- 典型结果：`Dry_surface/K45_S005`（撞击后）铺展半径 8.5 µm（D/D0≈1.7）、
  最大速度 22 m/s、压力峰值 4×10⁵ Pa；`K86_S01`（初始态）液滴等效半径 5.0024 µm（理论 5 µm）。

### 11.4 运行方式

```bash
python -m cae_pipeline.cli --list                      # 数据集清单（不解压）
python -m cae_pipeline.cli Dry_surface/K86_S01 -o json_out
python -m cae_pipeline.cli --group Wet_surface -o json_out
python -m cae_pipeline.cli --evaluate --limit 6 --no-hash
python -m cae_pipeline.cli --serve                     # HTTP API
```

### 11.5 尚未覆盖（后续）

- 结构仿真（Mechanical RST）通道：需另写 RST 读取器（PyDPF 或自实现），信封与流程可复用；
- 界面重构（PLIC）级别的精确界面面积/接触角、时间序列演化（本数据集每算例仅一个 `.dat.h5` 快照）；
- `dry_surface_impact_angles/*.avi` 视频侧的多模态描述。

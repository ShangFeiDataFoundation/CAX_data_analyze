# CAX 解析管线说明

## 文档说明

本文档面向 CAX 解析管线的使用者、集成方与维护人员，系统说明本仓库所提供两条解析通道的解析能力、实现原理、调用方式及适用范围。

本仓库针对计算机辅助设计（CAD）与计算机辅助工程（CAE）两类原始工程文件，分别建立了相互独立、可单独部署的解析通道。两条通道在输入格式与解析算法上各自独立，但在输出层面统一遵循同一套 JSON 信封结构（envelope schema），使异构的工程数据能够以一致的形态进入后续的存储、检索与建模环节。

本文档的组织结构如下：第 1 节给出整体概览；第 2 节说明当前可达到的解析效果；第 3 节阐述实现方法与关键技术路径；第 4 节说明安装与使用方式；第 5 节界定当前系统的能力边界与后续工作方向。

有关设计背景，请参阅 `CAX数据解析算法原型方案.md`；各通道的内部模块细节，请分别参阅 `cad_pipeline/README.md` 与 `cae_pipeline/README.md`。

## 1. 整体概览

本仓库现包含两条互相独立的解析通道，其共同目标是将原始工程文件转换为**结构统一的 JSON 文档**，并按需附带渲染图片，从而支持数据入库、内容检索以及多模态模型训练等下游应用。两条通道的差异主要体现在输入格式与数据来源，其输出结构保持一致。

| 通道 | 输入 | 数据来源 | 输出 |
| --- | --- | --- | --- |
| CAD | STEP（AP203/AP214）、IGES | ABC Dataset（`CAD_dataset/abc_0000_step_v00.7z`，1.6 GB，含 1 万个零件）；NASA CRM 整机装配 IGES | `<名称>_cad.json` + 四视图 PNG + STL |
| CAE | ANSYS Fluent CFF（`.cas.h5` + `.dat.h5`）、CGNS（HDF5）、Nastran BDF、Nastran OP2 | `CAE_dataset/*.zip`（1.9 GB，解压后 2.12 GB，18 个算例）；`外部样例数据/` | `<分组>__<算例>_cae.json` + 4 张图 PNG |

在输出层面，两条通道均采用同一种信封结构，由 `schema_version`、`data_type`、`source`、`meta` 与 `content` 五个字段构成。其中 `data_type` 取值为 `cad` 或 `cae`，作为判别字段用于区分正文类型；`meta` 记录 Schema 校验结果与处理链溯源信息；`content` 承载按类型区分的正文内容。全部 JSON 文档在产出前均经 pydantic 严格校验，以保证字段完整性与类型一致性。

## 2. 解析能力

### 2.1 CAD 通道

CAD 通道以 STEP 文件为输入，解析结果覆盖产品元数据、几何拓扑、基础几何元素、特征、装配结构、渲染视图与多模态描述等方面。具体能力如下表所示。

| 维度 | 拿到的东西 |
| --- | --- |
| 元数据 | 产品名、作者、单位、STEP schema（如 AP214）、实现级别 |
| IGES 元数据 | 全局节产品名、单位标志（INCH/MM…）、模型比例；解析曲面退化为 REVOLUTION 时自动还原 |
| 几何-拓扑 | 实体/壳/面/边/顶点数、体积、表面积、包围盒、是否闭合 |
| 基础几何元素 | 面类型分布：平面 / 圆柱 / 圆锥 / 球 / 圆环 / B 样条曲面及各自数量 |
| 特征 | 圆柱孔、凸台、阵列、圆角、倒角等，**每项带置信度**与参数（半径、轴向、数量） |
| 装配 | 实例层级树 + 每个实例的 4×4 位姿矩阵 |
| 可视化 | iso / front / top / right 四视图渲染（VTK 优先，失败自动回退 matplotlib） |
| 多模态 | 中文文本描述（默认规则化摘要，可接 VLM） |

在真实数据验证方面，该通道对 5 个真实 ABC 零件取得 **100% 成功率**，平均耗时 58.4 ms/件（不含渲染），累计识别特征 43 个。此外，对合成标注件（100×60×20 长方体，含 r=8 贯通孔）的解析结果中，面数为 7、孔半径为 8.0，与标注值一致，验证了特征识别环节的正确性。

### 2.2 CAE 通道

CAE 通道以 ANSYS Fluent CFF 格式文件为输入，解析结果覆盖网格、网格质量、求解设置、边界条件、初始条件、结果场、CFD 派生量与可视化等方面。具体能力如下表所示。

| 维度 | 拿到的东西 |
| --- | --- |
| 网格 | 节点/面/单元数、边界面数、zone 表（名称/类型/数量/面积）、包围盒与尺度、单元类型分布（tet/hex/poly…） |
| 网格质量 | 单元体积与面面积的 min/max/均值/分位数、体积一致性、非正体积单元数 |
| 求解设置 | 求解器版本、湍流模型（本数据集为 k-ω SST）、能量方程、多相（VOF）与相/材料、重力、表面张力、时间步与总步数、单位 |
| 边界条件 | 逐 zone 逐相：类型（wall / velocity-inlet / interior）、速度、温度、粗糙度、材料、对面单元数 |
| 初始条件 | `cell-registers`：初始液滴球（半径 5 µm）、湿表面算例的预置液膜区域 |
| 结果场 | 每个算例 42 个场条目：压力、速度三分量、VOF、密度、粘度、体积力、面质量/体积流量、壁面剪应力等；全局统计（min/max/mean/std/p1–p99）+ 关键场分区统计 |
| CFD 派生量 | 液滴等效球半径、最大直径、高度、**铺展系数 D/D0**、壁面润湿面积、相体积 |
| 可视化 | 壁面几何（含粗糙度放大）、过轴切片云图（VOF/速度/压力）、场分布直方图、液滴铺展剖面 |

CGNS（HDF5 存储）与 Nastran BDF 在输出层复用同一信封：

| 通道 | 拿到的东西 |
| --- | --- |
| CGNS | Base/Zone 层级、ZoneType、节点数、单元类型与数量、包围盒、FlowSolution 场统计、ZoneBC 清单、GoverningEquations/TurbulenceModel |
| Nastran BDF | SOL 与子工况、GRID 数量与包围盒、单元类型分布、属性/材料/约束/载荷/气动卡片统计、INCLUDE 展开清单 |
| Nastran OP2 | 节点/单元规模、单元类型、属性/材料数量、位移表、特征向量、复特征值，以及应力/应变/单元力/应变能结果表（按单元类型） |

在真实数据验证方面，该通道对全部 **18 个算例均解析成功**，单算例耗时 12.6–23.9 s。物理自洽性检查共 21 项，**全部通过**，具体包括：体积积分占包围盒比例为 95%–99%、无非正体积单元、边界条件面数与拓扑计数一致，以及初始态算例的液滴等效半径与 case 内定义值误差为 0.03%。

### 2.3 JSON 文档结构

两条通道产出的 JSON 文档具有统一的结构层次，其正文内容依据数据类型分支展开。整体结构示意如下。

```
schema_version / data_type
source（primary_format、files[]、解析器、数据集条目、访问方式）
meta.validation / meta.provenance / meta.capabilities（能力声明）
content
  ├ CAD: product / parts[]（几何+特征）/ assembly / rendered_views / visual
  └ CAE: analysis（模型+相+时间+单位）/ mesh / setup（材料+边界+初始条件）
         / field_catalog / results[] / zone_results[] / derived / rendered_views / visual
```

当前统一 Schema 版本为 `2.1`，CAD 与 CAE 各一种正文结构。共享定义位于
`cax_core/schema.py`，生成的 JSON Schema 位于 `schemas/cad.schema.json` 和
`schemas/cae.schema.json`。字段价值、视觉检索与能力边界见
`docs/CAX统一JSON与业务价值.md`。

### 2.4 工作区原型

```powershell
python -m cax_pipeline.cli --serve
```

也可以直接双击根目录的 `启动前端.cmd`，服务启动后会自动打开浏览器。

打开 `http://127.0.0.1:8200` 后可指定本机已解压工作区，扫描全部支持的 CAD/CAE
文件、自动渲染、调用视觉模型、建立 SQLite 索引并检索。使用说明见
`docs/原型使用说明.md`。

扫描结果按文件持久化到工作区 `.cax_reports/records/<record_id>/`。再次扫描时优先读取
已有报告，并检测源文件是否变化；前端支持按文件重新生成，以及导出包含 Markdown、JSON
和渲染图的 ZIP 报告。

为控制文档体积并便于入库，数组类字段一律只存**统计量与索引**，不写入原始场数据。据此，CAD 通道产出的 JSON 体积约为 3–15 KB/件，CAE 通道约为 75 KB/算例。

## 3. 实现方法

### 3.1 CAD 通道（`cad_pipeline/`）

CAD 通道按下列步骤依次完成解析，各步骤之间通过中间结果传递，最终汇总为经校验的 JSON 文档。

1. **STEP 头部文本解析** → 元数据与单位（`SI_UNIT`）。
2. **OCP（pythonOCC / cadquery-ocp）读 BRep** → 用 `TopExp_Explorer` 遍历拓扑，`BRepGProp` 算体积/表面积/质心，`Bnd_Box` 算包围盒，`GeomAbs_SurfaceType` 给面分类。
3. **规则化特征识别** → 以面类型 + 相邻关系 + 参数为规则识别圆柱孔/凸台/阵列/圆角/倒角，输出置信度。该环节在几何层面具有较高可靠性；设计意图层面的语义恢复仍需后续研究。
4. **装配解析** → Part/Instance 遍历，输出 4×4 位姿矩阵。
5. **渲染** → OCCT/VTK 离屏多相机位姿，得到四视图；失败回退 matplotlib 线框。
6. **描述与封装** → 规则化中文摘要（可插拔 VLM）→ pydantic 校验 → JSON。

### 3.2 CAE 通道（`cae_pipeline/`）

CAE 通道在实现上以"零解压"为核心约束，即在不产生完整解压副本的前提下完成对压缩包内 HDF5 数据的随机读取。其处理流程如下。

1. **不解压全包** → 只读 zip 中央目录建立算例清单；解析时用自实现的块缓存 seek 适配器（`h5stream.py`）**直接在压缩包内随机读取 HDF5**，磁盘上不产生解压副本。需要反复访问时可选 `--access extract`，该模式亦只解出实际使用的文件。
2. **Fluent CFF 读取** → `/meshes/1`（节点坐标、面节点连接、c0/c1 两侧单元、zone 表），`/results/1/phase-k/{cells,faces}/SV_*/<段>`（带 `minId/maxId` 的分段场，按全局 id 对齐），`/settings/*`（Scheme 文本形式的求解设置）。
3. **网格几何** → 一次分块遍历面，用散度定理算**单元体积与重心**（面法向定向自动纠正），同时得到面面积与逐 zone 面积；质量指标按抽样统计。
4. **设置提取** → 自写 S 表达式解析器读 Scheme 文本，提取模型开关、相与材料、逐 zone 逐相边界条件、时间步、单位、初始条件（液滴球 / 液膜六面体）。
5. **结果场与派生量** → 清洗 Fluent 的 `±DBL_MAX` 未定义哨兵后统计；以水相 `VOF>0.5` 单元集合求液滴形态与铺展系数；壁面 zone 相邻单元求润湿面积。
6. **可视化与封装** → matplotlib 出切片云图/剖面图/直方图 → 规则化摘要或 VLM → JSON。

### 3.3 已解决的技术问题

在实现过程中，以下两个问题曾对解析的稳定性构成影响，现已解决。此处予以记录，供后续维护与二次开发参考。

- h5py 的"文件对象"驱动要求 `readinto` **填满**请求字节数，短读会被当成 EOF，报 `filter returned failure during read`；因此适配器必须循环取块直至满足请求长度。
- 未计算的场（如初始时刻的上一时间步压力）填 `±1.7977e308`，直接求均值会溢出，必须先清洗并单独记 `undefined_values`。

## 4. 使用方法

### 4.1 环境安装

两条通道的依赖分别定义在各自目录的 `requirements.txt` 中，安装方式如下。

```bash
pip install -r cad_pipeline/requirements.txt     # cadquery-ocp, pydantic, matplotlib…
pip install -r cae_pipeline/requirements.txt     # h5py, numpy, matplotlib, pydantic
```

CAE 通道**不需要安装 ANSYS Fluent / PyDPF / PyFluent**，直接读 CFF 文件格式。在 Windows 控制台下，建议先执行 `$env:PYTHONIOENCODING="utf-8"`，否则中文输出可能乱码。

### 4.2 CAD 通道

```bash
# 端到端自检：用 OCP 造一个带孔长方体，解析 + 渲染
python -m cad_pipeline.cli --selftest -o data

# 解析单个 STEP / 整个目录
python -m cad_pipeline.cli data\abc_step\extracted\00000004 -o json_out

# 只出数据不出图 / 不要文本描述
python -m cad_pipeline.cli <file.step> --no-render --no-caption

# IGES 解析；STEP -> IGES 转换；STEP/IGES 一致性比对
python -m cad_pipeline.cli <file.igs> --no-render -o json_out
python -m cad_pipeline.cli --to-iges data\abc_step\extracted -o data\iges_selfmade --limit 10
python -m cad_pipeline.cli --parity a.step a.igs

# 评测报告（evaluation_report.md）+ 本地 API（127.0.0.1:8000）
python -m cad_pipeline.cli --evaluate
python -m cad_pipeline.cli --serve
```

产物：`json_out/<名称>_cad.json`、`json_out/renders/<名称>_{iso,front,top,right}.png`、`.stl`。

> 大包取件：`CAD_dataset/abc_0000_step_v00.7z` 是 solid 压缩包，用 7-Zip 只解需要的那几个：
> `"C:\Program Files\7-Zip\7z.exe" x CAD_dataset\abc_0000_step_v00.7z -odata\abc_step\extracted <成员名>`。

### 4.3 CAE 通道

```bash
# 清单（只读目录，3 秒内出结果，不解压）
python -m cae_pipeline.cli --list

# 单个算例 / 按分组批量 / 全部
python -m cae_pipeline.cli Dry_surface/K86_S01 -o json_out
python -m cae_pipeline.cli --group Wet_surface -o json_out
python -m cae_pipeline.cli --all --limit 4 --no-hash -o json_out

# 评测报告（cae_evaluation_report.md）+ 本地 API（127.0.0.1:8100）
python -m cae_pipeline.cli --evaluate --limit 6 --no-hash
python -m cae_pipeline.cli --serve

# 其他求解器格式（CGNS 仅 HDF5 存储；BDF 自动展开 INCLUDE）
python -m cae_pipeline.cli --cgns 外部样例数据\cgns_examples\multi_hdf5.cgns.gz -o json_out
python -m cae_pipeline.cli --bdf 外部样例数据\pynastran\freedlm.bdf -o json_out
python -m cae_pipeline.cli --op2 外部样例数据\pynastran\0012_flutter.op2 -o json_out

# 跨格式转换与一致性（BDF <-> CGNS）
python -m cae_pipeline.cli --to-cgns 外部样例数据\pynastran\freedlm.bdf -o data\_cae_parity
python -m cae_pipeline.cli --to-bdf 外部样例数据\cgns_examples\yf17_hdf5.cgns.gz -o data\_cae_parity
python -m cae_pipeline.cli --parity-cae a.bdf a.cgns
```

常用开关：`--access stream|extract`（默认流式零解压）、`--cache-dir`、`--no-render`、`--no-caption`、`--no-hash`（跳过 sha256）、`--data-index`（选第几个 `.dat.h5` 快照）。

产物：`json_out/<分组>__<算例>_cae.json`、`json_out/viz/<分组>__<算例>_*.png`。

### 4.4 以库方式调用

除命令行接口外，两条通道亦对外暴露 Python 函数，可在自有程序中直接调用。

```python
from cad_pipeline import parse_cad_step
doc = parse_cad_step(r"data\abc_step\extracted\00000007\....step", out_dir="json_out")
print(doc["content"]["parts"][0]["topology"], doc["content"]["parts"][0]["features"][:2])

from cae_pipeline import parse_cae_case
doc = parse_cae_case("CAE_dataset/Dataset_....zip", "Dry_surface/K86_S01", out_dir="json_out")
print(doc["content"]["mesh"]["cells"], doc["content"]["derived"]["droplet"]["spreading_ratio"])
```

在 `--serve` 启动本地服务后，可通过 HTTP 接口访问相应功能：

- CAD（127.0.0.1:8000）：`GET /health`、`POST /parse_path`（给本地 STEP 路径）、`POST /parse_file`（上传 STEP）。
- CAE（127.0.0.1:8100）：`GET /health`、`GET /cases`（算例清单）、`GET /cases/{算例}/summary`（摘要 + 统计，不出图）、`POST /parse`（完整解析并落盘）。

## 5. 能力边界与后续方向

当前系统在各解析环节的可靠性存在差异，部分内容属研究性课题。下表按层级给出可靠性判定及相应说明，以便使用者合理预期解析结果的适用范围。

| 层级 | 可靠性 | 说明 |
| --- | --- | --- |
| CAD 几何-拓扑、质量属性、面分类 | 可靠 | 与 STEP 精度一致 |
| CAD IGES | 可靠 | 解析曲面退化自动还原；面片集合自动缝合；6 个样例与 STEP 结论一致 |
| CAD 特征（孔/凸台/阵列/圆角/倒角） | 部分 | 几何级可靠并带置信度；设计意图、GD&T、约束关系属研究难点 |
| CAD 原生 CATIA `.CATPart` / Parasolid / JT | 边界 | 受商业工具链约束，见《商业工具依赖说明.md》 |
| CAE 网格、结果场、分区统计 | 可靠 | 直接来自 CFF 文件 |
| CAE CGNS | 部分 | 支持 HDF5 存储（5 个官方样例通过）；ADF 存储需 cgnslib |
| CAE Nastran BDF | 部分 | 输入侧卡片/网格/气动统计（4 个样例通过）；结果侧由 OP2 覆盖 |
| CAE Nastran OP2 | 部分 | 位移/模态/特征值 + 应力/单元力（5 个样例通过，plate 与 .f06 对齐）；需可选依赖 pyNastran |
| CAE 跨格式一致性 | 可靠 | BDF↔CGNS 双向转换；壳/梁（3,197/4,494）与体单元（97,104/528,915）两类网格均一致 |
| CAE 边界条件完整参数 | 部分 | 取决于 case 是否存盘；完整模型树需 ACT/APDL |
| CAE 液滴形态/铺展系数 | 可靠（干表面） | 湿表面算例中液膜与液滴同相，管线会明确标注"不可分离"，只给水相总量 |
| 结构仿真（Mechanical RST） | 待建 | 信封与流程可复用，需另写 RST 读取器 |
| 精确界面量（PLIC 面积、接触角）、多时刻演化 | 待建 | 本数据集每算例仅一个 `.dat.h5` 快照 |

评测与实测数据详见：`evaluation_report.md`（CAD）、`cae_evaluation_report.md`（CAE）。

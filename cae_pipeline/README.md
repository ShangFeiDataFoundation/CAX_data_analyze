# CAE 管道（CFD / 结构仿真数据 → JSON）

把 ANSYS Fluent 的 **CFF 格式**结果文件（`.cas.h5` + `.dat.h5`）解析为结构化、语义化、
可追溯的 JSON，并输出云图/剖面图。已在 Python 3.13 + `h5py 3.16` 上跑通，
并在真实数据集 `Dataset_Numerical_study_on_microdroplets_interaction.zip`
（ANSYS Fluent 2025R1，18 个算例，解压后 2.12 GB）上验证。

同一信封下另支持 **CGNS（HDF5 存储）** 与 **Nastran BDF** 输入，用于跨求解器覆盖。
结构结果侧支持 **Nastran OP2**（可选依赖 pyNastran）。

## 特点：不解压全包

数据集是一个 1.9 GB 的 zip。本管道**默认零解压**：只读 zip 中央目录得到清单，
解析某个算例时用自实现的块缓存 seek 适配器（`h5stream.ZipMemberFile`）
**直接在压缩包内随机读取 HDF5**。

```bash
# 清单（不读数据体）
python -m cae_pipeline.cli --list

# 解析单个算例（流式，不落盘）
python -m cae_pipeline.cli K86_S01 -o json_out

# 需要反复访问同一算例时可选择「只解需要的文件」到缓存目录
python -m cae_pipeline.cli K86_S01 --access extract --cache-dir data/_cae_cache -o json_out
```

> 实测：`K86_S01`（40 MB case + 4 MB data，93 万节点 / 152 万单元）端到端约 14 s；
> 200 MB 级别的 data 文件算例约 17 s，磁盘上不产生任何解压副本。

## 依赖

```bash
pip install -r cae_pipeline/requirements.txt
```

即 `h5py`、`numpy`、`matplotlib`、`pydantic`；API 需要 `fastapi`/`uvicorn`。
**不需要安装 ANSYS Fluent、PyDPF 或 PyFluent** —— 直接读 CFF 文件格式。

## 运行

```bash
# 列出数据集中的 18 个算例
python -m cae_pipeline.cli --list

# 单个算例：网格 + 设置 + 结果场 + 图件 + JSON
python -m cae_pipeline.cli Dry_surface/K86_S01 -o json_out

# 批量：按顶层分组 / 全部 / 限制数量
python -m cae_pipeline.cli --group Wet_surface -o json_out
python -m cae_pipeline.cli --all --limit 4 --no-hash -o json_out

# 只要数据不要图 / 不要描述
python -m cae_pipeline.cli K45_S01 --no-render --no-caption

# 评测并生成报告
python -m cae_pipeline.cli --evaluate --limit 6 --no-hash

# 启动本地 HTTP API（127.0.0.1:8100）
python -m cae_pipeline.cli --serve

# CGNS（HDF5 存储；.cgns 或 .cgns.gz）
python -m cae_pipeline.cli --cgns 外部样例数据/cgns_examples/multi_hdf5.cgns.gz -o json_out

# Nastran BDF（自动展开 INCLUDE）
python -m cae_pipeline.cli --bdf 外部样例数据/pynastran/freedlm.bdf -o json_out

# Nastran OP2 结果（需 pip install -r cae_pipeline/requirements-op2.txt）
python -m cae_pipeline.cli --op2 外部样例数据/pynastran/0012_flutter.op2 -o json_out

# 跨格式转换与一致性
python -m cae_pipeline.cli --to-cgns 外部样例数据/pynastran/freedlm.bdf -o data/_cae_parity
python -m cae_pipeline.cli --to-bdf 外部样例数据/cgns_examples/yf17_hdf5.cgns.gz -o data/_cae_parity
python -m cae_pipeline.cli --parity-cae a.bdf a.cgns
```

命令行开关：`--access stream|extract`、`--cache-dir`、`--block-mb`、`--data-index`、
`--no-hash`、`--no-render`、`--no-caption`、`--report`。
环境变量 `CAX_CAE_DATASET` 可指定数据集路径。

## 输出

- `json_out/<group>__<case>_cae.json`：统一信封 JSON（结构见下）。
- `json_out/viz/<group>__<case>_{mesh_overview,field_slices,field_statistics,droplet_profile}.png`：
  壁面几何（含粗糙度放大）、过轴切片云图、场分布直方图、液滴铺展剖面。
- `json_out/cae_evaluation_report.md`：评测报告（成功率/耗时/物理自洽性检查）。

## JSON 结构

输出使用共享 Schema `2.0`，定义位于 `cax_core/schema.py`，导出文件为
`schemas/cae.schema.json`。顶层与 CAD 通道一致：`schema_version` /
`data_type="cae"` / `source` / `meta` / `content`。

| 区块 | 内容 |
| --- | --- |
| `source` | `primary_format`、统一 `files[]`、解析器版本、数据集条目和访问方式 |
| `meta.validation` | 警告与错误；`meta.provenance.steps` 记录处理链；`meta.capabilities` 声明能力 |
| `content.analysis` | 求解器版本、模型（湍流/能量/多相/重力/表面张力）、相与材料、时间步、单位 |
| `content.mesh` | 节点/面/单元数、边界面数、包围盒与尺度、单元类型分布、zone 表（含每 zone 面积）、质量指标（体积/面积分位、体积一致性） |
| `content.setup` | 材料、边界条件（逐 zone、逐相：速度/温度/材料）、cell zone、初始条件（`cell-registers` 液滴/液膜区域）、表面类型（dry/wet） |
| `content.field_catalog` | 文件里实际存了哪些 `SV_*` 场（含语义/单位），以及未存盘的常见量 |
| `content.results` | 每个场的全局统计（min/max/mean/std/p1..p99）、覆盖度、DBL_MAX 未定义值计数 |
| `content.zone_results` | 关键面场在壁面/入口等 zone 上的分区统计 |
| `content.derived` | 速度幅值、相体积、液滴形态（等效球半径/直径/高度/铺展系数）、壁面润湿面积 |
| `content.rendered_views` | 图件索引，含相对路径、哈希、尺寸和 MIME |
| `content.visual` | 结构化视觉画像（规则化或 DeepSeek VLM） |

### 派生量（CFD 语义）

- **液滴形态**：水相 `VOF > 0.5` 单元的重心集合 → 体积、包围盒、最大直径、高度、
  等效球半径；与 case 中 `kvapka_register`（球形初始液滴，r=5 µm）对比得到
  **铺展系数 D/D0**（Mundo 等用的 β 指标）。
- **湿表面算例**：`hladina_reg` 六面体 register 描述预置液膜，管道用其顶面高度把
  「液膜顶面以上的水」判为液滴，并在 JSON 中标注分离误差。
- **润湿面积**：壁面 zone 中相邻单元水相 `VOF > 0.5` 的面面积之和。
- **相体积**：`Σ VOF × 单元体积`，与计算域体积对比可做守恒性检查。

## 模块

| 模块 | 功能 |
| --- | --- |
| `h5stream.py` | zip 内 HDF5 随机读适配器（块缓存 + 完整填充读，HDF5 安全） |
| `dataset.py` | 数据集清单、算例分组、按需访问（stream / 选择性 extract） |
| `cff.py` | Fluent CFF 读取核心：分段数据集 → 全局 id 数组、zone 表、设置文本 |
| `scheme.py` | Fluent Scheme 设置文本解析（S 表达式、关联表、thread 块） |
| `mesh.py` | 节点/面/单元几何：面心、面积、单元体积与重心（分块，散度定理，自动定向）、质量指标 |
| `setup.py` | 求解设置：模型、材料、相、边界条件、时间步、单位、初始条件 |
| `fields.py` | 结果场目录与统计、未定义值清洗、派生量（速度幅值/液滴/润湿面积） |
| `render.py` | 壁面几何、切片云图、直方图、铺展剖面（matplotlib Agg） |
| `multimodal.py` | 兼容入口；实际使用 `cax_core.vision` |
| `schema.py` | 兼容导出；统一 Schema 位于 `cax_core/schema.py` |
| `pipeline.py` | 主流程编排 + 溯源 + 渲染 + 描述 |
| `cli.py` / `api.py` / `evaluate.py` | 命令行 / HTTP API / 评测报告 |

## 数据格式要点（踩坑记录）

- **分段数据集**：`/meshes/1/nodes/coords/<sec>`、`/faces/nodes/<sec>/{nodes,nnodes}`、
  `/results/1/phase-k/{cells,faces}/SV_*/<sec>`，段内有 `minId/maxId`，需要按全局 1-based id 对齐；
  段的子节点有时是 dataset、有时是 group（`faces/nodes`、`cells/ctype`）。
- **DBL_MAX 哨兵**：未计算的场（如初始时刻的 `SV_P_M1`）填 `±1.7977e308`，统计前必须清洗，
  否则求和溢出；管道把它们记为 `undefined_values`。
- **面法向定向**：CFF 面节点顺序的法向可能与「外向」相反，单元体积会整体为负；
  管道按总体积符号自动纠正并在 JSON 中记录 `volume_orientation_flipped`。
- **单位**：CFF 内部为 SI（坐标 m）；case 里的 `unit-table` 只是 GUI 显示单位（本数据集为 µm）。
  图件按 µm 绘制，JSON 里同时给出 `coordinate_unit` 与 `display_units`。
- **zone 类型码**：`1=fluid, 2=interior, 3=wall, 10=velocity-inlet`（已与
  `/settings/Thread Variables` 中的 `(zid kind name phase)` 文本核对），其余码为通用兜底表。
- **h5py 文件对象驱动**：`readinto` 必须填满调用方要求的字节数，短读会被 HDF5 当成 EOF
  并报 `filter returned failure during read`。

## 与 CAD 通道的关系

两者共用统一信封（`schema_version`/`data_type`/`source`/`meta`/`content`）与溯源字段，
`data_type` 区分 `cad` / `cae`，便于后续入库与跨系统关联建模。

## CGNS 与 Nastran BDF

### CGNS（`cgns.py`）

- 只读 **HDF5 存储层**；ADF 存储会给出明确提示（需要 cgnslib 或自实现）。
- 解析 Base / Zone / ZoneType / GridCoordinates / Elements_t / FlowSolution / ZoneBC / FlowEquationSet。
- 结构化区按顶点尺寸推算单元数；非结构区按 Elements_t 的类型与数量统计。
- 结果场给出 min/max/mean/std/p1–p99 统计，与 Fluent 通道共用 `results` 结构。
- 实测：cgns.org 的 5 个 HDF5 样例全部通过（最多 97,104 节点 / 528,915 单元 / 60 个场）。

> CGNS/HDF5 的标量节点名是 `" data"`（带前导空格），不是 `"data"`。

### Nastran BDF（`nastran.py`）

- 纯文本解析，不依赖 pyNastran；支持固定 8 列与自由格式，自动展开 `INCLUDE`。
- 输出：SOL 与子工况、GRID 数量与包围盒、单元类型分布、
  属性/材料/约束/载荷/气动卡片统计；结果侧（OP2）暂不解析。
- 实测：`f16_ma41`（SOL 145，气动弹性）、`0012_flutter`、`bah_plane`、`freedlm`
  四个样例全部通过。

### Nastran OP2（`op2.py`）

- OP2 是分层 Fortran 记录格式，完整自实现成本高，这里用 BSD 许可的 **pyNastran** 作为读取后端。
- 输出：节点/单元规模与包围盒、单元类型、属性/材料数量、位移表、特征向量、
  复特征值（实部/虚部/频率/阻尼比），以及**应力 / 应变 / 单元力 / 应变能**结果表
  （按单元类型自动枚举，如 `cquad4_stress`、`cbar_force`）。
- 结果表给出条目数、分量数、各分量最大值；已校验的单元类型附分量标签
  （CQUAD4 应力：fiber_distance / normal_x / normal_y / shear_xy / angle / major / minor / von_mises）。
- 频域语义：颤振分析中 `frequency_hz = |imag| / 2π`；纯实根（`kind="real"`）不给频率。
- 实测：`bah_plane.op2`（20 节点 / 10 个特征值）、`0012_flutter.op2`（243 节点 / 2 阶模态）、
  `plate.op2`（CQUAD4 应力，最大 von Mises 418.0055）、
  `bar_grid_point_forces.op2`（CBAR/CBEAM 单元力与应力）。
- 数值校验：`plate.op2` 的最大 von Mises 与同目录 `plate.f06` 文本结果
  （4.180055E+02）一致。
- 不作为强依赖：未安装 pyNastran 时只有 `--op2` 不可用，其余通道不受影响。

### 跨格式一致性（`exchange.py` / `parity.py`）

- `--to-cgns`：BDF 的 GRID + 单元 connectivity → CGNS/HDF5（自写的最小 CGNS 写出器 `cgns_writer.py`）。
- `--to-bdf`：CGNS/HDF5 → 自由格式 BDF。
- `--parity-cae`：比较两份结果 JSON 的网格层——节点数、单元数、单元类型（BDF/CGNS 类型名先归一化）、包围盒（相对容差）。
- 语义约定：体单元优先；有体单元时表面单元视为边界表示，不写入 BDF，避免重复计数；
  BDF 没有"面"概念，`boundary_faces` 差异只作提示。
- 实测：
  - `freedlm.bdf`（3,197 节点 / 4,494 单元，壳+梁）→ CGNS → 读回：**一致**；
  - `yf17_hdf5.cgns.gz`（97,104 节点 / 528,915 四面体）→ BDF → 读回：**一致**。

## 现状与后续

- ✅ 零解压的 zip 内 CFF 读取、18 个算例清单与批量解析；
- ✅ 网格/设置/结果场/派生量/图件/JSON/CLI/API/评测；
- ✅ 物理自洽性检查（体积积分、边界面对齐、液滴半径回归）。
- ✅ CGNS（HDF5 存储）读取：5 个官方样例通过，实测 97,104 节点 / 528,915 单元。
- ✅ Nastran BDF 读取：4 个样例通过（含 INCLUDE 递归与气动卡片）。
- ✅ Nastran OP2 读取：2 个样例通过（模态/颤振：特征向量 + 复特征值）。
- ✅ CAE 跨格式一致性：BDF↔CGNS 双向转换 + 网格层比对（壳/梁与体单元两类网格均一致）。
- ⏳ 与结构仿真（Ansys Mechanical RST）共用同一信封：需要另写 RST 读取器（PyDPF 或自实现）；
- ⏳ CGNS ADF 存储：需 cgnslib 或自实现 ADF 解析；
- ⏳ OP2 表覆盖仍有限（目前位移/特征向量/特征值；应力、力等表待扩展）；
- ⏳ 界面重构（PLIC）精确面积/接触角、时间序列演化（本数据集每算例只存一个 `.dat.h5` 快照）；
- ⏳ `dry_surface_impact_angles/*.avi` 视频侧的多模态描述。

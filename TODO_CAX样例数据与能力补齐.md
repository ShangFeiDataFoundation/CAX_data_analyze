# TODO：CAX 样例数据获取与能力补齐

范围限定：只覆盖 CAD 与 CAE 两条通道。记录时间：2026-10-15。

## 已完成（2026-10-15）

- [x] 抓取样例数据并落盘：`外部样例数据/`，28 个文件、87.0 MB，来源与校验见该目录 `README.md`。
- [x] 格式探测：确认为后续实现定下三条关键结论——
  1. CGNS 有两种存储层，**HDF5 存储可用现有 h5py 直接读**（5 个样例已验证），ADF 存储需 `cgnslib` 或自实现（12 个样例）；
  2. Ansys `.rst` **不是 HDF5**，需要专用读取器；
  3. Nastran `.bdf` 是纯文本，**可以直接文本解析**，是三种结构数据里门槛最低的。

## 已完成（2026-10-21）

- [x] **CAD IGES 通道**：`read_iges` + IGES 全局节解析（单位/比例/产品名）。
  - 解析曲面退化还原：IGES 往返把 CYLINDER/CONE/SPHERE 退化成 REVOLUTION，已按母线类型还原。
  - 面片集合自动缝合重建实体（多级容差，取首个无自由边结果）。
  - 轴向符号归一化：同一孔的两个半柱面在 IGES 中轴向相反，已统一，避免特征被拆开。
  - 装配体积改为按实体绝对值求和（复合体有符号体积会相互抵消）。
- [x] **STEP -> IGES 自造回归集**：`data/iges_selfmade/`，5 个 ABC 零件 + 1 个合成标注件。
- [x] **跨格式一致性检查**：`--parity`，6/6 全部一致（拓扑、体积、面类型、特征数）。
- [x] **真实整机装配 IGES**：NASA CRM `ASSY 8-1-11.igs`（26,370 面 / 11 实体 / 2,322 特征 / INCH）。
- [x] **CAE CGNS（HDF5 存储）**：5 个官方样例通过（最多 97,104 节点 / 528,915 单元 / 60 个场）。
- [x] **CAE Nastran BDF**：4 个样例通过，支持 INCLUDE 递归、固定/自由格式、气动卡片统计。
- [x] 新增输出全部通过统一 `CaeEnvelope` 校验（9 份 JSON，`is_valid=True`）。
- [x] **CAE Nastran OP2（结果侧）**：位移/特征向量/复特征值；2 个样例通过
      （`bah_plane` 10 个特征值、`0012_flutter` 2 阶模态）。
- [x] **OP2 结果表扩展**：按单元类型自动枚举应力/应变/单元力/应变能；
      `plate.op2` 最大 von Mises 418.0055 与 `plate.f06` 的 4.180055E+02 一致。
- [x] **CAE 跨格式一致性**：自写 CGNS/HDF5 写出器 + BDF↔CGNS 双向转换 + `--parity-cae`。
      - `freedlm.bdf`（3,197 节点 / 4,494 壳梁单元）→ CGNS → 读回：一致；
      - `yf17_hdf5.cgns.gz`（97,104 节点 / 528,915 四面体）→ BDF → 读回：一致。

## 已确认的取数渠道

| 目标格式 | 数据源 | 规模 | 获取方式 | 许可/注意 |
| --- | --- | --- | --- | --- |
| IGES（航空整机装配） | NASA CRM `geometry/original-cad-files` | 44 MB / 包 | 直接下载 | 公开，引用 NASA CRM |
| STEP + 语义 PMI/GD&T | NIST MBE PMI 测试用例（FTC/STC/CTC） | 单件级 | 页面 Free Download | 公开，ASME Y14 测试件 |
| 原生 B-Rep(.smt) + STEP + 装配 JSON | Fusion 360 Gallery（Assembly 子集） | 2.8 GB / 整包 | 官方 tools 定向取件 | 研究用途，需引用论文 |
| 结构结果 RST / MAPDL 输入 CDB | `ansys/example-data` | 单文件 0.3–90 MB | GitHub 单文件下载 | 官方示例仓库，按其 LICENSE |
| Nastran BDF/OP2、少量 Abaqus INP | `SteveDoyle2/pyNastran` | 单文件 ≤ 数 MB | GitHub 单文件下载 | 含商用求解器测试模型，注意再分发 |
| Abaqus INP（结构算例） | CalculiX 官方测试包 | 数十 MB / 包 | dhondt.de 下载 | 免费 |
| CGNS（含航空算例） | cgns.org Examples | 单文件 0.02–55 MB | 直接下载 | 站点 CC0，文件为提交者 as-is |
| CGNS/Nastran/Abaqus 网格（自造） | 现有 Fluent 算例网格 + meshio | 本地生成 | `pip install meshio` | 无外部依赖 |

## P0：今天可做（无外部依赖）

- [x] CAD：用现有 OCC 把 ABC 的 STEP 批量另存为 IGES（当前数据集只有 5 个零件，产出 6 件含合成件）。
- [x] CAD：解析 NASA CRM 整机装配 IGES（耗时约 175 s，只做拓扑/统计，未渲染）。
  - 前置：解压 `外部样例数据/nasa_crm/ASSY-8-1-11.igs.zip`（解压后 211 MB，命令见该目录 README）。
  - 该件面数远大于 ABC 零件，先只做拓扑遍历与统计，不渲染。
- [x] CAE：解析 cgns.org 的 **HDF5 存储**样例（5 个全通过）。
- [ ] CAE：CGNS **ADF 存储**（12 个样例）——需 cgnslib 或自实现 ADF 解析。
- [ ] CAE：解析 `ansys/example-data` 的 `.rst`，先只做拓扑/节点/单元计数与结果场目录。
- [x] CAE：解析 `pynastran/*.bdf`（纯文本）——4 个样例通过。

## P1：一周内（需要写新读取器）

- [ ] CAE：**RST 结构仿真通道**——复用统一信封，`content` 新增结构分支（网格、材料、约束/载荷、结果场目录、统计）。
  - 备选路线：自实现读取 vs 依赖 PyDPF；先评估依赖的许可与安装成本再定。
- [x] CAE：**Nastran BDF 通道**（输入侧，含 INCLUDE 与气动卡片）；OP2 结果侧仍待建。
- [x] CAE：**CGNS 通道**（HDF5 存储），与 Fluent 共用 `results`/`mesh` 结构。
- [x] 统一：**CAD 多格式一致映射检查**（`--parity`）；CAE 侧的同网格多格式比对待做。
- [ ] CAE：用 meshio 把现有 Fluent 网格转成 CGNS / Nastran / Abaqus，形成自造的"同一网格多格式"回归集。
- [ ] CAD：**AP242 PMI 存在性识别**——取 NIST 一个 FTC 的 AP203 与 AP242 两份，先只做"PMI/公差实体是否存在、数量多少"，不做语义解释。

## P2：需要外部条件

- [ ] CAD：**装配与位姿**——用 Fusion 360 Gallery 的单个装配（用官方取件工具定向下载，不整包下 2.8 GB）对照现有 `assembly` 结构，检验实例树与 4×4 位姿是否够用。
- [ ] CAD：**航空真实结构件**——向商飞索取 2–3 个型号零件（含 CATPart 或 JT），作为原生格式样例；解析能力按《商业工具依赖说明.md》的边界处理。
- [ ] CAE：**结构结果真数据**——商飞的 Nastran OP2 / ANSYS RST 实件结果，替换公开示例。
- [ ] 补齐 18 个算例的全量评测产物并落盘（当前只留了 6 算例报告、7 份 JSON）。

## 已放弃（见《商业工具依赖说明.md》）

- JT（ISO 14306）自主解析
- CATIA 原生 `.CATPart/.CATProduct` 自主解析
- Parasolid `.x_t` 自主解析
- Abaqus `.odb` 结果自主读取

## 完成判据

1. 每个新增格式都有：可运行读取器 + 至少 1 个真实样例 + 至少 1 项自动校验 + 评测报告条目。
2. 所有外部样例在 `外部样例数据/README.md` 中可溯源（来源、许可、字节数、sha256）。
3. `python -m <包>.cli --evaluate` 覆盖新增格式，不破坏既有 CAD/CAE 回归。

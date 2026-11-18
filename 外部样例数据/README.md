# 外部样例数据

抓取时间：2026-10-15（INCLUDE 附件补于 2026-10-21）。
共 **40 个下载文件、87.3 MB**；另含解压后的 `nasa_crm/ASSY 8-1-11.igs`（211.3 MB），
目录当前合计约 **308.6 MB**（不含本说明、`manifest.json` 与 `fetch_samples.py`）。

本目录存放外部公开数据集样例，用于 CAD / CAE 解析通道的格式扩展与回归测试。
与 `*_dataset/` 同级地位：**只读，不要修改或覆盖**。所有原始文件按下载原样保存（CGNS 保持 `.gz`，未解压）。

复现与校验（清单与 sha256 见同目录 `manifest.json`）：

```powershell
python 外部样例数据/fetch_samples.py --verify   # 只校验，不联网
python 外部样例数据/fetch_samples.py            # 补齐缺失文件后再校验
```

脚本对已存在且 sha256 一致的文件会跳过，不会重复下载。

## 一、来源与许可

| 目录 | 来源 | 许可 / 使用条件 |
| --- | --- | --- |
| `nasa_crm/` | NASA Common Research Model，Geometry → Original CAD files | 公开；引用 NASA CRM 相关文献 |
| `cgns_examples/` | cgns.org → Examples（用户提交的 CGNS 测试文件） | 站点 CC0；文件为提交者 as-is，非官方认证 |
| `ansys_example_data/` | GitHub `ansys/example-data` | Ansys 官方示例仓库，使用前核对其 LICENSE |
| `pynastran/` | GitHub `SteveDoyle2/pyNastran`（`models/aero/`） | 仓库测试模型，含商用求解器产出，注意来源与再分发限制 |

## 二、文件清单

sha256 为前 16 位，便于校验而不占版面。

| 文件 | 字节 | sha256(16) |
| --- | --- | --- |
| nasa_crm/ASSY-8-1-11.igs.zip | 43,920,268 | 54ee8e63350f0485 |
| cgns_examples/5blocks.cgns.gz | 21,627 | c38d933e3c01fe24 |
| cgns_examples/HeatingCoil.cgns.gz | 2,437,486 | 0f61fb14e34dbc3e |
| cgns_examples/MovingTransient.cgns.gz | 869,442 | f8e319a705273d35 |
| cgns_examples/StaticMixer.cgns.gz | 227,165 | 30d194bbc809a031 |
| cgns_examples/bump.cgns.gz | 6,404,184 | ab489e328a88bb4c |
| cgns_examples/bump_hdf5.cgns.gz | 6,414,020 | 222549e86af387e2 |
| cgns_examples/delta_vertex.cgns.gz | 3,085,617 | 1235c14da90bad85 |
| cgns_examples/multi.cgns.gz | 2,270,766 | b1754924382cd77c |
| cgns_examples/multi_hdf5.cgns.gz | 2,298,770 | 7d29fed1b30e8d25 |
| cgns_examples/oversetnasa1.cgns.gz | 180,604 | 815e7310a6e9088b |
| cgns_examples/oversetnasa2.cgns.gz | 190,719 | b39dc7340c5e0959 |
| cgns_examples/particles_example.cgns.gz | 301,557 | 57e10a40b87523fc |
| cgns_examples/sqnz_s.adf.cgns.gz | 409,129 | 9be14056ba5b7a06 |
| cgns_examples/sqnz_s.hdf.cgns.gz | 446,341 | b8c02ac01bc94b54 |
| cgns_examples/trappedvtx_ngon.cgns.gz | 5,225,317 | a21b3a2422f8b6d8 |
| cgns_examples/tut21.cgns.gz | 68,629 | 2faaca5844f3a0c4 |
| cgns_examples/yf17_hdf5.cgns.gz | 7,462,731 | 3363310a5d0c120e |
| ansys_example_data/ASimpleBar.rst | 2,686,976 | f93aa930a216cde4 |
| ansys_example_data/composite_fatigue_file.rst | 458,752 | 0454145881c58aff |
| ansys_example_data/model_with_all_element_types_all_output.rst | 393,216 | adee99701d08bd78 |
| ansys_example_data/static.rst | 327,680 | fdf6c8e3887b28ac |
| pynastran/0012_flutter.bdf | 556 | ff4433daeaa33a79 |
| pynastran/0012_flutter.op2 | 219,764 | f22df8663c7e1c55 |
| pynastran/bah_plane.bdf | 1,525 | 6eb882e748532716 |
| pynastran/bah_plane.op2 | 13,944 | c5a396f5dd6eac84 |
| pynastran/f16_ma41.bdf | 79,163 | 52a35ee537fb4669 |
| pynastran/freedlm.bdf | 541,316 | 5ce0718d7fb07394 |
| pynastran/aero_cards.inc | 1,005 | 05ff2c1572d43179 |
| pynastran/flutter_cards.inc | 5,200 | b2b0d2b7643feb8d |
| pynastran/geom.inc | 10,101 | 2c47eca138f4f0ec |
| pynastran/rigid_modes.inc | 1,447 | ab67398672312605 |
| pynastran/aero_bah.inc | 732 | 69a8f99698371b68 |
| pynastran/interface_bah.inc | 209 | b01183cfee6bd68e |
| pynastran/structure_bah.inc | 1,677 | 2aaf09979d0a78f2 |
| pynastran/bah_plane.f06 | 150,454 | e6d21384db19015f |
| pynastran/op2_extra/plate.op2 | 18,936 | 25d51e5305768b01 |
| pynastran/op2_extra/plate.f06 | 73,115 | f387c1fd637a9edf |
| pynastran/op2_extra/bar_grid_point_forces.op2 | 18,596 | be1ac5afe3acbb41 |
| pynastran/op2_extra/static_solid_shell_bar_radial.op2 | 18,816 | f68e3e0086c522fb |

## 三、实测结论（抓取后即时验证）

1. **CGNS 存在两种存储层，必须分别处理**：本目录 17 个样例中，5 个是 **HDF5 存储**（`*_hdf5`、`sqnz_s.hdf`、`particles_example`），12 个是 **ADF 存储**。
   - HDF5 存储的可以直接用现有 `h5py` 打开（已验证），无需引入 `cgnslib`；注意其基节点命名不固定，实测出现过 `Base...`、`SQNZ`、`STREAM_00` 三种。
   - ADF 存储的既不是 HDF5 也不是文本，需要 `cgnslib` 或自实现 ADF 解析。
2. **Ansys `.rst` 不是 HDF5**：文件头为 `64 00 00 00 00 00 00 80 0c 00 00 00 ff ff ff ff`，是 MAPDL 结果文件的二进制记录结构，需要专用读取器（PyDPF 的 C 库或自实现）。
3. **Nastran `.op2` 是带记录标记的二进制**，同样需要专用读取器；`pyNastran` 可作为参考实现。
4. **Nastran `.bdf` 是纯 ASCII 文本**（实测以 `SOL`、`$ID`、`$` 开头），可直接用文本解析，与现有 STEP 头段解析的思路一致——这是三种格式里门槛最低的一个。
5. 所有 `.gz` 均已通过完整性校验（gzip 可完整解压）。

## 四、尚未抓取（需要时再取）

按"够用即可"的原则，以下未下载，避免占用磁盘：

| 内容 | 规模 | 位置 |
| --- | --- | --- |
| Fusion 360 Gallery 装配集（原生 `.smt` + STEP + 装配 JSON） | 2.8 GB | AutodeskAILab 官方 tools 定向取件 |
| NIST MBE PMI 测试用例（STEP AP203 + AP242 含 PMI/GD&T） | 单件级 | NIST 项目页 Free Download |
| CalculiX 结构算例（Abaqus 兼容 INP） | 数十 MB | dhondt.de `ccx_2.23.structest.tar.bz2` |
| CGNS 大文件：DLR-F6 翼身（55 MB）、SC10 叶片（34 MB）、delta（8 MB） | — | cgns.org Examples |
| NASA CRM 满尺寸装配 STEP（58 MB）、满尺寸 IGES（44 MB）、左翼 STEP（10 MB） | — | NASA CRM 同页 |
| 其余 `ansys/example-data` 的 `.rst`/`.cdb`（含 90 MB 级大文件） | — | 同一仓库，按需下载 |

## 五、NASA CRM 压缩包说明

`nasa_crm/ASSY-8-1-11.igs.zip` 内含单个 `ASSY 8-1-11.igs`，**解压后 211 MB**。
2026-10-21 已解压并完成解析（`data/_nasa_crm/`）：26,370 面 / 11 实体 / 2,322 特征，
单位 INCH，耗时约 175 s。若需重新解压：

```powershell
Expand-Archive -LiteralPath "外部样例数据\nasa_crm\ASSY-8-1-11.igs.zip" -DestinationPath "外部样例数据\nasa_crm\"
```

注意：该 IGES 是 model scale 整机装配，面数与体量都远大于现有 ABC 零件，解析耗时会明显上升，
建议先只做拓扑遍历与统计，不要直接进渲染流程。

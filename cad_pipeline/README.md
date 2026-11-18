# CAD 管道（整套流程）

把 STEP / IGES 文件解析为结构化、语义化、可追溯的 JSON，并输出多视角渲染图。
已在 Python 3.13 + `cadquery-ocp 8.0.1` 上跑通，并在真实 ABC 零件上验证。
IGES 与 STEP 的解析结论可用 `--parity` 直接比对。

## 依赖

```bash
pip install -r cad_pipeline/requirements.txt
```
即 `cadquery-ocp`（含 `vtk`/`matplotlib`/`numpy`）、`pydantic`；API 可选 `fastapi`。

## 运行

```bash
# 端到端自检：用 OCP 生成一个带圆柱孔的长方体，解析 + 渲染
python -m cad_pipeline.cli --selftest -o data

# 解析单个 STEP 文件
python -m cad_pipeline.cli path/to/model.stp -o json_out

# 批量解析目录下的所有 .stp/.step/.igs/.iges
python -m cad_pipeline.cli path/to/steps -o json_out

# IGES 直接解析
python -m cad_pipeline.cli path/to/model.igs --no-render -o json_out

# STEP -> IGES 批量转换（自造跨格式回归集）
python -m cad_pipeline.cli --to-iges data/abc_step/extracted -o data/iges_selfmade --limit 10

# 同一零件的 STEP / IGES 一致性比对
python -m cad_pipeline.cli --parity a.step a.igs

# 只解析、跳过渲染
python -m cad_pipeline.cli path/to/model.stp --no-render -o json_out

# 跳过多模态描述
python -m cad_pipeline.cli path/to/model.stp --no-caption -o json_out

# 评测 & 生成报告
python -m cad_pipeline.cli --evaluate

# 启动本地 HTTP API (127.0.0.1:8000)
python -m cad_pipeline.cli --serve
```

## 输出

- `json_out/<name>_cad.json`：结构化元数据（统一信封）。
- `json_out/renders/<name>_{iso,front,top,right}.png`：多视角渲染。
- `json_out/renders/<name>.stl`：三角化网格（VTK 离屏失败时由 matplotlib 出图）。

## JSON 结构要点

输出使用共享 Schema `2.0`，Schema 定义位于 `cax_core/schema.py`，导出文件为
`schemas/cad.schema.json`。顶层为统一信封：`schema_version` / `data_type` / `source` /
`meta` / `content`。`content` 固定包含：

- `product`：名称、作者、单位、STEP schema、标准。
- `parts[]`：包围盒、体积/面积、拓扑统计、面类型分布、
  特征列表（`cylindrical`/`pattern`/`round`/`chamfer`，含 subtype=hole/boss 与置信度）。
- `assembly`：复合形子实例与 4x4 位姿矩阵。
- `rendered_views[]`：iso/front/top/right 视角，含相对路径、哈希、尺寸和 MIME。
- `visual`：结构化视觉画像，含类别、别名、形状特征、判断依据和置信度。
- `source.files[]`：所有输入文件及 sha256；`meta.capabilities` 声明数据能力。
- `meta.validation`/`meta.provenance`：Schema 校验结果与处理链步骤。

## 模块

| 模块 | 功能 |
| --- | --- |
| `step_meta.py` | STEP 头段元数据 + `SI_UNIT` 单位识别 |
| `iges_meta.py` | IGES 全局节元数据（产品名/单位标志/模型比例） |
| `geometry.py` | BRep 拓扑/质量属性/包围盒/面类型/圆柱参数 |
| `convert.py` | STEP -> IGES 转换 |
| `parity.py` | 跨格式一致性检查（拓扑/质量属性/面类型/特征数） |
| `features.py` | 特征识别（圆柱/孔/凸台/阵列/圆角/倒角） |
| `assembly.py` | 装配结构与位姿矩阵 |
| `render.py` | 多视角渲染（VTK → matplotlib 回退） |
| `multimodal.py` | 兼容入口；实际使用 `cax_core.vision` |
| `schema.py` | 兼容导出；统一 Schema 位于 `cax_core/schema.py` |
| `pipeline.py` | 主流程编排 + 溯源 |
| `api.py` | FastAPI HTTP API |
| `evaluate.py` | 评测与报告 |

## 多模态（VLM）

默认使用规则化视觉画像。配置 Key 后调用 `deepseek-flash` 的多视图视觉分析，
并将结果整理为可搜索标签。配置优先级为显式参数、环境变量、`config.local.json`、
`config.json`。

字段价值和检索用法见根目录 `docs/CAX统一JSON与业务价值.md`。

## 现状与后续

- ✅ 几何/拓扑/质量属性解析（与 STEP 精度一致）。
- ✅ 基础几何元素分类（面类型）。
- ✅ 特征识别：圆柱/孔/凸台/阵列/圆角/倒角，带置信度。
- ✅ 装配结构与位姿解析。
- ✅ 多视角渲染（VTK 优先，空白则 matplotlib 回退）。
- ✅ 统一 JSON Schema 校验 + 溯源。
- ✅ CLI / 批量 / HTTP API / 评测。
- ✅ IGES 读取：解析曲面退化（CYLINDER/CONE/SPHERE → REVOLUTION）自动还原，
      面片集合自动缝合重建实体。
- ✅ STEP / IGES 一致性比对：6 个样例（5 个 ABC 零件 + 合成标注件）全部一致。
- ✅ 真实整机装配 IGES 跑通（NASA CRM：26,370 面 / 11 实体 / 2,322 特征 / 单位 INCH）。
- ⏳ 参数化/约束/GD&T、设计意图语义：研究难点，需后续研究性建模。
- ⏳ 原生 CATPart / Parasolid / JT：受商业工具链约束，见根目录《商业工具依赖说明.md》。

## 说明：数据来源

ABC 数据集 STEP 块 `abc_0000_step_v00.7z`（约 1.6 GB，含 10000 个零件）。该 7z 是
solid 包（LZMA2），可用 `7-Zip` 解出少量文件：

```bash
"C:\Program Files\7-Zip\7z.exe" x retrieve -odata\abc_step\extracted <member1> <member2>
```

本仓库 `data\abc_step\extracted\` 已解出 5 个真实 ABC 零件（`00000004..00000008`），
并用 `python -m cad_pipeline.cli data\abc_step\extracted -o json_out` 跑通，
得到 `json_out/*_cad.json` 与 `json_out/renders/*.png|.stl`。
拿到任意 `.stp/.step` 后，直接 `python -m cad_pipeline.cli <file>` 即可复用。

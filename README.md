# CAX_data_analyze

面向商用飞机 CAX 数据资源的解析、语义分类、检索与性能分析工具。

项目把 CAD 的 STEP/IGES 和 CAE 的 Fluent CFF、CGNS、Nastran BDF/OP2
解析为统一 JSON，并提供本地 Web 工作区用于扫描、检索、预览、分类筛选和性能分析。

## 主要能力

- CAD/CAE 共享 `schema_version / data_type / source / meta / content` 统一信封。
- CAD 提取几何、拓扑、质量属性、特征、装配和渲染图。
- CAE 提取网格、材料、边界条件、结果场、派生工程指标和可视化结果。
- `meta.classification` 对数据与元数据进行语义分类，支持字段级检索和智能体路由。
- 前端支持按数据/元数据层级及几何、材料、网格、结果场、质量等分类筛选。
- LLM 与非 LLM 性能以 span 形式记录，包括耗时、调用次数、token、降级和吞吐。
- 性能主界面只展示五项关键指标，完整信息在“性能详情”中查看和导出。

分类目录、检索场景和智能体用途见
[CAX数据与元数据分类设计.md](CAX数据与元数据分类设计.md)。

## 目录

```text
cad_pipeline/       CAD 解析、特征识别、渲染与评测
cae_pipeline/       CAE 解析、网格/设置/结果场与评测
cax_core/           共享 Schema、分类规则、视觉调用和性能追踪
cax_pipeline/       工作区扫描、SQLite 索引、FastAPI 与前端
schemas/            导出的 CAD/CAE JSON Schema
tests/              契约、分类、检索、性能和报告测试
docs/               使用说明与数据价值说明
demo/               可再生成的静态演示构建脚本
```

原始数据集、解析产物、渲染图、报告和本地配置不进入仓库。

## 环境

建议使用 Python 3.11 及以上版本，当前开发环境为 Python 3.13。

```powershell
$env:PYTHONIOENCODING = "utf-8"
pip install -r requirements-prototype.txt
pip install -r cad_pipeline/requirements.txt
pip install -r cae_pipeline/requirements.txt
```

CAE 通道不需要安装 ANSYS Fluent；它直接读取支持的 CAE 文件格式。

## 配置

复制示例配置并填写本地 API Key：

```powershell
Copy-Item config.example.json config.local.json
```

`config.local.json` 已被 Git 忽略。不要提交真实 API Key、浏览器缓存的 Key
或其他运行凭据。

## 启动前端

```powershell
python -m cax_pipeline.cli --serve
```

浏览器打开 `http://127.0.0.1:8200`。Windows 也可以双击 `启动前端.cmd`。

## 命令行

解析单个 CAD 文件：

```powershell
python -m cad_pipeline.cli path/to/model.step -o json_out
```

解析单个 Fluent 算例：

```powershell
python -m cae_pipeline.cli Dry_surface/K86_S01 -o json_out
```

运行项目自检：

```powershell
python -m cae_pipeline.cli --evaluate --limit 2 --no-hash
```

完整命令见 [CAX解析管线说明.md](CAX解析管线说明.md) 和
[docs/原型使用说明.md](docs/原型使用说明.md)。

## 测试

```powershell
python -m unittest tests.test_cax_contracts -v
node --check cax_pipeline/static/app.js
```

部分契约测试需要本地样例数据，默认路径包括 `data/`、`test_workspace/` 和
`外部样例数据/`。这些目录按仓库策略被忽略，不会随代码提交。

## 数据与安全约定

- 不提交商用飞机原始数据集、压缩包、全场数组和批量解析产物。
- 不提交已有 `json_out/`、渲染图、报告或 SQLite 运行索引。
- 不提交 `config.local.json`、API Key 或任何本地凭据。
- 数据集按需获取，只提取单个文件进行调试，避免全量解压。
- 本仓库只保存可复用代码、Schema、测试和说明文档。

# AGENTS.md

本仓库把 CAX 原始文件解析成统一 JSON：CAD 走 STEP，CAE 走 Fluent CFF。

## 目录

- `cad_pipeline/`、`cae_pipeline/`：两条独立通道，入口 `python -m <包名>.cli`
- `*_dataset/`：原始数据，只读
- `json_out/`、`data/`：解析产物
- `CAX解析管线说明.md`：用户说明；细节见各通道 `README.md`

## 约定

- 源文件统一 UTF-8；Windows 先设 `$env:PYTHONIOENCODING="utf-8"`，否则中文乱码。
- 数据集是大压缩包，**不要全量解压**，只按需取单个文件。
- 用 apply_patch 改代码；不要动 `*_dataset/` 和已产出的 JSON、图片。
- 自检：`python -m cae_pipeline.cli --evaluate --limit 2 --no-hash`。

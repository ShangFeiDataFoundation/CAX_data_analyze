# CAX 测试工作区

这个目录用于前端扫描和端到端测试，包含当前管线的代表性输入格式。

## 内容

- `cad/hex_nut.step`、`cad/hex_nut.igs`：合成的六角螺母，可用于搜索“螺母”。
- `cad/sample_box_hole.stp`：带通孔的合成支座。
- `cae/fluent_cff/`：一个按需取出的 Fluent case/data 算例。
- `cae/cgns/bump_hdf5.cgns.gz`：CGNS/HDF5 样例。
- `cae/nastran/freedlm.bdf`：Nastran 输入模型。
- `cae/nastran/plate.op2`：Nastran 结构结果。
- `.cax_reports/`：已经生成的每个文件报告，可直接用于测试读取、导出和重新生成。

## 使用

启动前端后，把下面这个目录作为工作区：

```text
C:\files\北航期间\CAX数据解析\test_workspace
```

建议测试：

1. 点击“选择目录”，选中本目录，再点击“扫描并解析”。
2. 搜索“螺母”或“中心通孔”。
3. 筛选 `CAD` 和 `CAE`，检查缩略图和统一 JSON。
4. 点击“导出报告”下载整批 ZIP，或在单个文件上导出/重新生成。

扫描产物写入 `data/frontend_runs/`，不会修改本目录中的文件。

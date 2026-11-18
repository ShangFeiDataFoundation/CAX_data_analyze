# CAX 统一 JSON 与业务价值

## 1. 设计目标

解析器不再把“格式转换”作为终点，而是把工程文件转换为可校验、可追溯、可检索的数据资产。

- 所有 CAD 输入共享同一份 CAD JSON Schema。
- 所有 CAE 输入共享同一份 CAE JSON Schema。
- 不同格式的“能力差异”通过固定字段和 `meta.capabilities` 表达，不通过临时增删字段表达。
- 原始网格和全场数组不写入 JSON，只写入可解释的统计量、派生量和资源引用。
- 渲染图、视觉摘要和检索标签与结构化数据放在同一文档中。

当前版本号为 `2.1`。已有 `2.0` 及更早产物保持只读，新解析结果使用新结构。

## 2. 共同信封

```json
{
  "schema_version": "2.1",
  "data_type": "cad|cae",
  "source": {
    "primary_format": "STEP",
    "files": [
      {
        "role": "model",
        "path": "workspace/model.step",
        "format": "STEP",
        "bytes": 123456,
        "sha256": "...",
        "hash_skipped": false
      }
    ],
    "parser": {"name": "cad_pipeline", "version": "0.4"},
    "parsed_at": "2026-10-23T12:00:00+08:00",
    "solver": null,
    "dataset": null
  },
  "meta": {
    "validation": {"is_valid": true, "warnings": [], "errors": []},
    "provenance": {"source_files": [], "steps": []},
    "capabilities": {
      "geometry": true,
      "mesh": false,
      "setup": false,
      "results": false,
      "render": true,
      "vision": true
    },
    "tags": [],
    "domain": "commercial_aircraft"
  },
  "content": {}
}
```

`source.files` 统一表达单文件以及 Fluent 的 case/data 多文件输入。`meta.capabilities` 让检索系统在解析前就能知道该记录具备哪些能力。`meta.validation` 和 `meta.provenance` 用于判断结果是否可信以及如何复现。

## 3. CAD JSON

CAD 的 `content` 固定包含：

```text
product / parts / assembly / rendered_views / visual
```

| 字段 | 内容 | 业务价值 |
| --- | --- | --- |
| `product` | 产品名、作者、单位、STEP/IGES 头和标准 | 数据归属、版本归档、单位一致性检查 |
| `parts[].topology` | 实体、壳、面、边、顶点数量 | 复杂度评估、CAD 软件兼容性检查、轻量化筛选 |
| `parts[].mass_props` | 体积、表面积 | 材料估算、零件相似度、异常零体积排查 |
| `parts[].bbox` | 三轴包围盒 | 尺寸筛选、装配空间分析、屏幕取景 |
| `parts[].face_types` | 平面、圆柱、球、B 样条等面类型统计 | 几何复杂度、加工方式推断、相似模型检索 |
| `parts[].features` | 孔、凸台、阵列、圆角、倒角及置信度 | 设计复用、DFM 检查、特征级搜索、CAE 前处理 |
| `assembly` | 实例层级与 4×4 位姿矩阵 | 装配关系分析、BOM 关联、部件定位 |
| `rendered_views` | 多视角图片及其哈希、尺寸、MIME | 可视化浏览、视觉模型输入、结果缓存 |
| `visual` | 视觉类别、别名、形状特征、判断依据、置信度 | 用“螺母”“六边形”“中心通孔”等语义找模型 |

CAD 数据适合支持零件库检索、设计复用、相似件推荐、几何质量筛查、加工特征统计，以及把检索到的模型送入后续仿真。

## 4. CAE JSON

CAE 的 `content` 固定包含：

```text
analysis / mesh / setup / field_catalog / results
/ zone_results / derived / rendered_views / visual
```

| 字段 | 内容 | 业务价值 |
| --- | --- | --- |
| `analysis` | 物理类型、求解器、模型、相、时间、单位 | 区分 CFD/结构/多相/模态算例，支持工况检索 |
| `mesh` | 节点、单元、面、zone、尺寸、单元类型、质量 | 网格规模预测、质量异常筛查、算力估算 |
| `setup` | 材料、边界条件、初始条件、载荷与约束 | 工况复用、仿真规范检查、结果复现 |
| `field_catalog` | 实际存盘场及其位置、语义、单位 | 避免假定某格式一定有某个结果场 |
| `results` | 场统计：min/max/mean/std/分位数、覆盖度 | 快速判断结果量级、异常检测、仿真对比 |
| `zone_results` | 关键场在边界或区域上的统计 | 定位局部热点和边界效应 |
| `derived` | 液滴铺展、润湿面积、模态频率、应力表等派生量 | 直接支持工程指标和业务规则 |
| `rendered_views` | 网格、云图、统计图和结构结果图 | 快速浏览、视觉检索、报告生成 |
| `visual` | 视觉类别、物理图形特征、检索文本 | 用图形特征检索 CFD、结构结果或液滴形态 |

CAE 数据适合支持仿真知识库、工况复用、求解器迁移对比、网格质量门禁、结果异常检测、代理模型训练和仿真报告自动生成。

不同格式的支持面并不相同。例如 BDF 通常只有输入模型，OP2 可能只有结构结果，CGNS 的求解设置取决于文件内实际节点。统一 Schema 保证字段存在，`capabilities`、空数组和警告说明真实能力，而不是把缺失值伪装成零。

## 5. 视觉描述与“找螺母”

视觉分析结果采用结构化对象，而不是只保存一段自由文本：

```json
{
  "kind": "vlm",
  "summary": "CAD 视觉判断：六角螺母；主要特征：六边形外轮廓、中心通孔",
  "model": "deepseek-flash",
  "provider": "deepseek",
  "profile": {
    "primary_class": "nut",
    "display_name_zh": "六角螺母",
    "aliases_zh": ["螺母", "螺帽"],
    "aliases_en": ["hex nut", "nut"],
    "shape_traits": ["六边形外轮廓", "中心通孔", "端面倒角"],
    "confidence": 0.91,
    "evidence": ["ISO 视图可见六角外形和中心通孔"],
    "search_text_zh": "六角螺母，中心通孔，紧固件",
    "search_text_en": "hex nut with central through hole"
  }
}
```

检索系统对类别、同义词、形状特征、摘要和路径加权。用户搜索“螺母”时，会扩展到“螺帽、六角螺母、六边形、中心通孔、nut、hex nut”等线索，因此即使模型没有准确输出零件名称，也可以通过几何特征命中。

本版本不计算 embedding，预留了后续图片向量和文本向量接口。当前检索模式是可解释的结构化标签与全文匹配。

## 6. 可信度和局限

- CAD 特征识别是几何启发式识别，置信度不等于设计意图确认。
- 视觉模型可能误判，应结合多视图和结构化几何信息使用。
- CAE JSON 保存的是统计与派生摘要，不替代原始全场数据。
- 文件哈希、解析器版本、处理步骤用于追溯；关闭哈希时会在 `source.files` 中明确标记。
- 工作区扫描只处理已解压文件，不递归进入压缩包。

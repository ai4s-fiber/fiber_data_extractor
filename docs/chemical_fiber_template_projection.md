# 化学纤维模板投影

## 定位

`化学纤维数据模板(2).pdf` 是模板界面的视觉导出，不是可直接驱动运行时
采集的 JSON Schema、OpenAPI 或可填写 PDF。项目因此采用“抽取事实 -> 模板投影”
的方式，不让 GPT 按整张模板强行填空。

当前实现的投影版本是 `chemical_fiber_projection_v1`。它是本地兼容层，不冒充
外部数据库的最终字段协议；真实字段 ID、枚举和写入规则需要从外部系统的机器可读
接口绑定。

## 接口

```text
GET /api/template-schema
GET /api/v1/template-schema
GET /api/projects/{project_id}/papers/{paper_id}/template-projection
GET /api/v1/projects/{project_id}/papers/{paper_id}/template-projection
```

单篇投影默认包含未映射事实。需要下载 JSON 时增加：

```text
?download=true
```

只查看当前已经有明确字段映射的值时使用：

```text
?include_unmapped=false
```

## 数据原则

- `FactCandidate`、`SampleCatalog` 和 `EvidenceItem` 是投影的来源，不改写 MinerU
  和 GPT-5.5 的抽取流程。
- 每个值保留 `raw_value`，并尽量解析出 `value_number`、`range_min`、
  `range_max` 和 `operator`。
- 每个值带有 `entity_key`，性能和结构数据没有明确样品归属时进入
  `paper:<id>:unassigned` 并标记 `needs_review`。
- 没有证据文本、样品归属不确定、置信度过低或没有实际值的内容不会标记为
  `extracted`。
- 未识别的事实不会丢失，而是进入
  `fiber_sample.<section>.unmapped.<stable_slug>` 和 `unmapped_facts`。
- 不生成 `not_reported` 的虚假空行。未报告字段在质量统计中不计为漏抽。

## 质量指标

投影中的 `quality` 只统计论文实际报告并进入抽取结果的字段，重点包括：

- `reported_field_count`
- `evidence_coverage`
- `mapped_value_count`
- `unmapped_fact_count`
- `status_counts`

这与“完整模板字段填充率”不同。完整模板中的设备、厂家、原始文件和工业化工艺
字段可能在论文中不存在，不能用它们惩罚文献抽取质量。

## 外部数据库绑定

接入真实数据库前需要取得以下机器可读资料：

- 字段稳定 ID、层级路径和数据类型；
- 枚举代码、单位代码和必填规则；
- 容器与重复表格的父子关系；
- 文件附件和原始测试文件的关联方式；
- 创建、更新、幂等键和部分记录写入规则；
- 一条已填写的真实样例记录。

在这些资料到位前，项目不会根据 PDF 截图猜字段 ID，也不会自动写入外部数据库。
拿到协议后，应先实现 dry-run 对照，再增加带幂等键和重试的同步适配器。

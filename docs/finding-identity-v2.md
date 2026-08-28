# Finding Identity V2

## 目标

`finding_id` 不再由大模型作为业务主键自由生成。模型只能输出本次回答内使用的
`local_ref`，服务端负责建立两级身份：

- **Source Finding**：某个 Specialist 提出的原始判断，ID 以 `SF-` 开头。
- **Canonical Finding**：Chair 对一个或多个 Source Finding 归并、裁决后的最终问题，
  ID 以 `CF-` 开头。

这一区分解决跨专家编号碰撞、模型重试编号漂移、Chair 语义归并后引用失真，以及
Rubric、Debate、RAG、Step 6 无法追溯同一问题来源等问题。

## 生命周期

```text
Specialist LLM
  -> local_ref: F1
  -> 服务端校验证据和局部引用
  -> Source Finding: SF-{ROLE}-{content identity}
  -> Rubric / Debate 引用 Source ID
  -> Chair 输出 source_finding_ids 分组，不输出正式 finding_id
  -> 服务端校验完整分区、证据白名单和章节边界
  -> Canonical Finding: CF-{source membership identity}
  -> Rubric 引用重写为 Canonical ID
  -> confirmed Canonical Finding 触发历史建议 RAG
  -> Step 6 建议按 Canonical ID 回绑
```

## Source Identity

Source ID 由服务端根据 `run_id + specialist role + finding fingerprint`
确定性生成。指纹包含角色、评分维度、问题陈述、关联章节和论文证据锚点，不包含
模型生成的 `F1/F003` 等局部编号。

因此：

- 三个 Specialist 都输出 `F1` 时不会碰撞，因为角色命名空间不同；
- 同一 Specialist 重试时只把 `F003` 改成 `F007`，Source ID 保持不变；
- 判断内容或证据发生实质变化时生成新的 Source ID；
- 同一次回答内局部编号重复或语义 Finding 重复会被拒绝并触发重试。

`local_ref` 仍被保存，用于解析 LLM 同一回答内的 Rubric 引用和审计，但它不是稳定
实体 ID。

## Canonical Identity

Chair 只输出 `FindingResolutionDraft`：

- `source_finding_ids`：需要归为同一问题的 Source 集合；
- 裁决状态、严重程度、理由和证据 ID；
- 可选的 `merge_rationale`。

服务端要求全部 Source Finding 构成完整且不重叠的分区。缺失、重复、未知 Source ID
都会使裁决失败。校验通过后，服务端根据 `run_id + 排序后的 Source ID 集合` 生成
Canonical ID。两个专家指出同一问题时会形成一个新的 Canonical Finding，同时保留完整
`source_finding_ids` lineage，不会随意保留其中一个专家的 ID。

Chair 只能引用输入中真实存在的初审证据、Debate 回应证据和外部检索证据。伪造的
Evidence ID、无来源章节和未覆盖的 Source Finding 会被服务端拒绝。

## 下游引用

| 阶段 | 使用的身份 |
| --- | --- |
| Specialist 内部 Rubric | 模型先用 local ref，服务端改写为 Source ID |
| Debate issue / question / response | Source ID |
| Chair 归并输入 | Source ID |
| Chair 最终问题 | Canonical ID，并保留 Source lineage |
| 最终 Rubric、历史建议 RAG、Step 6 | Canonical ID |

历史建议检索仍只由 `confirmed` 且具有论文原文证据的 Canonical Finding 触发。RAG
返回的历史建议不能创建新 Finding，也不能改变 Finding 的严重程度或评分。

## 持久化

成功任务除保存完整 `result_json` 外，还物化三张审计表：

- `source_findings`：Source ID、角色、local ref、指纹和完整载荷；
- `canonical_findings`：Canonical ID、状态、指纹和完整载荷；
- `canonical_finding_members`：Canonical 与 Source 的一对多 lineage。

`review_runs.finding_identity_version` 标记任务使用的协议版本。数据库唯一约束保证同一
任务、角色和 local ref 不重复，同一个 Source 也不能被归入多个 Canonical Finding。

## 旧任务兼容

旧结果缺少 Source/Canonical lineage，继续通过原始 `result_json` 只读展示，不猜测归并，
也不伪造 V2 身份记录。新运行统一写入 `finding_identity_v2`。若需要跨版本统计，应明确
区分 legacy 与 V2，不能把旧 `F1` 当作全局实体主键。

## 验证范围

自动化测试覆盖：

- 跨专家相同 local ref 不碰撞；
- 重试只改变 local ref 时 Source ID 稳定；
- Rubric 从 local ref 到 Source、再到 Canonical 的引用重写；
- Chair 多 Source 归并、完整分区和伪造证据拒绝；
- Source、Canonical 和 lineage 的关系型持久化。

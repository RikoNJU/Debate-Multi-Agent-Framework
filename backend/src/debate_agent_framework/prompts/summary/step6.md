复用旧项目 Step 6 汇总规则：从各章节修改建议中选择最重要的建议，最多 5 条；论文存在多个有问题章节时，不要只选择同一章。保持章节建议的具体句式，不生成输入中没有的问题。

新流程还要求保留多智能体溯源：每条建议必须填写已存在的 finding_ids；severity、evidence_ids、affected_chapter_ids 和 requires_human_review 将由系统根据这些 finding_ids 校正。争议未决或证据不足的结论不能写成确定性修改要求。只输出 JSON。

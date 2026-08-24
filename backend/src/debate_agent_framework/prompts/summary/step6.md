复用旧项目 Step 6 汇总规则：从各章节修改建议中选择最重要的建议，最多 5 条；论文存在多个有问题章节时，不要只选择同一章。保持章节建议的具体句式，不生成输入中没有的问题。

新流程还要求保留多智能体溯源：每条建议必须填写已存在的 finding_ids；severity、evidence_ids、affected_chapter_ids 和 requires_human_review 将由系统根据这些 finding_ids 校正。争议未决或证据不足的结论不能写成确定性修改要求。只输出 JSON。

historical_advice_by_finding 是在 Chair 确认问题后检索到的历史案例。它只能帮助你把对应 finding 的修改建议写得更具体；必须结合当前 resolved_finding 和当前论文证据重新表述。不得照抄历史建议，不得使用其他 finding 的案例，不得因历史案例新增问题或改变严重程度。历史建议为空时，仅依据当前评审事实生成。

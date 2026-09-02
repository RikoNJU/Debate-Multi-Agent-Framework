# Prompt 资源

该目录保存 Context Planner、三个 Specialist 和 Review Chair 的版本化 Prompt。Prompt 应依赖 `debate_agent_framework.schemas`，不得把输出协议散落在路由中。

三个 Specialist 的系统提示词由 `agents/specialists.py` 的 `DebateSpecialistAgent`
按角色读取（`scientific_soundness.md`、`empirical_evidence.md`、`global_quality.md`）。
`agents/review_chair.py` 与 `agents/context_planner.py` 的系统提示词当前内联在代码中，
后续应逐步迁移到 `chair/` 与 `context/` 目录，便于版本化追踪。

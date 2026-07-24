# Debate 论文评审框架扩展层

该目录参考睿文智评对后端资源进行分类。Multi-Agent 核心位于 `backend/src/debate_agent_framework/`，并在包内继续拆分 Agent、模型、工作流、接口和适配器。

```powershell
conda activate langgraph
cd D:\debate-multi-agent-framework
pip install -e ".[web]"
python -m debate_agent_framework.main
```

- API 文档：`http://localhost:8020/docs`
- 健康检查：`GET /api/debate/health`
- 提交任务：`POST /api/debate/runs`
- 查询任务：`GET /api/debate/runs/{task_id}`

该接口只用于框架联调。可通过 `.env.example` 修改监听地址、端口和 CORS；任务状态暂时保存在进程内。

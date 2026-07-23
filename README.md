# Debate 论文评审 Multi-Agent 框架

独立的证据驱动论文评审框架，通过三个全文视角 Specialist、Review Chair 和一轮定向 Debate 提升评审质量。

![Debate 评审流程](assets/workflow.svg)

## 项目结构

```text
src/debate_agent_framework/    Agent、状态图和数据契约
app/backend/                   Prompt、配置、适配器和可选 API
examples/                      示例评审输入
tests/                         工作流与接口测试
docs/                          设计方案和代码说明
assets/                        流程图
```

## 运行

```powershell
conda activate langgraph
cd D:\debate-multi-agent-framework
pip install -e ".[dev,web]"
pytest
debate-demo --input examples\review_input.json --output output\result.json
```

可选接口启动命令：

```powershell
python -m app.backend.main
```

- [代码框架说明](docs/code-framework.md)
- [V0 设计方案](docs/design-v0.md)

当前 Adapter 装配确定性的 Demo Agent，只用于验证框架闭环和原 Step 4/5 兼容输出。

# Prompt Cache 与模型调用观测 V2

## 目标

在不改变评审语义和 LangGraph 拓扑的前提下，减少重复传输论文全文造成的 Token、延迟和费用，并让每次模型调用的缓存命中、耗时和失败原因可观测。

本方案不在应用内缓存模型答案。它只是构造稳定的 Prompt 前缀，利用 OpenAI-compatible 服务商可能提供的自动 Prompt Cache；是否真实命中必须以服务商响应中的 `cache_hit_tokens` 为准。

## Prompt 装配

默认启用 `cache_v2`：

```text
稳定 system 指令
  + 稳定论文公共信息/正文（角色无关）
  + 本次任务指令、Role/Persona、Rubric、结构化输入（易变后缀）
```

三位 Specialist 初审使用同一个公共论文前缀，角色职责和输出 Schema 放在最后一条消息，因此可共享缓存前缀。`run_id`、重试次数、时间等易变元数据不进入前缀；前缀的规范 JSON 计算 SHA-256，仅用于审计和比较，不作为服务商缓存键。

Debate 和 Chair 不再重复携带论文全文：

- Specialist Debate 只接收被挑战的 Finding、相关章节片段、问题和必要的同行意见。
- Chair 接收 Specialist 结构化结论、Canonical Finding、证据、争议和回答。
- ReviewContext 中正文只保留一份，章节目录只保留 ID、名称、阶段和字符数；结构化文档只传定位摘要。

兼容开关：

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `DEBATE_PROMPT_LAYOUT` | `cache_v2` | 改为 `legacy` 可恢复旧 Prompt 消息布局 |
| `DEBATE_CONTEXT_POLICY` | `compact_v2` | 改为 `legacy` 可恢复 Debate/Chair 完整上下文 |
| `DEBATE_STRUCTURED_OUTPUT_MODE` | `prompt` | 设为 `json_schema` 时向兼容服务发送 response format |
| `DEBATE_JSON_STREAM` | `false` | JSON Agent 是否使用流式响应 |

## 调用级观测

模型客户端在最终成功或重试耗尽后产生一条调用指标：

```text
run_id / node / role / operation / model
prompt_tokens / cache_hit_tokens / cache_miss_tokens
completion_tokens / reasoning_tokens
latency_ms / estimated_cost_yuan
prompt_prefix_hash / status / error
```

指标通过 `ContextVar` 从 LangGraph 节点和动态 Specialist 分支关联到当前任务，写入 `model_call_metrics`。`review_runs` 同时维护总调用数、失败数、Token、缓存命中和估算费用汇总，任务查询 API 返回 `model_usage`。

错误字段最多保留 1000 字符。数据库不保存 Prompt、论文正文或模型原始响应。费用只有配置当前服务商单价后才有意义：

```env
DEBATE_INPUT_PRICE_PER_MILLION=0
DEBATE_CACHE_HIT_PRICE_PER_MILLION=0
DEBATE_OUTPUT_PRICE_PER_MILLION=0
```

## 数据流

```text
LangGraph node / Specialist Send
  -> model_call_scope(run_id, node, role)
  -> Prompt V2 prefix + incremental suffix
  -> OpenAI-compatible API
  -> usage / latency / terminal error
  -> model_call_observer
  -> model_call_metrics detail
  -> review_runs aggregate
  -> task API model_usage
```

## 验证标准

1. 同一论文、不同 Specialist 的公共前缀字节完全一致，`prompt_prefix_hash` 相同。
2. `run_id` 和运行元数据变化不改变公共前缀。
3. 论文正文不在 ReviewContext、章节和结构化块中重复传输。
4. Debate 数据包只包含被挑战 Finding 与相关章节。
5. 成功和最终失败调用均留下指标；重试中的瞬时错误不重复计数。
6. 用同一篇论文执行冷启动与热启动对照，只有服务商报告的缓存 Token 才计为命中。

## 当前边界

- 尚未用真实 DeepSeek 服务完成冷/热对照，因此不能宣称已有具体命中率或成本降幅。
- 不同 API 网关对缓存字段命名和计费规则可能不同；客户端兼容顶层命中字段及 OpenAI 风格 `cached_tokens`，上线前仍需核对实际响应。
- 当前为进程内回调同步写库，适合现有单机任务模型；引入独立任务队列后应改为异步遥测写入。

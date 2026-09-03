# 独立 AIGC 文本检测

## 功能边界

AIGC 检测是论文评审系统之外的独立辅助工具，入口为 `/aigc`。它不写入
Finding、Rubric、18 维评分、总分、Chair 裁决或历史建议 RAG，也不会影响评审任务
成功与否。

模型输出表示分类器对文本窗口的风险估计，不等于“AI 生成比例”，不能单独作为学术
不端认定依据。当前输出标记为 `calibrated=false`，在形成脱敏、人工标注的校准集之前，
不应对外宣称准确率、误报率或可用于自动处罚。

## 数据流

```text
上传 PDF
  -> 创建独立 AIGC task + 浏览器访问码
  -> MinerU 解析 PDF
  -> 读取结构化 block、页码和 bbox
  -> 排除标题、公式、图片等非正文块
  -> 在章节边界内构建 80-510 token 文本窗口
  -> yuchuantian/AIGC_detector_zhv3 本地批量推理
  -> 按 token 聚合窗口与章节风险
  -> SQLite 保存任务/窗口结果，本地目录保存 PDF/MinerU 产物
  -> 前端轮询并展示章节分布、文本预览和来源定位
```

默认目标窗口为 384 tokens，最大 510 tokens，为模型特殊 token 留出空间。窗口保留稳定
`segment_id`、原始 `block_id`、页码和坐标。公式、HTML 标记和图片路径不作为检测文本。

## API

| 方法     | 路径                                     | 说明                     |
| -------- | ---------------------------------------- | ------------------------ |
| `GET`    | `/api/debate/aigc/availability`          | 服务、依赖和模型配置状态 |
| `POST`   | `/api/debate/aigc/tasks`                 | 上传单篇 PDF 并创建任务  |
| `GET`    | `/api/debate/aigc/tasks/{task_id}`       | 使用访问码读取任务       |
| `POST`   | `/api/debate/aigc/tasks/{task_id}/retry` | 重试失败或中断任务       |
| `DELETE` | `/api/debate/aigc/tasks/{task_id}`       | 删除任务、PDF 和解析产物 |

除 availability 和创建接口外，任务接口使用 `X-AIGC-Access-Code`。访问码仅在创建时返回，
前端保存在当前浏览器 `localStorage`，数据库只保存 SHA-256 摘要。

## 启用方式

```powershell
pip install -e ".[web,ingestion,aigc]"
```

在 `backend/.env` 配置：

```env
DEBATE_AIGC_ENABLED=true
DEBATE_AIGC_MODEL_ID=yuchuantian/AIGC_detector_zhv3
DEBATE_AIGC_DEVICE=auto
DEBATE_AIGC_BATCH_SIZE=8
DEBATE_AIGC_AI_LABEL_INDEX=1
DEBATE_AIGC_MAX_CONCURRENCY=1
```

还需要有效的 `DEBATE_MINERU_TOKEN`。模型默认从 Hugging Face 下载并在首次请求时惰性
加载；离线部署可把 `DEBATE_AIGC_MODEL_ID` 指向本地模型目录，并设置
`DEBATE_AIGC_LOCAL_FILES_ONLY=true`。CPU 可以运行但速度较慢，GPU 显存不足时应降低
`DEBATE_AIGC_BATCH_SIZE`。

## 已知限制

- 当前分类概率未经本项目论文数据校准，阈值 `0.5/0.8` 只是可配置的展示阈值。
- 识别对象只包含可提取正文；扫描质量、OCR 错误和特殊排版会影响结果。
- 中文检测模型会受文本类型、润色、专业术语和训练分布漂移影响。
- 当前后台任务运行在 FastAPI 进程内；服务重启会把运行中任务标为 `interrupted`，可手动重试。
- 浏览器遗失访问码后无法读取或删除对应任务，需要后续管理员数据治理工具处理。

## 上线前验证

1. 建立经过授权和脱敏的人工/模型生成文本校准集，按论文、章节而不是随机句子切分训练测试集。
2. 报告 AUROC、AUPRC、ECE、不同文本长度及不同论文类型的误报率，并选择展示阈值。
3. 对 MinerU 解析失败、超大论文、模型 OOM、并发请求和服务重启执行故障测试。
4. 制定数据保留期限、删除流程和访问审计策略，不把真实论文或模型权重提交 Git。

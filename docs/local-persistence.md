# 本地论文持久化

系统使用 SQLite 保存论文、版本、产物索引和评审任务，使用本地文件系统保存
原始 PDF、MinerU 产物与结构化论文输入。

## 配置

```env
DEBATE_DATA_DIR=backend/data
DEBATE_DATABASE_URL=
```

`DEBATE_DATABASE_URL` 为空时，数据库位于
`backend/data/debate.db`。数据目录已加入 `.gitignore`，不能提交真实论文。

## 数据目录

```text
backend/data/
├── debate.db
├── mineru/
└── papers/
    └── {paper_id_hash}/
        └── {revision_id}/
            ├── source.pdf
            ├── full.md
            ├── content_list.json
            ├── structured_input.json
            └── mineru/
```

论文目录使用 `paper_id` 的 SHA-256 摘要，不直接使用用户输入构造路径。
每次上传都会生成新 `revision_id`，同一论文可以保留多个版本。

## 数据库迁移

应用启动时自动执行：

```powershell
alembic upgrade head
```

也可以在仓库根目录手动执行。生产部署应在启动 Web 进程前单独运行迁移。

## 查询接口

```text
GET /api/debate/papers/{paper_id}
GET /api/debate/papers/{paper_id}/runs
GET /api/debate/runs/{task_id}
```

论文详情接口只返回元数据、版本和产物数量，不公开服务器本地文件路径。

## 重启行为

已完成和失败任务会从 SQLite 恢复。服务启动时，上次仍为 `running` 的任务会
标记为 `interrupted`，不会伪装成继续运行。自动恢复执行需要后续接入独立任务队列。

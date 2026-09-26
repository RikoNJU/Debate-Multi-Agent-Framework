#!/usr/bin/env bash
# =============================================================================
# 衡文云审 · AI 论文评审系统 —— 一键打包脚本（比赛交付版）
#
# 用法:  bash scripts/package.sh
# 产物:  dist/release/ruiwen-zhiping-<YYYYMMDD>/...  及同名 tar.gz
# 卸载视角的说明见包内 CONTEST-DEPLOY.md
# =============================================================================
set -euo pipefail

PROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE="$(date +%Y%m%d)"
PKG_NAME="ruiwen-zhiping-${DATE}"
REL="${PROOT}/dist/release"
STAGE="${REL}/${PKG_NAME}"
TARBALL="${REL}/${PKG_NAME}.tar.gz"

echo "==> 清空旧的 stage/输出"
rm -rf "${STAGE}" "${TARBALL}"
mkdir -p "${STAGE}/backend" "${STAGE}/frontend" "${STAGE}/finetuning" \
         "${STAGE}/deploy" "${STAGE}/offline/aigc_detector_zhv3" "${STAGE}/offline/tectonic"

excl=(--exclude='__pycache__' --exclude='*.pyc' --exclude='.git'
      --exclude='node_modules' --exclude='.venv' --exclude='venv'
      --exclude='*.egg-info' --exclude='.pytest_cache' --exclude='.env'
      --exclude='*.log')

echo "==> 1/8 后端代码（不含 data 大件、节跳过 staged/.staging/日志）"
rsync -a "${excl[@]}" \
      --exclude='data/papers' --exclude='data/mineru' --exclude='data/aigc' \
      --exclude='data/checkpoints.db*' --exclude='data/.staging' --exclude='logs' \
      "${PROOT}/backend/" "${STAGE}/backend/"

echo "==> 2/8 后端内置数据（评审库 + RAG v2 语料库 + 历史建议）"
mkdir -p "${STAGE}/backend/data"
for item in debate.db corpus chroma_dense_v2 cleaned_chunks_v2.jsonl historical_advice_v2.jsonl; do
  [ -e "${PROOT}/backend/data/${item}" ] && cp -a "${PROOT}/backend/data/${item}" "${STAGE}/backend/data/"
done

echo "==> 3/8 前端（源码 + 已构建 dist，不含 node_modules）"
rsync -a "${excl[@]}" "${PROOT}/frontend/" "${STAGE}/frontend/"

echo "==> 4/8 微调工程（打开成功的那次 run, 16GB 基础模型不入包）"
rsync -a "${excl[@]}" \
      --exclude='models' --exclude='outputs/global_quality_qwen3_8b_v1/20260828_050412/checkpoint-100' \
      "${PROOT}/finetuning/" "${STAGE}/finetuning/"

echo "==> 5/8 工具脚本 / 文档 / 测试 / 资源"
rsync -a "${excl[@]}" "${PROOT}/scripts/" "${STAGE}/scripts/"
rsync -a "${excl[@]}" "${PROOT}/docs/" "${STAGE}/docs/"
rsync -a "${excl[@]}" "${PROOT}/tests/" "${STAGE}/tests/"
rsync -a "${excl[@]}" "${PROOT}/examples/" "${STAGE}/examples/"
rsync -a "${excl[@]}" "${PROOT}/assets/" "${STAGE}/assets/"
cp "${PROOT}/requirements.txt" "${PROOT}/pyproject.toml" "${PROOT}/alembic.ini" "${PROOT}/README.md" "${STAGE}/" 2>/dev/null || true

echo "==> 6/8 离线 AIGC 检测权重（解引用符号链接后实体拷贝）"
SNAP="$(ls -d "${HOME}"/.cache/huggingface/hub/models--yuchuantian--AIGC_detector_zhv3/snapshots/*/ | head -1)"
rsync -aL "${SNAP}" "${STAGE}/offline/aigc_detector_zhv3/"

echo "==> 7/8 离线 tectonic（LaTeX 评审表 PDF 导出）"
if [ -x "${HOME}/.local/bin/tectonic" ]; then
  cp "${HOME}/.local/bin/tectonic" "${STAGE}/offline/tectonic/tectonic"
fi
[ -d "${HOME}/.cache/Tectonic" ] && cp -a "${HOME}/.cache/Tectonic" "${STAGE}/offline/tectonic/Tectonic-cache"

echo "==> 8/8 生成部署脚本 / 环境模板 / 评委快速上手"
: "${DEBATE_LLM_API_KEY:=}"
: "${DEBATE_MINERU_TOKEN_VALUE:=}"
python3 - <<'PY' "${STAGE}/deploy/.env.example" "${PROOT}/backend/.env" "${DEBATE_LLM_API_KEY}" "${DEBATE_MINERU_TOKEN_VALUE}"
import sys
out, src, KEY, MTK = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
keep = {
    "DEBATE_API_KEY":"",
    "DEBATE_RUNTIME":"real",
    "DEBATE_PROVIDER":"openai_compatible",
    "DEBATE_MODEL":"deepseek-ai/DeepSeek-V4-Pro",
    "DEBATE_BASE_URL":"https://api.siliconflow.cn/v1",
    "DEBATE_TEMPERATURE":"0.2",
    "DEBATE_TIMEOUT_SECONDS":"120",
    "DEBATE_MINERU_API_BASE":"https://mineru.net/api/v4",
    "DEBATE_MINERU_TOKEN":"",
    "DEBATE_MINERU_MODEL_VERSION":"vlm",
    "DEBATE_MINERU_TIMEOUT_SECONDS":"600",
    "DEBATE_EVIDENCE_PROVIDER":"openalex",
    "DEBATE_V2_MODE":"v2",
    "RAG_V2_CORPUS_PATH":"backend/data/historical_advice_v2.jsonl",
    "DEBATE_V2_CHROMA_PATH":"backend/data/chroma_dense_v2",
    "DEBATE_V2_BM25_PATH":"backend/data/bm25_v2",
    "DEBATE_AIGC_ENABLED":"true",
    "DEBATE_AIGC_MODEL_ID":"offline/aigc_detector_zhv3",
    "DEBATE_AIGC_DEVICE":"auto",
    "DEBATE_AIGC_LOCAL_FILES_ONLY":"true",
    "DEBATE_BOOTSTRAP_ADMIN_USERNAME":"admin",
    "DEBATE_BOOTSTRAP_ADMIN_PASSWORD":"Admin@12345",
    "DEBATE_BOOTSTRAP_ADMIN_DISPLAY_NAME":"系统管理员",
}
lines = [
    "# ========= 衡文云审 环境配置模板（部署时按需填写） =========",
    "# 比赛演示前请确认 SiliconFlow + MinerU 账户有余额(见 CONTEST-DEPLOY.md)",
    "",
    "# 必填：下面两个 Key 决定评审主流程能否工作",
    "#  DEBATE_API_KEY  —— SiliconFlow API Key（同一 Key 也建议复制给 DEBATE_V2_API_KEY）",
    "#  DEBATE_MINERU_TOKEN —— MinerU 云端解析 Token",
    "",
]
for k, default in keep.items():
    env_val = default
    if k == "DEBATE_API_KEY" and KEY:
        env_val = KEY
    if k == "DEBATE_MINERU_TOKEN" and MTK:
        env_val = MTK
    lines.append(f"{k}={env_val}")
lines += [
    "",
    "# 历史建议 RAG v2（稠密+BM25→RRF→重排），embedding/重排走 SiliconFlow",
    "DEBATE_V2_API_KEY=",
    "# DEBATE_V2_EMBEDDING_ENDPOINT=https://api.siliconflow.cn/v1/embeddings",
    "# DEBATE_V2_EMBEDDING_MODEL=BAAI/bge-m3",
    "# DEBATE_V2_RERANK_ENDPOINT=https://api.siliconflow.cn/v1/rerank",
    "# DEBATE_V2_RERANK_MODEL=Qwen/Qwen3-Reranker-0.6B",
    "# DEBATE_V2_RERANK_THRESHOLD=6",
    "",
    "# ---- 可选，未配置时使用代码内默认值 ----",
    "# DEBATE_STEP5_LOCAL=0        # 1=启用本地 Qwen3-8B+QLoRA 做 Step5",
    "# DEBATE_REVIEW_TABLE_TIMEOUT=900   # 评审表 PDF 导出超时(秒)，首次启动需下载宏包",
    "# DEBATE_AIGC_BATCH_SIZE=8",
    "# DEBATE_AIGC_MEDIUM_THRESHOLD=0.5",
    "# DEBATE_AIGC_HIGH_THRESHOLD=0.8",
    "# HF_ENDPOINT=https://hf-mirror.com",
]
open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("generated", out)
PY

cat > "${STAGE}/deploy/install.sh" <<'SH'
#!/usr/bin/env bash
# 首次安装：Python 依赖 + 前端依赖 + 离线模型/LaTeX 就位
set -euo pipefail
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PKG_ROOT}"

echo "==> 后端依赖"
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev,web,ingestion,rag,aigc]"

echo "==> 前端依赖并构建"
cd frontend
npm install
npm run build
cd ..

echo "==> 离线 AIGC 权重"
mkdir -p "${HOME}/.cache/huggingface/hub/models--yuchuantian--AIGC_detector_zhv3/snapshots/v3-package"
rsync -a offline/aigc_detector_zhv3/ "${HOME}/.cache/huggingface/hub/models--yuchuantian--AIGC_detector_zhv3/snapshots/v3-package/"

echo "==> 离线 tectonic"
if [ -f offline/tectonic/tectonic ]; then
  mkdir -p "${HOME}/.local/bin"
  cp offline/tectonic/tectonic "${HOME}/.local/bin/tectonic"
  chmod +x "${HOME}/.local/bin/tectonic"
fi
[ -d offline/tectonic/Tectonic-cache ] && cp -a offline/tectonic/Tectonic-cache "${HOME}/.cache/Tectonic"

echo "==> 安装完成"
echo "下一步: cp deploy/.env.example backend/.env 填入 Key 后执行 bash deploy/start.sh"
echo "若 DEBATE_AIGC_MODEL_ID 指向本地路径, 可直接等价位 backend/.env 中"
echo 'DEBATE_AIGC_MODEL_ID="'${PKG_ROOT}'/offline/aigc_detector_zhv3"'
SH

cat > "${STAGE}/deploy/start.sh" <<'SH'
#!/usr/bin/env bash
# 一键启动：后端 8020 + 前端 3000
set -euo pipefail
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PKG_ROOT}"
[ -f .venv/bin/activate ] || { echo "请先执行 bash deploy/install.sh"; exit 1; }
[ -f backend/.env ] || { echo "请先生成 backend/.env (cp deploy/.env.example backend/.env 并填入 Key)"; exit 1; }
set -a; source backend/.env; set +a
mkdir -p backend-log

echo "==> 启动后端 :8020"
setsid .venv/bin/python -m uvicorn debate_agent_framework.main:app \
      --host 0.0.0.0 --port 8020 > backend-log/backend-$(date +%H%M%S).log 2>&1 &
echo $! > .backend.pid

echo "==> 启动前端 :3000"
cd frontend
setsid node node_modules/vite/bin/vite.js --host --port 3000 > ../backend-log/vite-$(date +%H%M%S).log 2>&1 &
echo $! > ../.frontend.pid
cd ..
sleep 2
echo "后端 PID $(cat .backend.pid)  前端 PID $(cat .frontend.pid)"
echo "浏览器访问 http://<本机IP>:3000"
SH

cat > "${STAGE}/deploy/stop.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PKG_ROOT}"
[ -f .backend.pid ] && kill "$(cat .backend.pid)" 2>/dev/null || true
[ -f .frontend.pid ] && kill "$(cat .frontend.pid)" 2>/dev/null || true
rm -f .backend.pid .frontend.pid
echo "已停止"
SH

cat > "${STAGE}/deploy/healthcheck.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
H="http://localhost:8020/api/debate"
echo "-- /health --"; curl -s --max-time 8 "${H}/health" || echo "(后端未启动)"
echo; echo "-- /aigc/availability --"; curl -s --max-time 8 "${H}/aigc/availability" || echo "(后端未启动)"
echo; echo "-- 前端 :3000 --"; curl -s -o /dev/null -w "HTTP %{http_code}\n" --max-time 8 http://localhost:3000/ || echo "(前端未启动)"
SH
chmod +x "${STAGE}"/deploy/*.sh

cat > "${STAGE}/CONTEST-DEPLOY.md" <<'MD'
# 衡文云审 · 比赛部署快速上手（评委版）

完整流程见 `docs/judge-deployment-test-guide.md`。这里是最短路径：

1. 准备环境：Linux + Python 3.11+；建议 8C/32G + 一块 ≥8G 显存 GPU。
2. 安装第三方账号（需有余额）：
   - SiliconFlow（评审主模型）：`https://cloud.siliconflow.cn`
   - MinerU（PDF 解析）：`https://mineru.net`
3. 安装并启动：
   ```bash
   bash deploy/install.sh
   cp deploy/.env.example backend/.env   # 填入上面两个账号的 KEY/TOKEN
   bash deploy/start.sh
   bash deploy/healthcheck.sh             # 确认 health + aigc/availability 就绪
   ```
4. 浏览器打开 `http://<本机IP>:3000`：
   - 学生评审（免登录）：上传论文 → 全程可视化观看“类型识别→多专家初审→辩论裁决→18维评分”
   - 教师工作台 / 管理后台：admin / Admin@12345（部署时可改 `DEBATE_BOOTSTRAP_ADMIN_*`）
   - AIGC 检测：上传 PDF → 逐块检测 + 色块标注 PDF
5. 故障速查：见 `docs/judge-deployment-test-guide.md` §8（402 余额、MinerU 瞬时失败、GPU 序号、评审表超时）。

## 包内离线资产
- `offline/aigc_detector_zhv3/`：AIGC 检测模型权重（无需联网，DEBATE_AIGC_MODEL_ID 指向本地目录即可）
- `offline/tectonic/`：LaTeX 引擎（评审表 PDF），首次导出无需联网下载
- `finetuning/outputs/global_quality_qwen3_8b_v1/20260828_050412/`：QLoRA 微调最佳 run 产物
MD

echo "==> 生成 tar.gz"
cd "${REL}"
tar -czf "${TARBALL}" "${PKG_NAME}"
du -sh "${TARBALL}" "${PKG_NAME}"
echo "完成: ${TARBALL}"
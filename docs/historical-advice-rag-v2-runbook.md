# Historical Advice RAG V2 Runbook

## Build Order

The legacy Chroma database is an immutable source. V2 artifacts are built beside it:

```powershell
python -m pip install -e ".[web,rag,dev]"
python scripts/batch_clean.py --source-db D:\paper-review-backend\backend\data\databases\user_result_cloud --strict
python scripts/build_chroma_dense_v2.py
python scripts/build_bm25_v2.py
python scripts/preflight_rag_v2.py `
  --corpus backend/data/rag_v2/corpus/historical_advice_v2.jsonl `
  --dense-db backend/data/rag_v2/chroma `
  --bm25-dir backend/data/rag_v2/bm25
```

`build_chroma_dense_v2.py` is fixed to `Qwen/Qwen3-Embedding-8B` and 4096 dimensions.
The embedding endpoint may be local or remote, but index construction and online queries must
use the same model revision and dimensions. Existing output directories are not replaced unless
`--force` is supplied; construction first happens in a temporary sibling directory.

## Runtime

```env
DEBATE_V2_MODE=shadow_v2
RAG_V2_CORPUS_PATH=backend/data/rag_v2/corpus/historical_advice_v2.jsonl
DEBATE_V2_CHROMA_PATH=backend/data/rag_v2/chroma
DEBATE_V2_BM25_PATH=backend/data/rag_v2/bm25
DEBATE_V2_EMBEDDING_ENDPOINT=https://api.siliconflow.cn/v1/embeddings
DEBATE_V2_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
DEBATE_V2_EMBEDDING_DIMENSIONS=4096
DEBATE_V2_RERANK_ENDPOINT=https://api.siliconflow.cn/v1/rerank
DEBATE_V2_RERANK_MODEL=Qwen/Qwen3-Reranker-0.6B
DEBATE_V2_API_KEY=
```

Modes:

- `off`: do not load or query V2 artifacts;
- `shadow_v2`: execute retrieval but do not pass results to Step 6;
- `v2`: pass accepted historical cases to Step 6 and expose provenance.

Step 7 always receives a separately generated RAG-free summary.

## Labeled Evaluation

The evaluation query set is JSONL, one query per line:

```json
{"query_id":"q-exp-001","route":"content","dense_query":"实验结果缺少统计显著性检验","bm25_query":"实验结果 统计显著性 重复实验","relevant_advice_ids":["adv_..."]}
```

Run the V2 ablations with:

```powershell
python scripts/ab_eval.py `
  --queries data/rag_eval_queries.jsonl `
  --corpus backend/data/rag_v2/corpus/historical_advice_v2.jsonl `
  --dense-db backend/data/rag_v2/chroma `
  --bm25-dir backend/data/rag_v2/bm25
```

Legacy A/B evaluation additionally requires the legacy database, collection names, and its own
`text-embedding-v4/2048` endpoint. The script never queries a legacy collection with Qwen vectors.
It reports Recall, MRR, nDCG, Precision@2, empty-result accuracy, and mean/P50/P95 latency.

## Current Verification Boundary

Automated tests verify the data contract, stable IDs, query construction, RRF hydration,
provenance, Step 6 consumption, and Step 7 isolation. This repository does not contain the
sensitive legacy database, generated V2 indexes, or teacher relevance labels. Do not claim a
retrieval-quality improvement until `ab_eval.py` has been run on governed labels.

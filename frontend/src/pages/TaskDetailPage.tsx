import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronDown, ChevronLeft, CheckCircle2, CircleAlert, Copy, ExternalLink, FileText, ShieldCheck, UsersRound } from 'lucide-react';

import { getRunSnapshot, getTaskAccess } from '../lib/reviewApi';

const ROLE_LABELS: Record<string, string> = {
  scientific_soundness: '科学严谨性专家',
  empirical_evidence: '实证证据专家',
  global_quality: '全局质量专家',
};

const STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  running: '评审进行中',
  succeeded: '评审已完成',
  failed: '评审失败',
  interrupted: '评审已中断',
};

function roleLabel(role: string | undefined): string {
  return (role && ROLE_LABELS[role]) || role || '评审专家';
}

function Accordion({ title, icon, children, open = false }: { title: string; icon?: React.ReactNode; children: React.ReactNode; open?: boolean }) {
  const [expanded, setExpanded] = useState(open);

  return (
    <section className="report-section">
      <button className="section-toggle" onClick={() => setExpanded(!expanded)}>
        <span className="section-icon">{icon}</span>
        <span>{title}</span>
        <ChevronDown className={expanded ? 'up' : ''} />
      </button>
      {expanded && <div className="section-content">{children}</div>}
    </section>
  );
}

export default function TaskDetailPage() {
  const { taskId = '' } = useParams();
  const [snapshot, setSnapshot] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let active = true;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    const loadSnapshot = async () => {
      if (!taskId) {
        setLoading(false);
        setError('缺少任务 ID');
        return;
      }

      setLoading(true);
      try {
        const payload = await getRunSnapshot(taskId);
        if (!active) return;
        setSnapshot(payload);
        setError(null);
        const status = payload?.status;
        if (status === 'queued' || status === 'running') {
          timerRef.current = setTimeout(loadSnapshot, 5000);
        } else {
          setLoading(false);
        }
      } catch (exc: any) {
        if (!active) return;
        setError(exc?.message || '任务详情获取失败');
        setLoading(false);
      }
    };

    loadSnapshot();

    return () => {
      active = false;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [taskId]);

  const result = snapshot?.result ?? null;
  const status = snapshot?.status;
  const score = result?.final_score ?? null;
  const publishedReview = snapshot?.published_review ?? null;
  const displayedScore = publishedReview?.total_score ?? score?.total_score;
  const title = result?.context?.profile?.title ?? '论文评审报告';
  const review = result?.synthesis?.global_review ?? null;
  const reviews = result?.independent_reviews ?? [];
  const issues = result?.debate_plan?.issues ?? [];
  const questions = result?.debate_plan?.questions ?? [];
  const responses = result?.debate_responses ?? [];
  const externalEvidence = result?.external_evidence ?? [];
  const chapterEvaluation = result?.synthesis?.chapter_evaluation ?? {};
  const workloadSummary = result?.synthesis?.workload_evaluation?.summary ?? '';

  const isPending = status === 'queued' || status === 'running';
  const isFailed = status === 'failed'
    || status === 'interrupted'
    || (status === undefined && error);
  const accessCode = getTaskAccess(taskId);

  const copyAccessCode = async () => {
    if (!accessCode) return;
    await navigator.clipboard.writeText(accessCode);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <div className="report-page">
      <header className="report-bar">
        <Link to="/student"><ChevronLeft />返回任务列表</Link>
        <div className="report-brand"><span>RW</span> 睿文智评</div>
        <span>评审报告</span>
      </header>

      <main className="report-main">
        <div className="report-title">
          <div>
            <span className="eyebrow">DEBATE REVIEW REPORT</span>
            <h1>{title}</h1>
            <p>
              <FileText size={15} /> {taskId} · {STATUS_TEXT[status ?? ''] ?? (error ? '加载失败' : '正在加载…')}
            </p>
          </div>
          {accessCode && <div className="access-receipt"><small>任务访问码</small><code>{accessCode}</code><button onClick={copyAccessCode} title="复制任务访问码"><Copy size={15}/>{copied ? '已复制' : '复制'}</button></div>}
          <div className="score-card">
            <small>最终评分</small>
            {displayedScore !== undefined && displayedScore !== null ? (
              <>
                <strong>{displayedScore}</strong>
                <span>{publishedReview ? '人工终审' : score?.grade}</span>
              </>
            ) : (
              <strong className="score-placeholder">—</strong>
            )}
          </div>
        </div>

        {loading || (isPending && !result) ? (
          <div className="report-loading">
            正在读取评审结果…（实时评审较慢，通常需要 10 分钟以上，页面会自动刷新）
            {status === 'running' || status === 'queued' ? ` 当前状态：${STATUS_TEXT[status]}` : ''}
          </div>
        ) : isFailed || error ? (
          <div className="report-error">
            <CircleAlert size={22} />
            <b>评审失败</b>
            <p>{snapshot?.error || error || '未知错误，请稍后重试。'}</p>
            <Link to="/">返回任务列表</Link>
          </div>
        ) : (
          <div className="report-grid">
            <aside className="report-nav">
              <b>报告目录</b>
              <a href="#specialists">01 三位专家意见</a>
              <a href="#debate">02 讨论与外部证据</a>
              <a href="#global">03 全局评审</a>
              <a href="#compatibility">04 Step 4 / 5 兼容结果</a>
              <a href="#score">05 最终评分</a>
              {publishedReview && <a href="#human-review">06 人工终审</a>}
            </aside>

            <div className="report-body">
              <div id="specialists">
                <Accordion title="三位 Specialist 的独立意见" icon={<UsersRound />} open>
                  {reviews.length ? reviews.map((reviewer: any, index: number) => (
                    <article className="specialist" key={reviewer.review_id || index}>
                      <div className="specialist-top">
                        <div>
                          <span className={`specialist-dot d${index}`} />
                          <strong>{roleLabel(reviewer.role)}</strong>
                        </div>
                        <small>置信度 {Math.round((reviewer.confidence || 0.8) * 100)}%</small>
                      </div>
                      <h4>正面观察</h4>
                      {(reviewer.strengths || []).map((item: string, i: number) => (
                        <p className="positive" key={i}>{item}</p>
                      ))}
                      {(reviewer.findings || []).map((finding: any, i: number) => (
                        <div className="finding" key={finding.finding_id || i}>
                          <b>{finding.dimension}</b>
                          <strong>{finding.claim}</strong>
                          <p>{finding.rationale}</p>
                        </div>
                      ))}
                    </article>
                  )) : <p className="empty-note">暂无独立评审意见</p>}
                </Accordion>
              </div>

              <div id="debate">
                <Accordion title="Debate 问题、回应和外部证据" icon={<CircleAlert />} open>
                  {(issues.length ? issues : questions.length ? questions : []).map((item: any, index: number) => (
                    <div className="debate-row" key={index}>
                      <span>{index + 1}</span>
                      <div>
                        <b>{item.title || item.prompt || item.evidence_query || '待讨论问题'}</b>
                        {item.prompt && <p>{item.prompt}</p>}
                        {item.description && <p>{item.description}</p>}
                      </div>
                    </div>
                  ))}
                  {!issues.length && !questions.length && <p className="empty-note">没有需要进入 Debate 的争议</p>}
                  {responses.map((response: any, index: number) => (
                    <div className="response" key={index}>
                      <b>{roleLabel(response.role)} · 回应</b>
                      <p>{response.response}</p>
                    </div>
                  ))}
                  {externalEvidence.map((evidence: any, index: number) => (
                    <div className="evidence" key={index}>
                      <b>{evidence.source_title || evidence.doi || evidence.url || '外部证据'}</b>
                      <p>{evidence.quote}</p>
                    </div>
                  ))}
                </Accordion>
              </div>

              <div id="global">
                <Accordion title="全局评审与章节评价" icon={<ShieldCheck />} open>
                  <div className="global-summary">
                    <b>总体评价</b>
                    <p>{review?.overall_summary}</p>
                    <div className="mini-tags">
                      {(review?.strengths || []).map((item: string, i: number) => (
                        <span key={i}>{item}</span>
                      ))}
                      {(review?.weaknesses || []).map((item: string, i: number) => (
                        <span className="weak" key={i}>{item}</span>
                      ))}
                    </div>
                  </div>
                  <div className="chapter-list">
                    {Object.entries(chapterEvaluation).map(([key, item]: [string, any]) => (
                      <div className="chapter-card" key={key}>
                        <strong>{item?.chapter_data?.chapter_name || key}</strong>
                        <p>{item?.chapter_data?.chapter_remark}</p>
                        <small>{item?.chapter_data?.scoring_impact}</small>
                      </div>
                    ))}
                    {!Object.keys(chapterEvaluation).length && <p className="empty-note">暂无章节评价</p>}
                  </div>
                </Accordion>
              </div>

              <div id="compatibility">
                <Accordion title="兼容性 / 工作量与结构评估" icon={<CheckCircle2 />} open>
                  <div className="compatibility-box">
                    <div>
                      <p>{workloadSummary || '暂无工作量与结构评估'}</p>
                    </div>
                  </div>
                </Accordion>
              </div>

              <div id="score">
                <Accordion title="最终评分" icon={<ExternalLink />} open>
                  <div className="score-panel">
                    {score ? (
                      <>
                        <div className="score-row">
                          <span>总分</span>
                          <b>{score.total_score}</b>
                        </div>
                        <div className="score-row">
                          <span>等级</span>
                          <b>{score.grade}</b>
                        </div>
                        <p>{score.overall_evaluation}</p>
                      </>
                    ) : (
                      <p className="empty-note">暂无评分</p>
                    )}
                  </div>
                </Accordion>
              </div>
              {publishedReview && <div id="human-review">
                <Accordion title="已发布的人工终审" icon={<CheckCircle2 />} open>
                  <div className="score-panel">
                    <div className="score-row"><span>人工终审总分</span><b>{publishedReview.total_score}</b></div>
                    <p>{publishedReview.advice_content || '教师未填写公开修改建议。'}</p>
                    <small>发布时间：{new Date(publishedReview.published_at).toLocaleString('zh-CN')}</small>
                  </div>
                </Accordion>
              </div>}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

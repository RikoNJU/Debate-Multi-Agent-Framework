import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronLeft, CheckCircle2, CircleAlert, ExternalLink, FileText, RotateCcw, ShieldCheck, UsersRound } from 'lucide-react';

import {
  downloadReviewTable,
  getRunSnapshot,
  replaceRetriedTask,
  retryReviewTask,
  upsertTaskRecord,
} from '../lib/reviewApi';

const ROLE_LABELS: Record<string, string> = {
  scientific_soundness: '科学严谨性专家',
  empirical_evidence: '实证证据专家',
  global_quality: '全局质量专家',
};

const POSITION_LABELS: Record<string, string> = {
  revise: '修正立场',
  maintain: '维持立场',
  defend: '辩护',
};

const STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  running: '评审进行中',
  succeeded: '评审已完成',
  failed: '评审失败',
  interrupted: '评审已中断',
};

function formatElapsed(startedAt?: string, now = Date.now()): string {
  if (!startedAt) return '刚刚开始';
  const elapsedSeconds = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes ? `${minutes} 分 ${seconds.toString().padStart(2, '0')} 秒` : `${seconds} 秒`;
}

function roleLabel(role: string | undefined): string {
  return (role && ROLE_LABELS[role]) || role || '评审专家';
}

function positionLabel(position: string | undefined): string {
  return (position && POSITION_LABELS[position]) || position || '';
}

/** 把每个工作流阶段的输出映射成可读摘要。 */
function stageDetail(stage: string, result: any): { title: string; body?: string } | null {
  if (!result) return null;
  const profile = result.review_profile;
  switch (stage) {
    case 'resolve_discipline_skill':
      return profile?.discipline_skill_id
        ? { title: `加载专业通用 Skill：${profile.discipline_skill_id}（v${profile.discipline_version ?? '—'}）` }
        : null;
    case 'step1_classify_paper':
      return profile?.legacy_paper_type
        ? { title: `论文类型识别：${profile.legacy_paper_type}` }
        : null;
    case 'resolve_review_skill':
      return profile?.skill_id
        ? { title: `加载评审 Skill：${profile.skill_id}（v${profile.version ?? '—'}）` }
        : null;
    case 'step2_classify_chapters': {
      const count = result.context?.chapters?.length;
      return count ? { title: `识别到 ${count} 个章节并划分阶段` } : null;
    }
    case 'build_context': {
      const paper = result.context?.profile;
      return paper?.title
        ? { title: '论文档案构建完成', body: `${paper.title}（${paper.paper_type ?? ''}）` }
        : null;
    }
    case 'independent_review': {
      const reviews = result.independent_reviews ?? [];
      if (!reviews.length) return null;
      return {
        title: `${reviews.length} 位专家完成独立评审`,
        body: reviews
          .map((review: any) => `${roleLabel(review.role)}：${(review.strengths ?? []).length} 项正面观察、${(review.findings ?? []).length} 项发现`)
          .join('；'),
      };
    }
    case 'plan_debate': {
      const issues = result.debate_plan?.issues ?? [];
      const questions = result.debate_plan?.questions ?? [];
      return issues.length || questions.length
        ? { title: `识别 ${issues.length} 个争议，拟定 ${questions.length} 个定向问题` }
        : null;
    }
    case 'retrieve_debate_evidence': {
      const count = result.external_evidence?.length ?? 0;
      return count ? { title: `检索到 ${count} 条外部证据` } : null;
    }
    case 'targeted_debate': {
      const responses = result.debate_responses ?? [];
      if (!responses.length) return null;
      return {
        title: `${responses.length} 位专家完成定向回应`,
        body: responses
          .map((response: any) => `${roleLabel(response.role)}（${positionLabel(response.position)}）：${response.response}`)
          .join('；'),
      };
    }
    case 'synthesize_review': {
      const chapters = Object.keys(result.synthesis?.chapter_evaluation ?? {});
      const summary = result.synthesis?.global_review?.overall_summary;
      return {
        title: `Chair 综合 ${chapters.length} 个章节的评审意见`,
        body: summary,
      };
    }
    case 'step5_workload_evaluation': {
      const summary = result.synthesis?.workload_evaluation?.summary;
      return summary ? { title: '结构与工作量评估完成', body: summary } : null;
    }
    case 'compatibility_gate':
      return { title: '兼容性校验通过', body: '章节键与工作量结构符合原 Step 4/5 输出约定' };
    case 'retrieve_cleaned_advice': {
      const count = result.summary_advice?.advice_count;
      return count ? { title: `检索到 ${count} 条历史建议（V2）` } : null;
    }
    case 'step6_summary_advice': {
      const summary = result.summary_advice?.summary;
      return summary ? { title: '关键修改建议汇总', body: summary } : null;
    }
    case 'retrieve_score_cases': {
      const cases = result.historical_score_cases ?? [];
      return cases.length ? { title: `检索到 ${cases.length} 条历史评分案例` } : null;
    }
    case 'step7_scoring': {
      const score = result.final_score;
      return score ? { title: `最终评分：${score.total_score}（${score.grade}）` } : null;
    }
    default:
      return null;
  }
}

function SpecialistsBlock({ reviews }: { reviews: any[] }) {
  if (!reviews.length) return <p className="empty-note">暂无独立评审意见</p>;
  return (
    <>
      {reviews.map((reviewer: any, index: number) => (
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
      ))}
    </>
  );
}

function DebateBlock({
  issues,
  questions,
  responses,
  externalEvidence,
}: {
  issues: any[];
  questions: any[];
  responses: any[];
  externalEvidence: any[];
}) {
  const rows = issues.length ? issues : questions;
  return (
    <>
      {rows.map((item: any, index: number) => (
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
          <b>{roleLabel(response.role)} · {positionLabel(response.position) || '回应'}</b>
          <p>{response.response}</p>
        </div>
      ))}
      {externalEvidence.map((evidence: any, index: number) => (
        <div className="evidence" key={index}>
          <b>{evidence.source_title || evidence.doi || evidence.url || '外部证据'}</b>
          <p>{evidence.quote}</p>
        </div>
      ))}
    </>
  );
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
  const [searchParams, setSearchParams] = useSearchParams();
  const [snapshot, setSnapshot] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [clock, setClock] = useState(Date.now());
  const [refreshKey, setRefreshKey] = useState(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [view, setView] = useState<'steps' | 'debate' | 'report'>(() => {
    const tab = searchParams.get('tab');
    return tab === 'steps' || tab === 'debate' || tab === 'report' ? tab : 'steps';
  });
  const viewTouched = useRef(false);

  const selectView = (next: 'steps' | 'debate' | 'report') => {
    viewTouched.current = true;
    setView(next);
    setSearchParams({ tab: next }, { replace: true });
  };

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

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

      // 本地草稿尚未创建后端任务，直接提示等待上传完成
      if (taskId.startsWith('local-')) {
        setLoading(false);
        setError('该论文正在上传解析中，请稍后从任务列表进入查看');
        return;
      }

      setLoading(true);
      try {
        const payload = await getRunSnapshot(taskId);
        if (!active) return;
        setSnapshot(payload);
        setError(null);
        upsertTaskRecord(taskId, payload);
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
  }, [taskId, refreshKey]);

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
  const progress = Math.max(0, Math.min(100, snapshot?.progress_percent ?? 0));
  const stageEvents = snapshot?.stage_events ?? [];
  const elapsed = formatElapsed(snapshot?.created_at, clock);
  const quietSeconds = snapshot?.updated_at
    ? Math.max(0, Math.floor((clock - new Date(snapshot.updated_at).getTime()) / 1000))
    : 0;

  // 未手动切换视图时：评审中默认停在 Step 流程，完成后默认展示评审报告
  useEffect(() => {
    if (viewTouched.current) return;
    if (searchParams.get('tab')) return;
    if (status === 'succeeded') setView('report');
    else if (status === 'queued' || status === 'running') setView('steps');
  }, [status, searchParams]);

  const retry = async () => {
    if (!taskId || retrying) return;
    setRetrying(true);
    setRetryError(null);
    try {
      const submission = await retryReviewTask(taskId);
      replaceRetriedTask(taskId, submission);
      // 断点续跑复用同一任务编号，刷新快照并重启轮询
      setSnapshot(submission as any);
      setError(null);
      setRetrying(false);
      setRefreshKey(key => key + 1);
    } catch (exc) {
      setRetryError(exc instanceof Error ? exc.message : '重新评审失败');
      setRetrying(false);
    }
  };

  const exportTable = async () => {
    if (!taskId || exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      await downloadReviewTable(taskId);
    } catch (exc) {
      setExportError(exc instanceof Error ? exc.message : '导出 18 维评审表失败');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="report-page">
      <header className="report-bar">
        <Link to="/student"><ChevronLeft />返回任务列表</Link>
        <div className="report-brand"><span>南京大学</span> 睿文智评</div>
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

        <div className="detail-tabs portal-tabs">
          <button className={view === 'steps' ? 'active' : ''} onClick={() => selectView('steps')}>Step 流程</button>
          <button className={view === 'debate' ? 'active' : ''} onClick={() => selectView('debate')}>三维度辩论</button>
          <button className={view === 'report' ? 'active' : ''} onClick={() => selectView('report')}>评审报告</button>
        </div>

        {loading || (isPending && !result) ? (
          view === 'steps' ? (
            <div className="report-loading progress-view">
              <div className="progress-heading">
                <div>
                  <small>评审已进行 {elapsed}</small>
                  <strong>{snapshot?.current_stage_label || (loading ? '正在读取任务状态' : STATUS_TEXT[status])}</strong>
                </div>
                <b>{progress}%</b>
              </div>
              <div className="progress-track" aria-label={`评审进度 ${progress}%`}>
                <i style={{ width: `${progress}%` }} />
              </div>
              {quietSeconds >= 180 && status === 'running' && (
                <p className="progress-warning">
                  当前步骤已等待 {formatElapsed(snapshot?.updated_at, clock)}，模型服务可能正在排队或重试，页面会继续自动刷新。
                </p>
              )}
              <div className="stage-timeline">
                {stageEvents.length ? stageEvents.map((event: any) => (
                  <div className={`stage-item ${event.status}`} key={event.stage}>
                    {event.status === 'succeeded' ? <CheckCircle2 size={16} /> : event.status === 'failed' ? <CircleAlert size={16} /> : <span className="stage-spinner" />}
                    <span>{event.label}</span>
                    <small>{event.status === 'running' ? '进行中' : event.status === 'succeeded' ? '已完成' : '失败'}</small>
                  </div>
                )) : <p>任务已进入评审队列，正在等待第一个阶段开始。</p>}
              </div>
            </div>
          ) : (
            <div className="report-loading progress-view">
              <strong>评审仍在进行中</strong>
              <p>{view === 'debate' ? '三维度辩论' : '评审报告'}将在评审结束后生成，请先查看 Step 流程。</p>
            </div>
          )
        ) : isFailed || error ? (
          <div className="report-error">
            <CircleAlert size={22} />
            <b>{error ? '页面加载失败' : '评审失败'}</b>
            {error && <p>{error}</p>}
            {!error && <p>{snapshot?.error || '未知错误，请稍后重试。'}</p>}
            {taskId?.startsWith('local-') && (
              <p>该页面是上传过程中产生的临时记录，请返回任务列表重新进入对应任务。</p>
            )}
            {retryError && <p>{retryError}</p>}
            {(status === 'failed' || status === 'interrupted') && (
              <button onClick={retry} disabled={retrying}>
                <RotateCcw size={16}/>{retrying ? '正在重新提交...' : '重新评审'}
              </button>
            )}
            <Link to="/student">返回任务列表</Link>
          </div>
        ) : (
          <div className={view === 'report' ? 'report-grid' : 'report-grid report-grid-wide'}>
            {view === 'report' && (
              <aside className="report-nav">
                <b>报告目录</b>
                <a href="#specialists">01 三位专家意见</a>
                <a href="#debate">02 讨论与外部证据</a>
                <a href="#global">03 全局评审</a>
                <a href="#compatibility">04 Step 4 / 5 兼容结果</a>
                <a href="#score">05 最终评分</a>
                {publishedReview && <a href="#human-review">06 人工终审</a>}
              </aside>
            )}
            <div className="report-body">
              {view === 'steps' && (
                <div className="workflow-steps">
                  <div className="workflow-step-summary">
                    <b>多智能体 Debate 工作流</b>
                    <span>共 {stageEvents.length} 个步骤 · 评审已进行 {elapsed}</span>
                  </div>
                  {stageEvents.map((event: any, index: number) => {
                    const detail = stageDetail(event.stage, result);
                    return (
                      <div className={`workflow-step ${event.status}`} key={event.stage || index}>
                        <div className="workflow-step-index">
                          {event.status === 'succeeded' ? <CheckCircle2 size={17} /> : event.status === 'failed' ? <CircleAlert size={17} /> : <span className="stage-spinner" />}
                        </div>
                        <div className="workflow-step-main">
                          <div className="workflow-step-head">
                            <strong>{event.label}</strong>
                            <span>
                              {event.status === 'succeeded' ? '已完成' : event.status === 'failed' ? '失败' : '进行中'}
                              {typeof event.progress_percent === 'number' && ` · ${event.progress_percent}%`}
                            </span>
                          </div>
                          <code>{event.stage}</code>
                          {detail && (
                            <p className="workflow-step-detail">
                              <b>{detail.title}</b>
                              {detail.body && <span>{detail.body}</span>}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {view === 'debate' && (
                <>
                  <div id="specialists">
                    <Accordion title="三位 Specialist 的独立意见（三个维度）" icon={<UsersRound />} open>
                      <SpecialistsBlock reviews={reviews} />
                    </Accordion>
                  </div>
                  <div id="debate">
                    <Accordion title="Debate 问题、回应和外部证据" icon={<CircleAlert />} open>
                      <DebateBlock issues={issues} questions={questions} responses={responses} externalEvidence={externalEvidence} />
                    </Accordion>
                  </div>
                </>
              )}

              {view === 'report' && (
                <>
                  <div id="specialists">
                    <Accordion title="三位 Specialist 的独立意见" icon={<UsersRound />} open>
                      <SpecialistsBlock reviews={reviews} />
                    </Accordion>
                  </div>

                  <div id="debate">
                    <Accordion title="Debate 问题、回应和外部证据" icon={<CircleAlert />} open>
                      <DebateBlock issues={issues} questions={questions} responses={responses} externalEvidence={externalEvidence} />
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
                  {status === 'succeeded' && (
                    <div className="export-actions report-export">
                      <button className="export-button" onClick={exportTable} disabled={exporting}>
                        {exporting
                          ? '正在生成...'
                          : publishedReview
                            ? '导出 18 维评审表（教师终审版）'
                            : '导出 18 维评审表（AI 预审版）'}
                      </button>
                      {!publishedReview && <small>教师发布终审后将自动切换为终审版</small>}
                      {exportError && <small className="export-error">{exportError}</small>}
                    </div>
                  )}
                  {publishedReview && <div id="human-review">
                    <Accordion title="已发布的人工终审" icon={<CheckCircle2 />} open>
                      <div className="score-panel">
                        <div className="score-row"><span>人工终审总分</span><b>{publishedReview.total_score}</b></div>
                        <p>{publishedReview.advice_content || '教师未填写公开修改建议。'}</p>
                        <small>发布时间：{new Date(publishedReview.published_at).toLocaleString('zh-CN')}</small>
                      </div>
                    </Accordion>
                  </div>}
                </>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

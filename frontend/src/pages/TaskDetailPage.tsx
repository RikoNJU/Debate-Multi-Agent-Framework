import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronDown, ChevronLeft, CheckCircle2, CircleAlert, ExternalLink, FileText, ShieldCheck, UsersRound } from 'lucide-react';

import { getRunSnapshot } from '../lib/reviewApi';

const demo = {
  title: '面向大语言模型的多智能体论文评审框架研究',
  independent_reviews: [
    {
      role: '科学严谨性专家',
      confidence: 0.88,
      strengths: ['研究问题清晰，技术路线与研究目标保持一致。'],
      findings: [
        {
          dimension: '理论依据',
          claim: '核心方法的理论边界尚需更明确说明。',
          rationale: '当前论证充分呈现了方法流程，但缺少对适用条件和失效场景的界定。',
        },
      ],
    },
    {
      role: '实证证据专家',
      confidence: 0.82,
      strengths: ['实验指标覆盖了主要性能与效率维度。'],
      findings: [
        {
          dimension: '实验设计',
          claim: '缺少与强基线方法的完整对比。',
          rationale: '建议补充统一数据划分和消融实验，以验证各模块的独立贡献。',
        },
      ],
    },
    {
      role: '全局质量专家',
      confidence: 0.9,
      strengths: ['结构完整，章节衔接自然，摘要能够概括主要工作。'],
      findings: [
        {
          dimension: '写作表达',
          claim: '部分章节的研究贡献表述重复。',
          rationale: '可将创新点集中放在引言末尾，并在结论中对应回应。',
        },
      ],
    },
  ],
  debate_plan: {
    issues: [
      { title: '方法主张与实验验证是否匹配', prompt: '理论成立是否足以支撑论文声称的整体贡献？' },
      { title: '实验对比是否充分', prompt: '请说明缺失的关键验证并给出依据。' },
    ],
  },
  debate_responses: [
    { role: '实证证据专家', response: '现有结果能说明可行性，但尚不足以支持显著优越性的结论；建议增加公开基准上的强基线对比。' },
  ],
  external_evidence: [
    { source: 'Papers with Code · Benchmark guidance', quote: '应使用统一协议报告方法在标准基准上的性能。' },
  ],
  synthesis: {
    global_review: {
      overall_summary: '论文选题具有现实意义，整体结构与技术路线较为完整。建议重点补强实验验证、明确方法适用边界，并精炼创新点表述。',
      strengths: ['问题定义清楚', '论文结构完整', '应用场景明确'],
      weaknesses: ['强基线比较不足', '适用边界需要说明'],
      author_questions: ['新增实验是否能覆盖不同数据规模下的表现？'],
      confidence: 0.87,
    },
    chapter_evaluation: {
      chapter_1: {
        chapter_data: {
          chapter_name: '引言与研究背景',
          chapter_remark: '研究动机充分，建议将创新点与贡献边界拆分为明确条目。',
          scoring_impact: '轻微影响',
        },
      },
      chapter_2: {
        chapter_data: {
          chapter_name: '方法与实验设计',
          chapter_remark: '实验协议应补充强基线和消融分析。',
          scoring_impact: '中等影响',
        },
      },
    },
    workload_evaluation: {
      summary: '论文结构基本完整，摘要、目录与章节组织符合规范。',
      structure_evaluation: {
        completeness: { score: 86, analysis: '核心章节完整。' },
        abstract_and_keywords: { score: 84, analysis: '摘要和关键词基本规范。' },
        catalog_standardization: { score: 82, analysis: '目录层级清晰。' },
        chapter_standardization: { score: 78, analysis: '跨章节回指仍可加强。' },
        acknowledgement_standardization: { score: 85, analysis: '格式无明显问题。' },
      },
    },
  },
  final_score: {
    total_score: 82.4,
    grade: '良好',
    overall_evaluation: '论文达到较好的本科毕业论文水平，完成补充实验与表述修改后将更具说服力。',
    confidence: 0.86,
  },
};

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

  useEffect(() => {
    let active = true;

    const loadSnapshot = async () => {
      if (!taskId) {
        setLoading(false);
        return;
      }

      setSnapshot(null);
      setLoading(true);

      try {
        const payload = await getRunSnapshot(taskId);
        if (active) {
          setSnapshot(payload);
        }
      } catch {
        if (active) {
          setSnapshot({ task_id: taskId, status: 'failed', result: demo });
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    loadSnapshot();

    return () => {
      active = false;
    };
  }, [taskId]);

  const data = useMemo(() => snapshot?.result ?? demo, [snapshot]);
  const score = data.final_score ?? demo.final_score;
  const title = data.title ?? data.paper_title ?? '论文评审报告';
  const review = data.synthesis?.global_review ?? demo.synthesis.global_review;

  return (
    <div className="report-page">
      <header className="report-bar">
        <Link to="/"><ChevronLeft />返回任务列表</Link>
        <div className="report-brand"><span>RW</span> 睿文智评</div>
        <span>评审报告</span>
      </header>

      <main className="report-main">
        <div className="report-title">
          <div>
            <span className="eyebrow">DEBATE REVIEW REPORT</span>
            <h1>{title}</h1>
            <p>
              <FileText size={15} /> {taskId} · {snapshot?.status === 'succeeded' ? '评审已完成' : '正在加载评审结果…'}
            </p>
          </div>
          <div className="score-card">
            <small>最终评分</small>
            <strong>{score.total_score}</strong>
            <span>{score.grade}</span>
          </div>
        </div>

        {loading ? (
          <div className="report-loading">正在读取评审结果…</div>
        ) : (
          <div className="report-grid">
            <aside className="report-nav">
              <b>报告目录</b>
              <a href="#specialists">01 三位专家意见</a>
              <a href="#debate">02 讨论与外部证据</a>
              <a href="#global">03 全局评审</a>
              <a href="#compatibility">04 Step 4 / 5 兼容结果</a>
              <a href="#score">05 最终评分</a>
            </aside>

            <div className="report-body">
              <div id="specialists">
                <Accordion title="三位 Specialist 的独立意见" icon={<UsersRound />} open>
                  {(data.independent_reviews ?? demo.independent_reviews).map((reviewer: any, index: number) => (
                    <article className="specialist" key={index}>
                      <div className="specialist-top">
                        <div>
                          <span className={`specialist-dot d${index}`} />
                          <strong>{reviewer.role}</strong>
                        </div>
                        <small>置信度 {Math.round((reviewer.confidence || 0.8) * 100)}%</small>
                      </div>
                      <h4>正面观察</h4>
                      {(reviewer.strengths || []).map((item: string) => (
                        <p className="positive" key={item}>{item}</p>
                      ))}
                      {(reviewer.findings || []).map((finding: any) => (
                        <div className="finding" key={finding.claim}>
                          <b>{finding.dimension}</b>
                          <strong>{finding.claim}</strong>
                          <p>{finding.rationale}</p>
                        </div>
                      ))}
                    </article>
                  ))}
                </Accordion>
              </div>

              <div id="debate">
                <Accordion title="Debate 问题、回应和外部证据" icon={<CircleAlert />} open>
                  {(data.debate_plan?.issues || []).map((issue: any, index: number) => (
                    <div className="debate-row" key={index}>
                      <span>Q{index + 1}</span>
                      <div>
                        <b>{issue.title || '待讨论问题'}</b>
                        <p>{issue.prompt}</p>
                      </div>
                    </div>
                  ))}
                  {(data.debate_responses || []).map((response: any, index: number) => (
                    <div className="response" key={index}>
                      <b>{response.role} · 回应</b>
                      <p>{response.response}</p>
                    </div>
                  ))}
                  {(data.external_evidence || []).map((evidence: any, index: number) => (
                    <div className="evidence" key={index}>
                      <b>{evidence.source}</b>
                      <p>{evidence.quote}</p>
                    </div>
                  ))}
                </Accordion>
              </div>

              <div id="global">
                <Accordion title="全局评审与章节评价" icon={<ShieldCheck />} open>
                  <div className="global-summary">
                    <b>总体评价</b>
                    <p>{review.overall_summary}</p>
                    <div className="mini-tags">
                      {(review.strengths || []).map((item: string) => (
                        <span key={item}>{item}</span>
                      ))}
                    </div>
                  </div>
                  <div className="chapter-list">
                    {Object.entries(data.synthesis?.chapter_evaluation ?? demo.synthesis.chapter_evaluation).map(([key, item]: [string, any]) => (
                      <div className="chapter-card" key={key}>
                        <strong>{item.chapter_data?.chapter_name || key}</strong>
                        <p>{item.chapter_data?.chapter_remark}</p>
                        <small>{item.chapter_data?.scoring_impact}</small>
                      </div>
                    ))}
                  </div>
                </Accordion>
              </div>

              <div id="compatibility">
                <Accordion title="兼容性 / 工作量与结构评估" icon={<CheckCircle2 />} open>
                  <div className="compatibility-box">
                    <div>
                      <p>{(data.synthesis?.workload_evaluation ?? demo.synthesis.workload_evaluation).summary}</p>
                    </div>
                  </div>
                </Accordion>
              </div>

              <div id="score">
                <Accordion title="最终评分" icon={<ExternalLink />} open>
                  <div className="score-panel">
                    <div className="score-row">
                      <span>总分</span>
                      <b>{score.total_score}</b>
                    </div>
                    <div className="score-row">
                      <span>等级</span>
                      <b>{score.grade}</b>
                    </div>
                    <p>{score.overall_evaluation}</p>
                  </div>
                </Accordion>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

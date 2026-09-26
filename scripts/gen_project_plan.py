# -*- coding: utf-8 -*-
"""根据《附件3-项目实现方案模板》生成衡文云审项目实现方案 docx + md。"""
import os
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DOCX = os.path.join(ROOT, "docs", "项目实现方案-衡文云审.docx")
OUT_MD = os.path.join(ROOT, "docs", "项目实现方案-衡文云审.md")
ARCH = os.path.join(ROOT, "docs", "系统架构图.png")
PERF = os.path.join(ROOT, "docs", "性能测试结果表.png")

D = []  # 内容结构
def h1(t): D.append(("h1", t))
def h2(t): D.append(("h2", t))
def p(t): D.append(("p", t))
def bs(items): D.append(("b", items))


# ============================== 内容 ==============================
h1("第一章 项目介绍")
h2("1.1 项目概况")
p("本科毕业设计（论文）评审是高校人才培养的关键环节，其核心文档——18 维预审表（论文格式、论文选题、论文水平、能力技能、论文质量五大类 18 个子项）要求评审教师对每篇论文逐项评定并给出百分制总评与修改建议。实践中该环节存在三大核心难点：一是“量大”，毕业季数百篇论文须在短时间内完成评审，教师工作负荷重；二是“一致性差”，评审标准高度依赖个人经验，不同教师对同一篇论文评分差异显著，且标准随学期漂移；三是“可复现性弱”，评分结论通常缺少可追溯的原文证据支撑，学生难以依建议有效修改。此外，生成式人工智能普及后，论文中机器生成内容比例上升，现有流程缺少面向中文论文的 AIGC 使用检测手段，学术诚信审查难以落地。")
p("针对上述痛点，本作品“衡文云审”提出以“证据导向的多智能体辩论评审”为核心的全过程智能论文评审方案：将教学单位沿用的 18 维预审表与完整评审流程（上传解析→论文分类→章节抽取→评审→修改建议→评分→存档）全部流程化、自动化，并构建四项关键技术能力。（1）多智能体辩论框架：将评审拆分为三个职责明确的“专家”独立初审，由“评审主席”识别争议并定向质询，最终裁决强制绑定原文段落或外部证据；（2）大模型微调：基于 Qwen3-8B 采用 QLoRA 微调“全局质量”评审专家，提升全中文结构规范评审能力；（3）历史评审经验激活：将历年专家评审建议清洗入库，构建“稠密向量＋BM25→RRF 融合→语义重排”的混合检索，使新评审可即时借鉴同类历史评审意见；（4）面向中文论文的 AIGC 检测：在本地 GPU 批量运行开源中文 AIGC 检测模型，输出全文逐窗口色块置信标注，并生成独立检测报告。")
p("系统采用“前端 React ＋ 后端 FastAPI”的一体化 Web 应用形态。教师上传 PDF（兼容扫描件与公式图表），系统经云端结构化解析与本地推理后，自动生成结构化评审报告（章节评价、修改建议、证据链接、18 维预审表）与 AIGC 检测报告，并支持一键导出 Word 与评审表 PDF。以教师人工评审为基准对 114 篇论文进行配对评测：评分等级一致率达 73.7%，平均绝对误差（MAE）5.25 分，|Δ|≤10 分的样本占 88.6%，18 维逐项打分精确一致率 89.9%；单篇论文评审由人工数小时缩短至分钟级并支持批量并发；全部关键组件支持离线一键部署，便于在竞赛与校内环境中复现。")

h1("第二章 项目实施方案")
h2("2.1 数据采集与清洗")
bs([
"论文语料：收集校内历年本科毕业设计论文（经授权用于教学评审）与配套专家评审表，统一去标识化（学号、姓名脱敏）后入库；",
"结构化解析：对接 MinerU 云端结构化引擎（vlm 版本）对 PDF 进行版面分析，输出保留表格、公式、脚注的规范化文本；解析失败自动重试并做分页控制；",
"历史建议库：抽取专家评审意见与修改建议，经去重、去乱码、敏感信息过滤，形成 541 条高质量“历史评审建议”样本库，并按论文类型与问题类型建立索引；",
"评测基准：沉淀 114 篇“教师评审 ↔ 系统输出”配对数据作为准确率评测基准；另构建 AIGC 检测滑窗样本用于检测能力验证。",
])
h2("2.2 理论推导与设计")
p("评审流程采用“独立初审→主席争议识别→定向质询→证据裁决”的辩论式机制：三专家基于同一全文各自独立给出视角意见，主席比较三份意见定位分歧，仅将分歧点定向下发给相关专家交叉质询，最终由主席基于原文证据与应答质量裁决，避免多数投票带来的思路趋同。同时设置“证据边界”硬约束：高严重度结论必须给出原文引用或外部证据，否则强制降低置信度并转人工复核，该约束以 Pydantic 模式校验在管线层强制执行。")
p("历史建议检索采用“稠密向量（BGE-M3）＋关键词（BM25）→倒数排名融合（RRF）→语义重排（Qwen3-Reranker）”的三级检索结构，通过检索阈值控制注入建议的数量与相关性，避免噪声干扰评审结论。")
h2("2.3 关键算法实现")
bs([
"兼容原 Step1-7 评审管线：论文分类、章节抽取、检索建议、逐章评审、修改建议、18 维评分全部保留，并在核心评审环节以多智能体管线增强；",
"三个专家视角：科学严谨性（理论、方法、结论一致性）、实证证据（实验设计、基线、消融、可复现性）、全局质量（结构、工作量、表达）；",
"18 维预审表生成：按五大类 18 细项逐项打分，自动生成总分、等级与修改建议，并经 LaTeX 模板（tectonic 离线引擎）排版导出 PDF 评审表，与校内标准表格逐字一致。",
])
h2("2.4 代码架构")
p("后端采用“领域—端口—服务”分层：领域层定义评审输入输出模式与证据约束，端口层封装外部接口，服务层实现工作流编排、微调专家、历史建议检索与 AIGC 检测；模块按 ingestion（解析）、agents（三专家与主席）、routers（证据检索）、services（任务流）、models（检测模型）、assets（模板）划分。技术栈为 Python/FastAPI＋SQLite＋异步并发，前端为 React18＋Vite（评审报告与检测可视化）。系统总体架构如图 1 所示。")
D.append(("img", ARCH, "图 1 系统总体架构"))
h2("2.5 系统搭建")
bs([
"服务编排：上传→解析→评审→出报告全流程状态机，任务可重试、可断点，Web 端支持批量提交；",
"云边协同：文本生成与外部证据检索借助 SiliconFlow（DeepSeek-V4-Pro）与 OpenAlex 开放学术数据，AIGC 检测采用本地 7×RTX3090 批式推理，实现数据不出校；",
"部署交付：提供一键安装/启动/停止/健康检查脚本，离线资产（AIGC 权重、LaTeX 宏包）随包分发，支持内网一键复现。",
])
h2("2.6 模型训练")
bs([
"训练基座：Qwen3-8B＋QLoRA（4-bit NF4 双量化，r=16，alpha=32，dropout=0.05），LoRA 仅适配自注意力 q/k/v/o 投影；",
"训练策略：3 轮余弦退火（峰值学习率 1e-4，warmup 5%），bf16＋梯度检查点，序列长度 4096，以评估集损失（eval_loss）选最优权重；",
"训练数据为中文结构规范评审样本，输出 8 类问题定位（学术表达、歧义、冗余、逻辑跳跃、章节职责、摘要完整性、术语一致性、证据结论失配）与 4 级严重度（trivial/minor/major/critical）的结构化 JSON；",
"评测：在独立评估集上与未微调基座对比问题召回与格式合规率，微调后格式合规与细粒度问题定位显著提升。",
])

h1("第三章 项目创新性分析")
h2("3.1 理念与方法创新")
p("证据导向的辩论式评审：不同于“一次推理直接出分”，通过多专家独立初审＋主席争议裁决，将评分结论转化为可追溯的证据链，显著抑制“幻觉式批评”；以数据约束而非提示词软约束实现“无证据则降置信或转人工”，保证评审可复现、可追责。")
h2("3.2 应用场景创新")
p("聚焦本科毕设评审这一高强度、强结构化的高频场景，直接复用教学单位现用 18 维预审表格式，输出物即“可用成品”，与既有教学流程零改造衔接；AIGC 检测定位为“人工复核的提示”而非“鉴定结论”，契合学位论文诚信审查的治理边界。")
h2("3.3 技术实现创新")
bs([
"历史评审经验闭环：评审产生的建议经清洗回流建议库，形成“积累—复用—再积累”的自增强闭环；检索采用稠密＋BM25＋RRF＋重排三级融合，在专用场景优于单路向量检索；",
"全开源可复现：以 Apache-2.0 许可的开源模型家族（Qwen3、BGE-M3、Qwen3-Reranker）完成“推理—微调—检索—检测”全链路，关键资产本地化，数据不出校；",
"自动化文档生产：18 维评审表等规范文书由 LaTeX 模板自动排版导出，与校内标准格式逐字一致。",
])

h1("第四章 项目实现成果")
h2("4.1 系统成果（工程能力）")
bs([
"全流程打通：上传 PDF→结构化解析（扫描件、公式、表格）→多智能体评审→证据链报告→18 维预审表 PDF→AIGC 检测报告，端到端可用；",
"评测资产齐备：541 条历史建议库、114 篇配对评测集、QLoRA 微调适配器（约 60MB）可随作品复现评测；",
"一键部署：完整的安装/启动/停止/健康检查脚本与离线权重（AIGC 391MB＋LaTeX 宏包）打包分发，部署包≤600MB。",
])
h2("4.2 质量成果（与教师人工基准配对评测，114 篇）")
bs([
"评分等级一致率 73.7%；MAE=5.25 分，|Δ|≤5 分样本占 75.4%、|Δ|≤10 分样本占 88.6%；Pearson 相关系数 0.828；AI 评分整体平均偏高约 5 分，已在报告中提示委员会校准；",
"18 维预审表逐项精确一致率 89.9%，其中“参考文献著录格式”一致率最低（48%），已定位为该维度打分规则优化重点；",
"评分偏差与 18 维一致率逐篇分布见附件图（另附偏差折线与逐维一致率图）。",
])
D.append(("img", PERF, "图 2 性能测试结果表"))
h2("4.3 性能成果")
p("单篇论文端到端评审（含结构化解析与多智能体推理）达到分钟级，支持批量并发提交；AIGC 检测采用本地 GPU 批式推理、整体吞吐显著高于逐篇在线处理；相关吞吐与时延数据见性能测试结果表（图 2）。")

h1("第五章 项目合规风险分析")
h2("5.1 数据合规风险")
p("论文与评审数据属教育教学数据并涉及个人信息。应对措施：数据仅存于校内自有服务器，展示层对学号、姓名脱敏；AIGC 检测仅存储内容 SHA-256 摘要，不保存明文比对原文；云端解析选择境内合规服务商（MinerU、SiliconFlow），配置中约定数据用途并最小化传输范围。")
h2("5.2 使用合规与伦理性风险")
p("AIGC 检测阈值（0.5/0.8）尚未按本校论文语料校准，存在误报可能，若被误用作纪律处分依据将引发争议。应对措施：在检测报告显著位置声明“结果仅供教师参考，不作为学术不端判定依据”，并依《学位法》及校内细则由专家人工复核后生效。")
h2("5.3 知识产权风险")
p("系统集成开源组件（Qwen3、BGE、Qwen3-Reranker、LoRA/QLoRA、Chroma、React 等），各组件许可证在教育教学与交付使用场景下均允许；评审表 LaTeX 模板源自校内规范文件，不涉及对外授权。应对措施：随包附组件清单与许可证说明，预留合规审查。")
h2("5.4 业务与运营风险")
p("评审主链路依赖第三方付费云 API（文本生成、PDF 结构化解析），存在余额耗尽、限流与服务波动风险。应对措施：内置自动重试与失败重试机制、离线降级路径（AIGC 本地化、LaTeX 离线），并提供余额告警与故障排查文档。")

h1("第六章 参考文献")
bs([
"[1] Qwen Team. Qwen3 Technical Report[R]. arXiv:2505.09388, 2025.",
"[2] Hu E, Shen Y, Wallis P, et al. LoRA: Low-Rank Adaptation of Large Language Models[C]. ICLR, 2022. arXiv:2106.09685.",
"[3] Dettmers T, Pagnoni A, Holtzman A, et al. QLoRA: Efficient Finetuning of Quantized LLMs[C]. NeurIPS, 2023. arXiv:2305.14314.",
"[4] Chen J, Xiao S, Zhang P, et al. BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings[EB/OL]. arXiv:2402.03216, 2024.",
"[5] Robertson S E, Zaragoza H. The Probabilistic Relevance Framework: BM25 and Beyond[J]. Foundations and Trends in Information Retrieval, 2009, 3(4): 333-389.",
"[6] Cormack G V, Clarke C L A, Buettcher S. Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods[C]. SIGIR, 2009.",
"[7] Lewis P, Perez E, Piktus A, et al. Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks[C]. NeurIPS, 2020. arXiv:2005.11401.",
"[8] Irving G, Christiano P, Amodei D. AI Safety via Debate[EB/OL]. arXiv:1805.00899, 2018.",
"[9] Tian Y, Chen H, Wang X, et al. Multiscale Positive-Unlabeled Detection of AI-Generated Texts[C]. ICLR, 2024. arXiv:2305.18149.",
"[10] yuchuantian. AIGC_detector_zhv3: 面向最新大模型的中文 AI 写作内容检测模型（v3）[EB/OL]. Hugging Face, 2025. https://huggingface.co/yuchuantian/AIGC_detector_zhv3.",
"[11] SiliconFlow. 硅基流动开放平台 API 文档[EB/OL]. https://siliconflow.cn, 2025.",
"[12] MinerU. 高精度文档结构化解析引擎[EB/OL]. https://mineru.net, 2025.",
"[13] Chroma. Chroma: AI 原生嵌入式向量数据库[EB/OL]. https://github.com/chroma-core/chroma, 2023.",
])


# ============================== 生成 docx ==============================
def set_run(run, size=12, bold=False, font="仿宋"):
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.font.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), "Times New Roman")
    rFonts.set(qn("w:hAnsi"), "Times New Roman")
    rFonts.set(qn("w:eastAsia"), font)


def para(doc, text, size=12, bold=False, align=None, indent=True, font="仿宋"):
    pr = doc.add_paragraph()
    pf = pr.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(3)
    if indent:
        pf.first_line_indent = Pt(size * 2)
    if align:
        pr.alignment = align
    r = pr.add_run(text)
    set_run(r, size=size, bold=bold, font=font)
    return pr


def add_toc(doc):
    pr = doc.add_paragraph()
    run = pr.add_run()
    fle = OxmlElement("w:fldSimple")
    fle.set(qn("w:instr"), 'TOC \\o "1-2" \\h \\z \\u')
    tt = OxmlElement("w:t")
    tt.text = "（请在 Word 中右键“更新域”生成目录）"
    r2 = OxmlElement("w:r")
    r2.append(OxmlElement("w:t"))
    r2.find(qn("w:t")).text = "（请在 Word 中右键“更新域”生成目录）"
    fle.append(r2)
    pr._p.append(fle)
    return pr


doc = Document()
sec = doc.sections[0]
sec.left_margin = sec.right_margin = Cm(2.5)
sec.top_margin = sec.bottom_margin = Cm(2.5)

# 封面
para(doc, "", indent=False)
para(doc, "衡文云审项目实现方案", size=24, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False)
para(doc, "", indent=False)
para(doc, "项目名称：衡文云审——面向本科毕业设计的全过程智能论文评审系统", size=16, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False)
para(doc, "团队名称：____________________", size=16, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False)
doc.add_page_break()

para(doc, "目  录", size=16, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False)
add_toc(doc)
doc.add_page_break()

for kind, *rest in D:
    if kind == "h1":
        para(doc, rest[0], size=16, bold=True, indent=False)
    elif kind == "h2":
        para(doc, rest[0], size=14, bold=True, indent=False)
    elif kind == "p":
        para(doc, rest[0], size=12)
    elif kind == "b":
        for t in rest[0]:
            para(doc, "• " + t, size=12)
    elif kind == "img":
        para(doc, "", indent=False)
        pr = doc.add_paragraph()
        pr.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if os.path.exists(rest[0]):
            pr.add_run().add_picture(rest[0], width=Cm(14.0))
        para(doc, rest[1], size=10.5, align=WD_ALIGN_PARAGRAPH.CENTER, indent=False)

doc.save(OUT_DOCX)
print("saved:", OUT_DOCX)

# ============================== 生成 md ==============================
md = ["# 衡文云审项目实现方案", "",
      "> 项目名称：衡文云审——面向本科毕业设计的全过程智能论文评审系统",
      "> 团队名称：____________", "", "## 目录", ""]
chapters = ["第一章 项目介绍", "第二章 项目实施方案", "第三章 项目创新性分析",
            "第四章 项目实现成果", "第五章 项目合规风险分析", "第六章 参考文献"]
for c in chapters:
    md.append("- " + c)
md += ["", "---"]
for kind, *rest in D:
    if kind == "h1":
        md += ["", f"## {rest[0]}", ""]
    elif kind == "h2":
        md += [f"### {rest[0]}", ""]
    elif kind == "p":
        md += [rest[0], ""]
    elif kind == "b":
        for t in rest[0]:
            md.append(f"- {t}")
            md.append("")
    elif kind == "img":
        md += [f"*{rest[1]}（见附件插图）*", ""]
with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write("\n".join(md))
print("saved:", OUT_MD)
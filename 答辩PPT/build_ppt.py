#!/usr/bin/env python
"""生成答辩 PPT（学术风：白底 + 学术宝蓝），python-pptx 手写版式。

排版约束：13.333x7.5 英寸（16:9），页边距 0.75 英寸，正文 ≥ 14pt，标题 24-28pt。
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "答辩PPT" / "校园服务智能问答系统-答辩终版.pptx"
ASSETS = BASE / "答辩PPT" / "assets"

BLUE = RGBColor(0x1E, 0x4F, 0xA8)
BLUE2 = RGBColor(0x3D, 0x7B, 0xD9)
BLUE_LIGHT = RGBColor(0xE8, 0xEF, 0xF8)
INK = RGBColor(0x1A, 0x22, 0x30)
GRAY = RGBColor(0x4A, 0x55, 0x68)
MUTED = RGBColor(0x8B, 0x97, 0xA8)
LINE = RGBColor(0xD6, 0xDC, 0xE5)
RED = RGBColor(0xD9, 0x53, 0x4F)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "Microsoft YaHei"

W, H = 13.333, 7.5
MARGIN = 0.75

prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
BLANK = prs.slide_layouts[6]


def tx(slide, x, y, w, h, text, size=16, color=INK, bold=False, align=PP_ALIGN.LEFT, line=1.25, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    lines = str(text).split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line
        run = p.add_run()
        run.text = ln
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
    return box


def rect(slide, x, y, w, h, fill=None, line_color=None, line_w=1.0, radius=False):
    from pptx.enum.shapes import MSO_SHAPE
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    if line_color is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line_color
        shape.line.width = Pt(line_w)
    shape.shadow.inherit = False
    if radius:
        shape.adjustments[0] = 0.08
    return shape


def new_slide(kicker: str, title: str, page: int, subtitle: str = ""):
    slide = prs.slides.add_slide(BLANK)
    rect(slide, 0, 0, W, 0.07, fill=BLUE)
    tx(slide, MARGIN, 0.42, 10.5, 0.3, kicker.upper(), 11, BLUE2, True)
    tx(slide, MARGIN, 0.72, 11.8, 0.55, title, 27, INK, True, line=1.1)
    if subtitle:
        tx(slide, MARGIN, 1.32, 11.8, 0.35, subtitle, 13, MUTED, line=1.3)
        line_y = 1.78
    else:
        line_y = 1.42
    rect(slide, MARGIN, line_y, W - MARGIN * 2, 0.014, fill=LINE)
    tx(slide, W - 1.0, H - 0.5, 0.5, 0.25, f"{page:02d}", 10, MUTED, align=PP_ALIGN.RIGHT)
    tx(slide, MARGIN, H - 0.5, 8, 0.25, "校园服务智能问答系统的设计与实现", 10, MUTED)
    return slide, line_y + 0.28


def bullets(slide, x, y, w, items, size=14, gap=0.44, dot_color=BLUE):
    cy = y
    for text in items:
        rect(slide, x, cy + 0.07, 0.07, 0.07, fill=dot_color)
        tx(slide, x + 0.22, cy, w - 0.22, gap - 0.2, text, size, GRAY, line=1.35)
        cy += gap
    return cy


def card(slide, x, y, w, h, head, body, accent=BLUE, body_size=13):
    rect(slide, x, y, w, h, fill=WHITE, line_color=LINE, line_w=0.75, radius=True)
    rect(slide, x, y, 0.05, h, fill=accent)
    tx(slide, x + 0.28, y + 0.22, w - 0.5, 0.3, head, 15, INK, True)
    tx(slide, x + 0.28, y + 0.72, w - 0.5, h - 0.9, body, body_size, GRAY, line=1.4)


def add_table(slide, x, y, w, rows, col_widths, header=True, size=12, row_h=0.38):
    n_rows, n_cols = len(rows), len(rows[0])
    shape = slide.shapes.add_table(n_rows, n_cols, Inches(x), Inches(y), Inches(w), Inches(row_h * n_rows))
    table = shape.table
    for i, cw in enumerate(col_widths):
        table.columns[i].width = Inches(cw)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.text = ""
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.06)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            p = cell.text_frame.paragraphs[0]
            p.line_spacing = 1.15
            run = p.add_run()
            run.text = str(val)
            run.font.name = FONT
            run.font.size = Pt(size)
            run.font.bold = header and r == 0
            run.font.color.rgb = WHITE if (header and r == 0) else INK
            cell.fill.solid()
            cell.fill.fore_color.rgb = BLUE if (header and r == 0) else WHITE
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    return table


# ---------------------------------------------------------------- 01 封面
s = prs.slides.add_slide(BLANK)
rect(s, 0, 0, W, 0.1, fill=BLUE)
rect(s, 0, 0, 0.14, H, fill=BLUE)
tx(s, 1.35, 1.45, 10, 0.35, "课程设计答辩 · 人机交互软件工程", 14, BLUE2, True)
tx(s, 1.35, 2.0, 10.6, 1.9, "校园服务智能问答系统的\n设计与实现", 40, INK, True, line=1.28)
rect(s, 1.35, 4.02, 0.95, 0.05, fill=BLUE)
tx(s, 1.35, 4.25, 10, 0.4, "RAG 检索增强生成 · 多智能体协作 · 校园事务闭环", 16, GRAY)
infos = [("答辩人", "（待填写）"), ("学号", "（待填写）"), ("指导教师", "（待填写）"), ("学院", "人工智能学院")]
for i, (k, v) in enumerate(infos):
    x = 1.35 + i * 2.75
    tx(s, x, 5.3, 2.4, 0.25, k, 11, MUTED)
    tx(s, x, 5.6, 2.4, 0.3, v, 14, INK)
tx(s, 1.35, 6.45, 4, 0.3, "2026 年 11 月", 12, MUTED)

# ---------------------------------------------------------------- 02 目录
s, y = new_slide("Contents", "本次汇报的内容", 2)
items = [
    ("01", "选题背景与调研", "问题从哪里来，用户真正需要什么"),
    ("02", "需求与竞品分析", "优先级怎么排，与现有产品的差异"),
    ("03", "系统设计与实现", "架构、Agent 工作流、RAG 与工具调用"),
    ("04", "校园服务模块", "报修、预约、失物、文档四个闭环"),
    ("05", "测试与评测", "160 项用例与有/无工具消融实验"),
    ("06", "总结与展望", "解决的问题、不足与后续方向"),
]
for i, (no, title, desc) in enumerate(items):
    col, row = i % 2, i // 2
    x = MARGIN + col * 6.0
    yy = y + row * 1.42
    tx(s, x, yy, 0.8, 0.5, no, 22, BLUE_LIGHT if False else BLUE2, True)
    tx(s, x + 0.85, yy - 0.02, 4.9, 0.35, title, 16, INK, True)
    tx(s, x + 0.85, yy + 0.38, 4.9, 0.35, desc, 12, MUTED)

# ---------------------------------------------------------------- 03 背景
s, y = new_slide("01 选题背景", "三类信息割裂，学生查不到也办不成", 3)
data = [("87%", "一学期内至少一次\n找不到具体条款"), ("61%", "遇到制度问题\n先问同学而非查官网"), ("92%", "愿意用一句话提问\n但要求指出依据")]
for i, (num, desc) in enumerate(data):
    x = MARGIN + i * 4.0
    tx(s, x, y, 1.8, 0.6, num, 34, BLUE, True)
    tx(s, x + 1.45, y + 0.1, 2.3, 0.7, desc, 12, GRAY, line=1.35)
problems = [
    ("制度文本难检索", "21 篇制度、504 条条款，关键词检索定位不到具体条款，只能逐篇翻"),
    ("时效信息无生命周期", "停水停电通知有效期可能只有几小时，与长期制度混排易误判"),
    ("事务办理无进度", "报修靠反复问宿管，预约要去现场，失物信息散落在群聊"),
]
for i, (head, body) in enumerate(problems):
    card(s, MARGIN + i * 4.0, y + 1.35, 3.75, 1.75, head, body)

# ---------------------------------------------------------------- 04 调研
s, y = new_slide("01 调研", "三种方法交叉验证，结论直接决定优先级", 4)
tx(s, MARGIN, y, 5.4, 0.3, "调研方法", 15, INK, True)
methods = [
    ("问卷调查", "118 份有效样本", "高频问题类型、渠道、满意度"),
    ("半结构化访谈", "学生 8 人 + 教师 3 人", "具体场景、失败案例、接受度"),
    ("竞品体验", "5 款产品 × 3 个任务", "功能覆盖、路径长度、可信度"),
]
yy = y + 0.5
for name, sample, focus in methods:
    rect(s, MARGIN, yy, 5.4, 1.05, fill=BLUE_LIGHT, radius=True)
    tx(s, MARGIN + 0.25, yy + 0.14, 2.2, 0.3, name, 14, BLUE, True)
    tx(s, MARGIN + 0.25, yy + 0.52, 2.3, 0.3, sample, 11, GRAY)
    tx(s, MARGIN + 2.7, yy + 0.32, 2.5, 0.5, focus, 11, GRAY, line=1.3)
    yy += 1.22
tx(s, 6.6, y, 5.6, 0.3, "四条结论 → 四条设计决策", 15, INK, True)
concl = [
    "制度问答最高频，且必须可溯源 → 条款级引用 + 校验",
    "过期信息比没有信息更糟 → 公告生效/失效自动过滤",
    "事务痛点在于进度不可见 → 状态流转 + 我的记录",
    "担心 AI 误操作 → 写操作只生成草稿，用户确认",
]
bullets(s, 6.6, y + 0.55, 5.8, concl, 13, 0.86)

# ---------------------------------------------------------------- 05 竞品
s, y = new_slide("02 竞品分析", "单点成熟，缺少“可溯源 + 可办理”的组合", 5, "选择学生实际会接触的 5 款产品，从四个维度对比")
rows = [
    ["产品 / 维度", "制度问答", "引用溯源", "事务办理", "时效控制"],
    ["今日校园", "弱", "无", "强", "中"],
    ["超星学习通", "弱", "无", "中", "弱"],
    ["官网 + 公众号", "仅全文检索", "需人工定位", "无", "人工维护"],
    ["通用大模型", "强但不稳定", "无或伪造", "无", "无"],
    ["本系统", "强（限定语料）", "条款级 + 校验", "四类事务", "自动过滤"],
]
add_table(s, MARGIN, y, 11.8, rows, [2.6, 2.4, 2.4, 2.2, 2.2], size=12, row_h=0.52)
tx(s, MARGIN, y + 3.6, 11.8, 0.9,
   "差异化定位：底层五个业务模块提供真实数据，上层 Agent 作为统一入口；\n两条路径读写同一套数据，不会出现“页面看不到、问答却答得出来”。",
   13, GRAY, line=1.5)

# ---------------------------------------------------------------- 06 需求
s, y = new_slide("02 需求分析", "按“影响 × 可行性”排出三个优先级", 6)
rows = [
    ["优先级", "需求", "验收口径"],
    ["P0", "制度问答与引用溯源", "拒答准确率、引用通过率可测"],
    ["P0", "公告时效管理", "过期公告不出现在列表与问答"],
    ["P0", "登录鉴权与权限隔离", "401 / 403 / 404 用例覆盖"],
    ["P1", "文档上传 / 预览 / 去重", "上传成功率、新增条例可追溯"],
    ["P1", "报修、预约、失物闭环", "状态机、限流、并发可测"],
    ["P2", "对话式预约与确认", "草稿阶段零写入"],
]
add_table(s, MARGIN, y, 8.0, rows, [1.0, 3.3, 3.7], size=12, row_h=0.56)
tx(s, 9.2, y, 3.4, 0.3, "非功能指标", 15, INK, True)
metrics = [("首字延迟", "≤ 3 s"), ("recall@3", "0.750"), ("自动化测试", "160 项"), ("并发预约", "仅 1 人成功")]
for i, (k, v) in enumerate(metrics):
    yy = y + 0.5 + i * 1.0
    tx(s, 9.2, yy, 1.9, 0.3, k, 12, MUTED)
    tx(s, 9.2, yy + 0.3, 3.4, 0.35, v, 16, BLUE, True)

# ---------------------------------------------------------------- 07 架构
s, y = new_slide("03 系统设计", "四层架构：接入、编排、能力、存储", 7)
layers = [
    ("接入层", "FastAPI · REST / SSE · 统一鉴权与限流", BLUE),
    ("编排层", "Agent 图：意图 → 记忆 → 检索/工具 → 生成 → 校验", BLUE2),
    ("能力层", "向量检索 · 图谱检索 · 五个业务服务 · 文档入库", RGBColor(0x5B, 0x93, 0xD9)),
    ("存储层", "MySQL（业务） · Qdrant（向量） · Neo4j（图谱）", RGBColor(0x7A, 0xA9, 0xE0)),
]
for i, (name, desc, color) in enumerate(layers):
    yy = y + i * 1.15
    rect(s, MARGIN, yy, 7.6, 0.92, fill=color, radius=True)
    tx(s, MARGIN + 0.3, yy + 0.26, 1.6, 0.35, name, 15, WHITE, True)
    tx(s, MARGIN + 1.95, yy + 0.28, 5.4, 0.35, desc, 12, WHITE)
tx(s, 8.9, y, 3.7, 0.3, "分层带来的收益", 15, INK, True)
bullets(s, 8.9, y + 0.55, 3.7, [
    "向量库切换只改配置",
    "业务模块新增不改 Agent 图",
    "每个模块双实现（MySQL / 内存）",
    "无数据库环境也能跑测试",
], 12, 0.9)

# ---------------------------------------------------------------- 08 Agent 工作流
s, y = new_slide("03 系统设计", "Agent 工作流：每一步都可降级", 8)
nodes = ["缓存查询", "意图理解", "多轮记忆", "检索 / 工具", "充分性判定", "生成", "引用校验"]
for i, name in enumerate(nodes):
    x = MARGIN + i * 1.72
    rect(s, x, y + 0.2, 1.5, 0.86, fill=BLUE_LIGHT if i not in (3, 6) else BLUE, radius=True)
    tx(s, x + 0.08, y + 0.5, 1.34, 0.3, name, 10, INK if i not in (3, 6) else WHITE, True, PP_ALIGN.CENTER)
tx(s, MARGIN, y + 1.35, 11.8, 0.4, "实心节点为分支点：检索可走向量/图谱/双路；校验不通过触发一次重生成，仍不通过则拒答", 12, MUTED)
rect(s, MARGIN, y + 2.0, 5.7, 1.5, fill=WHITE, line_color=LINE, radius=True)
tx(s, MARGIN + 0.25, y + 2.2, 5.2, 0.3, "三条降级路径", 14, INK, True)
bullets(s, MARGIN + 0.25, y + 2.62, 5.2, ["工具异常 → 回退标准 RAG", "校验失败 → 重生成一次", "LLM 超时 → 返回可解释错误"], 11, 0.4)
rect(s, 6.7, y + 2.0, 5.9, 1.5, fill=BLUE_LIGHT, radius=True)
tx(s, 6.95, y + 2.2, 5.4, 0.3, "可观测性", 14, BLUE, True)
tx(s, 6.95, y + 2.62, 5.4, 0.7, "每次问答回传 runtime.visited 与 tool_calls，前端轨迹面板可见每个节点耗时", 11, GRAY, line=1.35)

# ---------------------------------------------------------------- 09 RAG
s, y = new_slide("03 系统设计", "检索与生成：先约束范围，再要求依据", 9)
tx(s, MARGIN, y, 5.6, 0.3, "检索链路", 15, INK, True)
bullets(s, MARGIN, y + 0.5, 5.6, [
    "504 条条款同时入向量库与图谱",
    "向量负责语义相近，图谱负责条款关系",
    "RRF 融合两路结果，保留原文片段",
    "生成后逐条校验条款号是否真实存在",
], 13, 0.75)
for i, (k, v) in enumerate([("recall@3", "0.750"), ("MRR", "0.442"), ("拒答准确率", "可测可控")]):
    x = 6.6 + i * 2.1
    rect(s, x, y, 1.95, 1.15, fill=WHITE, line_color=LINE, radius=True)
    tx(s, x + 0.15, y + 0.18, 1.65, 0.25, k, 10, MUTED)
    tx(s, x + 0.15, y + 0.55, 1.65, 0.4, v, 18, BLUE, True)
tx(s, 6.6, y + 1.5, 6.1, 0.3, "为什么坚持拒答", 15, INK, True)
tx(s, 6.6, y + 1.95, 6.1, 1.6,
   "调研中 74% 的学生要求“答案要能指出依据”。与其给出一个看似合理但没有出处的答案，\n不如明确说明“资料中未提及”，并给出下一步建议。\n可控性来自范围约束 + 校验 + 拒答，而不是换更大的模型。",
   12, GRAY, line=1.5)
rect(s, MARGIN, y + 2.55, 5.6, 1.0, fill=BLUE_LIGHT, radius=True)
tx(s, MARGIN + 0.25, y + 2.75, 5.2, 0.6, "抽检问题：近三成回答曾引用不存在的条款号\n→ 增加校验与重生成后错误率显著下降", 11, BLUE, line=1.4)

# ---------------------------------------------------------------- 10 工具调用
s, y = new_slide("03 系统设计", "工具调用：能查、能办，但写操作必须确认", 10)
tools = ["校规检索", "公告查询", "工单创建", "报修进度", "我的预约", "场馆余量", "失物检索", "预约草稿"]
for i, name in enumerate(tools):
    col, row = i % 4, i // 4
    x = MARGIN + col * 1.75
    yy = y + row * 0.78
    is_draft = name == "预约草稿"
    rect(s, x, yy, 1.6, 0.6, fill=BLUE if is_draft else BLUE_LIGHT, radius=True)
    tx(s, x + 0.05, yy + 0.17, 1.5, 0.28, name, 10, WHITE if is_draft else INK, True, PP_ALIGN.CENTER)
tx(s, MARGIN, y + 1.62, 5.8, 0.3, "写操作的安全边界", 15, INK, True)
bullets(s, MARGIN, y + 2.1, 5.8, [
    "模型只能生成 requires_confirmation 草稿",
    "前端按 action 渲染“确认预约 / 确认提交工单”",
    "未点击确认前不占用座位、不写入工单库",
], 12, 0.66)
rect(s, 6.8, y + 1.55, 5.8, 2.1, fill=WHITE, line_color=LINE, radius=True)
tx(s, 7.05, y + 1.75, 5.3, 0.3, "一个真实问题：查询被误判为建单", 13, RED, True)
tx(s, 7.05, y + 2.2, 5.3, 1.25,
   "“我的报修到哪了”曾命中建单关键词，模型可能去创建工单。\n修复：从关键词表移除“报修 / 失物”等名词，只保留动作短语；\n并在提示词中明确“查询进度用 query_repair_status”。",
   11, GRAY, line=1.45)

# ---------------------------------------------------------------- 11 校园服务
s, y = new_slide("04 校园服务", "四个模块，每个都有独立的规则与边界", 11)
cards = [
    ("寝室报修", "pending → processing → completed\n每日 3 条限流 + 同房间同类 24h 判重", BLUE),
    ("图书馆预约", "仅约明天起 7 天；行锁 + 唯一键防超卖\n冲突返回 409 并提示换时段", BLUE2),
    ("失物招领", "先审后发：待审 → 通过/驳回 → 已解决\n图片限 jpg/png/webp ≤5MB", RGBColor(0x5B, 0x93, 0xD9)),
    ("文档管理", "上传即解析入库；条款级去重\n新增条例逐条回报管理员", RGBColor(0x7A, 0xA9, 0xE0)),
]
for i, (name, body, color) in enumerate(cards):
    col, row = i % 2, i // 2
    x = MARGIN + col * 6.0
    yy = y + row * 2.05
    card(s, x, yy, 5.75, 1.8, name, body, color, body_size=11)

# ---------------------------------------------------------------- 12 界面
s, y = new_slide("04 校园服务", "界面：状态可见、操作可逆、危险可确认", 12)
pairs = [
    ("assets/01-banner-visible.png", "紧急公告横幅：可关闭、可在公告页恢复"),
    ("assets/11-services-reserve.png", "预约界面：已约满时段置灰，我的预约可取消"),
]
for i, (img, cap) in enumerate(pairs):
    x = MARGIN + i * 6.05
    path = ASSETS / img.split("/")[-1]
    if path.exists():
        s.shapes.add_picture(str(path), Inches(x), Inches(y), width=Inches(5.85))
    rect(s, x, y + 3.42, 5.85, 0.02, fill=LINE)
    tx(s, x, y + 3.55, 5.85, 0.3, cap, 10, MUTED)
tx(s, MARGIN, y + 3.95, 11.8, 0.3, "另：文档上传后弹出“本次新增条例”清单，逐条列出新增与跳过重复的数量", 10, MUTED)

# ---------------------------------------------------------------- 13 并发安全
s, y = new_slide("04 技术要点", "并发与权限：靠机制，不靠约定", 13)
tx(s, MARGIN, y, 5.7, 0.3, "预约防超卖：两道防线", 15, INK, True)
steps = ["事务内 SELECT ... FOR UPDATE 锁位置行", "锁内检查目标时段是否已占用", "UNIQUE(location, date, slot) 兜底", "冲突统一捕获为 409"]
for i, text in enumerate(steps):
    yy = y + 0.55 + i * 0.72
    rect(s, MARGIN, yy, 0.34, 0.34, fill=BLUE, radius=True)
    tx(s, MARGIN + 0.07, yy + 0.06, 0.3, 0.25, str(i + 1), 11, WHITE, True, PP_ALIGN.CENTER)
    tx(s, MARGIN + 0.5, yy + 0.02, 5.2, 0.3, text, 12, GRAY)
tx(s, MARGIN, y + 3.5, 5.7, 0.3, "实测：8 线程并发抢同一时段，恰好 1 人成功", 11, BLUE, True)
tx(s, 6.7, y, 5.8, 0.3, "权限控制：三层校验", 15, INK, True)
rows = [
    ["层", "做法"],
    ["接口层", "角色判定，学生越权 403"],
    ["服务层", "属主二次校验，他人资源 404"],
    ["数据层", "查询强制带 user_id，不开管理口"],
]
add_table(s, 6.7, y + 0.55, 5.8, rows, [1.4, 4.4], size=11, row_h=0.62)
tx(s, 6.7, y + 3.3, 5.8, 0.8, "原则：工具入参只有 user_id，没有角色参数——\n越权一旦开口就收不住。", 11, GRAY, line=1.4)

# ---------------------------------------------------------------- 14 测试
s, y = new_slide("05 测试与评测", "160 项用例 + 一组干净的消融实验", 14)
tx(s, MARGIN, y, 5.8, 0.3, "测试构成", 15, INK, True)
rows = [
    ["层级", "覆盖", "规模"],
    ["服务层", "限流 / 判重 / 状态机 / 并发", "核心业务规则"],
    ["接口层", "权限 / 错误码映射", "401 / 403 / 404 / 409"],
    ["端到端", "登录 → 提交 → 确认", "Playwright"],
]
add_table(s, MARGIN, y + 0.55, 5.6, rows, [1.2, 3.0, 1.6], size=11, row_h=0.62)
tx(s, MARGIN, y + 3.05, 5.6, 0.3, "全量 160 项通过；关键路径含并发与越权", 11, BLUE, True)
tx(s, 6.6, y, 5.9, 0.3, "工具消融（8 题 × 开 / 关）", 15, INK, True)
rows2 = [
    ["配置", "工具选对率", "RAG 对照"],
    ["开启校园工具", "1.000", "1.000"],
    ["关闭校园工具", "0.125", "1.000"],
]
add_table(s, 6.6, y + 0.55, 5.9, rows2, [2.9, 1.7, 1.3], size=11, row_h=0.66)
tx(s, 6.6, y + 2.85, 5.9, 1.1,
   "关闭组 0.125 仅来自 RAG 对照题：7 道实时业务题在无工具时全部拿不到数据。\n两组对照题均为 1.000，说明工具接入没有抢走原有检索调用。",
   11, GRAY, line=1.45)

# ---------------------------------------------------------------- 15 总结
s, y = new_slide("06 总结", "做成了什么，还差什么", 15)
tx(s, MARGIN, y, 3.7, 0.3, "三个收获", 15, INK, True)
bullets(s, MARGIN, y + 0.5, 3.8, [
    "可控性来自约束与校验，不是更大的模型",
    "交互设计决定可信度：来源卡片 + 确认按钮",
    "用消融实验验证设计，而不是凭直觉",
], 11, 1.05)
tx(s, 4.95, y, 3.7, 0.3, "三个不足", 15, INK, True)
bullets(s, 4.95, y + 0.5, 3.7, [
    "评测题集仅 8 题，统计意义有限",
    "图谱只做关系召回，未支持多跳",
    "未做真实高并发压测",
], 11, 1.05, RED)
tx(s, 9.0, y, 3.4, 0.3, "下一步", 15, INK, True)
bullets(s, 9.0, y + 0.5, 3.4, [
    "扩充金标集到 50 题",
    "图片识别与报修图片",
    "站内消息与违约限制",
], 11, 1.05, BLUE2)
rect(s, MARGIN, y + 3.55, 11.8, 0.02, fill=LINE)
tx(s, MARGIN, y + 3.75, 11.8, 0.5, "谢谢各位老师，请批评指正", 18, BLUE, True, PP_ALIGN.CENTER)
tx(s, MARGIN, y + 4.25, 11.8, 0.3, "Q & A", 12, MUTED, align=PP_ALIGN.CENTER)

OUT.parent.mkdir(parents=True, exist_ok=True)
prs.save(str(OUT))
print(f"生成: {OUT} ({OUT.stat().st_size} bytes, {len(prs.slides.__iter__.__self__._sldIdLst)} 页)")

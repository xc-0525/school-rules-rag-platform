"""同步课程设计实验报告：容量制预约、登录页改版、测试规模、HTML 实体修复。

只改文字，不动样式；重复运行不会重复插入段落。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph

DOC = Path(
    "output/report-20261105-143403/stage3/校园服务智能问答系统的设计与实现-课程设计实验报告.docx"
)

REPLACEMENTS = [
    # 摘要里的 HTML 实体残留 + 测试规模
    ("构建&quot;意图理解", "构建“意图理解"),
    ("引用校验&quot;的可控问答链路", "引用校验”的可控问答链路"),
    ("实现&quot;问得到&quot;与&quot;办得成&quot;", "实现“问得到”与“办得成”"),
    ("测试层面建立 160 项自动化用例", "测试层面建立 188 项自动化用例"),
    # 5.3 登录页
    (
        "登录页仅保留账号、密码与一句系统定位说明",
        "登录页采用左右分栏：左侧以校园航拍、校徽与校训建立校本语境，右侧只保留账号、密码与一句系统定位说明",
    ),
    # 5.3 预约界面：容量制
    (
        "已被预约的时段置灰并标注",
        "每个时段实时标注剩余名额（如“余 40/40”），满员时段置灰并标注",
    ),
    # 6.1 工作量
    ("自动化测试 160 项", "自动化测试 188 项"),
    # 7.5 并发控制：移除唯一索引兜底
    (
        "在锁内检查目标时段是否已被占用；数据库层建立 (location_id, res_date, slot) 唯一索引作为最终兜底。",
        "在锁内统计该时段已占名额，未达容量上限才写入；同一学生同一位置同一时段只保留一条有效预约。",
    ),
    ("该时段刚被别人预约", "该时段已约满"),
    # 8.1 测试策略
    ("当前共 160 项自动化用例，全部通过。", "当前共 188 项自动化用例，187 项通过、1 项依赖外部引擎按环境跳过。"),
    # 引号统一为中文引号
    ('标注"已约满"', "标注“已约满”"),
    ('下方"我的预约"', "下方“我的预约”"),
    ('前端提示"该时段已约满"', "前端提示“该时段已约满”"),
    # 表格：需求表与指标表
    ("仅约明天起 7 天，防并发超卖", "仅约明天起 7 天，按时段容量占位"),
    ("并发抢位只成功一单", "容量内并发占位不超卖"),
    ("160 项，全部通过", "188 项，187 通过"),
    ("同一时段仅一人成功", "容量内不超卖"),
    ("8 线程并发恰好 1 成功", "8 线程抢容量 1 的研讨间恰好 1 成功"),
]

NEW_PARAGRAPHS = [
    "会话历史检索。左栏支持按标题搜索，结果按更新时间倒序，每页 10 条、点“加载更多”继续；搜索与翻页共用请求序号，快速改词时旧响应会被丢弃，不会出现结果错位。",
    "生成过程可控。回答流式输出期间输入框不锁定，学生可以先写下一个问题；发送键此时变为“停止”，点击即中止生成并把气泡标记为“已取消生成”，避免长回答把用户困在等待中。",
]


def set_paragraph_text(paragraph: Paragraph, text: str) -> None:
    """保留首个 run 的格式，把整段文字替换掉。"""
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for run in runs[1:]:
        run.text = ""


def replace_everywhere(doc: Document) -> int:
    changed = 0
    targets: list[Paragraph] = list(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                targets.extend(cell.paragraphs)

    for paragraph in targets:
        text = paragraph.text
        if not text:
            continue
        new_text = text
        for old, new in REPLACEMENTS:
            if old in new_text:
                new_text = new_text.replace(old, new)
        if new_text != text:
            set_paragraph_text(paragraph, new_text)
            changed += 1
    return changed


def main() -> None:
    doc = Document(str(DOC))
    changed = replace_everywhere(doc)

    exists = any("会话历史检索。" in p.text for p in doc.paragraphs)
    if not exists:
        anchor = next(
            (p for p in doc.paragraphs if p.text.startswith("对话式办理是本系统的交互亮点")),
            None,
        )
        if anchor is not None:
            for text in NEW_PARAGRAPHS:
                new_el = deepcopy(anchor._p)
                anchor._p.addnext(new_el)
                new_para = Paragraph(new_el, anchor._parent)
                set_paragraph_text(new_para, text)
                anchor = new_para

    doc.save(str(DOC))
    print(f"替换 {changed} 段，新增 {0 if exists else len(NEW_PARAGRAPHS)} 段 -> {DOC.name}")


if __name__ == "__main__":
    main()

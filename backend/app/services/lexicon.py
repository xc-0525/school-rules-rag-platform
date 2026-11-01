"""领域词典：意图、实体同义词、口语说法与图谱关键词。

P0-5 / P2-7 修复：
- 这些是**领域知识**，不再放在 `mock_data`（那是检索用的假语料）里，
  避免生产代码 import 一个叫 mock 的模块；
- 查询扩展不再"从 entities 里反向过滤"（按构造必然为空），
  改为**主动查同义词表**：实体 → 近义说法、口语整句 → 正式说法。

新增一个意图/词条时，只需改本文件；`stores.extract_graph_keywords`
与 `nodes.query_understand` 都从这里取词。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 意图词典：用于查询理解（intent + 实体抽取）
# ---------------------------------------------------------------------------
INTENT_KEYWORDS: dict[str, list[str]] = {
    "宿舍管理": ["宿舍", "熄灯", "晚归", "夜不归宿", "电器", "寝室", "公寓"],
    "考试纪律": ["考试", "作弊", "手机", "违纪", "考场"],
    "奖助学金": ["奖学金", "助学金", "评奖", "资助", "励志"],
    "考勤请假": ["请假", "旷课", "考勤", "放假", "销假", "缺课"],
    "违纪处分": ["打架", "斗殴", "处分", "记过", "警告", "留校察看"],
    "学籍管理": ["学籍", "注册", "转专业", "休学", "退学", "补考", "重修"],
    "校园服务": [
        "图书馆", "借阅", "续借", "自习", "食堂", "校园卡", "挂失", "补卡",
        "校园网", "网络", "校医院", "医疗", "心理咨询", "就业", "招聘", "场馆",
        "体育", "游泳", "社团", "预约",
    ],
}

# ---------------------------------------------------------------------------
# 实体 → 近义说法（查询扩展的主力：只扩命中的实体，不做意图级扩散）
#
# 为什么不按"同意图的其他关键词"扩展：那会把「熄灯」问题拽向「电器/晚归」，
# 召回上升但精度下降（详见审查报告 P0-5 修正 2）。
# ---------------------------------------------------------------------------
ENTITY_SYNONYMS: dict[str, list[str]] = {
    "熄灯": ["断电", "关灯", "熄灯时间"],
    "晚归": ["迟归", "夜不归宿"],
    "夜不归宿": ["晚归", "未归"],
    "寝室": ["宿舍", "公寓"],
    "宿舍": ["公寓", "寝室"],
    "电器": ["大功率", "违规电器"],
    "考试": ["考场", "考试纪律"],
    "作弊": ["违纪", "考场违纪"],
    "手机": ["电子设备", "智能手表"],
    "奖学金": ["助学金", "评奖", "资助"],
    "助学金": ["奖学金", "资助"],
    "请假": ["审批", "销假", "考勤"],
    "旷课": ["缺课", "考勤"],
    "处分": ["纪律处分", "记过"],
    "学籍": ["注册", "休学", "退学"],
    "图书馆": ["借阅", "自习", "开放时间"],
    "校园卡": ["挂失", "补卡", "充值"],
    "食堂": ["就餐", "饭卡"],
    "校园网": ["网络", "上网"],
    "心理咨询": ["心理", "辅导"],
    "就业": ["招聘", "就业指导"],
}

# ---------------------------------------------------------------------------
# 口语整句 → 正式说法（原 EXPANSIONS，保留并小幅补充）
# ---------------------------------------------------------------------------
EXPANSIONS: dict[str, list[str]] = {
    "不回寝室": ["夜不归宿", "晚归"],
    "回寝室": ["夜不归宿", "晚归"],
    "几点关门": ["熄灯", "晚归"],
    "查寝": ["夜不归宿", "晚归"],
    "带电": ["电器", "大功率"],
    "挂科": ["考试", "违纪", "作弊"],
    "被处分": ["处分", "违纪"],
    "评奖": ["奖学金", "助学金"],
    "放假": ["请假", "考勤"],
    "饭卡": ["校园卡", "充值"],
    "断网": ["熄灯", "校园网"],
}

# 图谱检索关键词池（由上面三张表 + 高频词展开，便于 Neo4j 侧做包含匹配）
GRAPH_TERMS: set[str] = set()
for _keywords in INTENT_KEYWORDS.values():
    GRAPH_TERMS.update(_keywords)
for _synonyms in ENTITY_SYNONYMS.values():
    GRAPH_TERMS.update(_synonyms)
for _phrases, _synonyms in EXPANSIONS.items():
    GRAPH_TERMS.add(_phrases)
    GRAPH_TERMS.update(_synonyms)
GRAPH_TERMS.update(
    {
        "熄灯", "晚归", "夜不归宿", "查寝", "电器", "大功率", "作弊", "手机", "考试",
        "奖学金", "助学金", "励志", "请假", "旷课", "考勤", "打架", "斗殴", "处分",
        "开除", "记过", "留校察看", "论文", "抄袭", "学籍", "注册", "转专业", "休学",
        "退学", "补考", "重修", "评奖", "评优", "宿舍管理", "总则", "附则",
        "图书馆", "借阅", "续借", "自习", "食堂", "校园卡", "挂失", "补办",
        "校园网", "网络", "校园码", "校医院", "医疗", "心理咨询", "心理",
        "就业", "招聘", "场馆", "体育", "游泳", "社团", "预约",
    }
)


def _sort_by_length(terms: list[str]) -> list[str]:
    """长词优先：避免「宿舍」把「宿舍管理」吃掉。"""
    return list(dict.fromkeys(sorted(terms, key=len, reverse=True)))


_ENTITY_VOCAB = _sort_by_length(
    [*GRAPH_TERMS, *INTENT_KEYWORDS.keys(), *ENTITY_SYNONYMS.keys(), *EXPANSIONS.keys()]
)


def extract_intent(query: str) -> tuple[str, list[str]]:
    """识别意图与实体（长词优先，实体去重）。"""
    intent = "其他"
    entities: list[str] = []
    for name, keywords in INTENT_KEYWORDS.items():
        matched = [keyword for keyword in keywords if keyword in query]
        if matched:
            if intent == "其他":  # 取第一个命中意图，避免多意图互相覆盖
                intent = name
            entities.extend(matched)
    if not entities:
        # 没有任何领域词命中：只把整句当作实体，但下游不会用它去拼检索词
        entities = [query]
    return intent, list(dict.fromkeys(entities))


def expand_terms(query: str, entities: list[str], limit: int = 3) -> list[str]:
    """产出可追加到检索串的新词。

    顺序：口语整句（更具体） → 实体近义词（更泛）；只返回 query 里没有的词。
    绝不返回 query 自身或其子串——那正是旧实现空转的原因。
    """
    added: list[str] = []

    def _push(candidate: str) -> None:
        term = (candidate or "").strip()
        # 过滤：空词、已存在、整句回灌、过短
        if len(term) < 2 or term in query or term in added:
            return
        added.append(term)

    for phrase, synonyms in EXPANSIONS.items():
        if phrase in query:
            for synonym in synonyms:
                _push(synonym)

    for entity in entities:
        if len(entity) < 2 or entity == query:
            continue
        for synonym in ENTITY_SYNONYMS.get(entity, ()):
            _push(synonym)

    return added[: max(0, limit)]


def expand_query(query: str, entities: list[str], limit: int = 3) -> tuple[str, list[str]]:
    """返回（扩展后的检索串, 实际追加的词）。没有可扩词时原样返回。"""
    terms = expand_terms(query, entities, limit=limit)
    if not terms:
        return query, []
    return f"{query} {' '.join(terms)}", terms

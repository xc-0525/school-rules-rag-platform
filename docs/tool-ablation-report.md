# 检索与生成评测报告

- 语料模式：vector=`qdrant` / graph=`neo4j` / llm=`deepseek`
- 题量：8
- 证据阈值：0.5；融合模式=append；RRF k=60
- 图谱闸门：准入分 0.55；条目占比上限 0.5

## 检索指标

| 融合配置 | recall@3 | MRR | 命中/总数 | 图谱空召回率 |
| --- | --- | --- | --- | --- |
| 双路（append） | 0.750 | 0.442 | 6/8 | 0.000 |

## 校园服务工具消融（CAMPUS_TOOLS_ENABLED 开 / 关）

- 题量：8 题（其中 RAG 对照题按 `expect_tool=search_rules` 单列统计）

| 配置 | 工具选对率 | RAG 对照题命中 | 被拒答题数 | p50 时延 |
| --- | --- | --- | --- | --- |
| 开启校园工具 | 1.000 | 1.000 | 0 | 3512.0 ms |
| 关闭校园工具 | 0.125 | 1.000 | 4 | 3245.0 ms |

> 关闭后 `query_repair_status / query_my_reservations / query_location_availability / query_lost_found` 不再下发给模型，实时业务问题只能走 RAG 或拒答——这一列与开启列的差值即工具接入带来的增益。

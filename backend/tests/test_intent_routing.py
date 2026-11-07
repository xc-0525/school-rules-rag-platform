"""办理意图判定与场馆名称模糊匹配（用户报的两个"智能度下降"缺陷的回归测试）。"""

from __future__ import annotations

import unittest

from app.agents.tools import match_locations
from app.agents.tools_node import _looks_like_action

LOCATIONS = [
    {"id": 1, "name": "图书馆一楼自习区 A 区", "zone": "一楼", "capacity": 40},
    {"id": 2, "name": "图书馆二楼研讨间 201", "zone": "二楼", "capacity": 1},
    {"id": 3, "name": "图书馆二楼研讨间 202", "zone": "二楼", "capacity": 1},
    {"id": 4, "name": "图书馆三楼电子阅览区", "zone": "三楼", "capacity": 30},
]


class MatchLocationsTest(unittest.TestCase):
    def names(self, keyword: str) -> list[str]:
        hits, _ = match_locations(LOCATIONS, keyword)
        return [str(item["name"]) for item in hits]

    def test_exact_substring(self) -> None:
        self.assertEqual(len(self.names("图书馆")), 4)
        self.assertEqual(self.names("201"), ["图书馆二楼研讨间 201"])

    def test_alias_normalization(self) -> None:
        """库里叫“研讨间”，学生口语说“研讨室/讨论室/会议室”——必须能对上。"""
        expected = ["图书馆二楼研讨间 201", "图书馆二楼研讨间 202"]
        for keyword in ("研讨室", "讨论室", "会议室"):
            self.assertEqual(self.names(keyword), expected, keyword)
        self.assertEqual(self.names("自习室"), ["图书馆一楼自习区 A 区"])

    def test_whitespace_and_case_insensitive(self) -> None:
        """正式名是“自习区 A 区”（带空格），用户常说“a区”。"""
        self.assertEqual(self.names("a区"), ["图书馆一楼自习区 A 区"])
        self.assertEqual(self.names("A区"), ["图书馆一楼自习区 A 区"])

    def test_bigram_only_returns_best_match(self) -> None:
        """“图书馆a区座位”不能被 4 个都以“图书馆”开头的名字全命中。"""
        self.assertEqual(self.names("图书馆A区座位"), ["图书馆一楼自习区 A 区"])

    def test_no_match_returns_empty(self) -> None:
        """库里没有的场馆必须返回空，让调用方回可选清单，不能瞎猜。"""
        for keyword in ("体育馆", "游泳馆", "校医院"):
            self.assertEqual(self.names(keyword), [], keyword)

    def test_empty_keyword_returns_all(self) -> None:
        self.assertEqual(len(self.names("")), 4)


class LooksLikeActionTest(unittest.TestCase):
    def test_action_with_service_noun(self) -> None:
        cases = [
            "那你帮我将两个都取消了然后预约后天下午的图书馆a区座位",
            "取消我今天下午的研讨间预约",
            "帮我报修一下",
            "我想预约明天的自习座位",
        ]
        for query in cases:
            self.assertTrue(_looks_like_action(query), query)

    def test_room_reference_short_reply(self) -> None:
        """“我想约202”“要201”这类只给房号的短回复同样属于办理。"""
        for query in ("我想约202", "要201", "约 202"):
            self.assertTrue(_looks_like_action(query), query)

    def test_consultation_is_not_action(self) -> None:
        """问规则/流程的句子不能触发办理兜底，否则会把咨询也塞进工具链。"""
        cases = [
            "考试时带手机进考场会怎么处理？",
            "图书馆怎么预约",
            "预约需要什么条件",
            "报修流程是怎样的",
            "自习室开放时间是几点",
        ]
        for query in cases:
            self.assertFalse(_looks_like_action(query), query)

    def test_unrelated_query_is_not_action(self) -> None:
        for query in ("今天天气怎么样", "大约3点去图书馆", "", "国家奖学金需要什么条件"):
            self.assertFalse(_looks_like_action(query), query)


if __name__ == "__main__":
    unittest.main()

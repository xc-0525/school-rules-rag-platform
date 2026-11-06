"""Agent 提示词中的当前日期锚定。

回归背景：模型侧没有时钟，跨天续聊时会把历史里的"今天"当成现在，
出现过 11-06 被当成 11-05 的日期错乱。日期行必须每次组装 prompt 时实时生成。
"""

from __future__ import annotations

import unittest
from datetime import date

from app.agents.prompts import current_date_line, tool_system_prompt
from app.core.config import Settings


class CurrentDateLineTest(unittest.TestCase):
    def test_contains_today_and_relative_dates(self) -> None:
        line = current_date_line(date(2026, 11, 6))
        self.assertIn("2026-11-06", line)
        self.assertIn("星期五", line)
        self.assertIn("“明天”指 2026-11-07", line)
        self.assertIn("“后天”指 2026-11-08", line)

    def test_defaults_to_real_today(self) -> None:
        line = current_date_line()
        self.assertIn(date.today().isoformat(), line)

    def test_tool_prompt_contains_date_line_in_both_modes(self) -> None:
        settings = Settings()
        for enabled in (True, False):
            settings.campus_tools_enabled = enabled
            self.assertIn("今天是", tool_system_prompt(settings))
            # 日期规则编号随新规则顺延，避免两条规则 12
            self.assertIn("12. 日期换算", tool_system_prompt(settings))
            self.assertIn("13. 不需要工具时不要强行调用", tool_system_prompt(settings))


if __name__ == "__main__":
    unittest.main()

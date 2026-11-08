"""N-20 身份画像测试：学历判定、证据过滤、Prompt 注入、资料接口。

全部走内存实现（不配 mysql_url），资料存进程内 dict。
运行：cd backend && python -m unittest tests.test_user_profile -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.agents.prompting import build_messages  # noqa: E402
from app.agents.state import AgentState  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.chat import Evidence  # noqa: E402
from app.services import profile as profile_service  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from tests.test_review_fixes import make_settings, token_for  # noqa: E402

STUDENT = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}


def evidence(title: str) -> Evidence:
    return Evidence(type="vector", title=title, detail="第一条", snippet="正文", score=0.9)


class EducationInferTests(unittest.TestCase):
    def test_infer(self) -> None:
        self.assertEqual(profile_service.infer_education_level("03-学生学籍管理规定本科.md"), "undergraduate")
        self.assertEqual(profile_service.infer_education_level("04-学生学籍管理规定专科.md"), "junior")
        self.assertEqual(profile_service.infer_education_level("08-学生住宿管理规定.md"), "general")

    def test_normalize(self) -> None:
        self.assertEqual(profile_service.normalize_education("Unset"), "unset")
        with self.assertRaises(profile_service.ProfileError):
            profile_service.normalize_education("doctor")
        with self.assertRaises(profile_service.ProfileError):
            # general 只用于文档，不能作为用户身份
            profile_service.normalize_education("general")


class EvidenceFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            evidence("03-学生学籍管理规定本科.md"),
            evidence("04-学生学籍管理规定专科.md"),
            evidence("08-学生住宿管理规定.md"),
        ]

    def test_undergraduate_keeps_own_and_general(self) -> None:
        kept = profile_service.filter_evidence(self.items, "undergraduate")
        self.assertEqual(
            [item.title for item in kept],
            ["03-学生学籍管理规定本科.md", "08-学生住宿管理规定.md"],
        )

    def test_junior_drops_undergraduate(self) -> None:
        kept = profile_service.filter_evidence(self.items, "junior")
        self.assertNotIn("03-学生学籍管理规定本科.md", [item.title for item in kept])

    def test_unset_does_not_filter(self) -> None:
        kept = profile_service.filter_evidence(self.items, "unset")
        self.assertEqual(len(kept), 3)

    def test_limit_applied(self) -> None:
        kept = profile_service.filter_evidence(self.items, "unset", limit=2)
        self.assertEqual(len(kept), 2)


class PromptTests(unittest.TestCase):
    def _system_content(self, level: str) -> str:
        state = AgentState(query="挂科几门会退学", user_education=level)
        messages = build_messages(state)
        return messages[0]["content"]

    def test_prompt_states_level(self) -> None:
        self.assertIn("本科生", self._system_content("undergraduate"))
        self.assertIn("专科生", self._system_content("junior"))

    def test_unset_prompt_asks_not_guess(self) -> None:
        content = self._system_content("unset")
        self.assertIn("未设置学历", content)
        self.assertIn("不要替用户猜测", content)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        profile_service.clear_mock_profiles()
        settings = make_settings()
        settings.auth_required = True
        settings.dev_mode = False
        services = build_services(settings)
        services.llm = FakeLLM()
        self.client = TestClient(create_app(settings, services=services))

    def test_me_defaults_unset(self) -> None:
        me = self.client.get("/api/v1/auth/me", headers=STUDENT)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["education_level"], "unset")

    def test_update_profile_then_me(self) -> None:
        resp = self.client.patch(
            "/api/v1/auth/profile",
            json={"education_level": "undergraduate", "college": "人工智能学院", "grade": "2024"},
            headers=STUDENT,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        # 画像随 token 下发，改完换发新 token
        self.assertEqual(body["user"]["education_level"], "undergraduate")
        self.assertEqual(body["user"]["education_label"], "本科")
        fresh = {"Authorization": f"Bearer {body['token']}"}

        me = self.client.get("/api/v1/auth/me", headers=fresh).json()
        self.assertEqual(me["college"], "人工智能学院")
        self.assertEqual(me["grade"], "2024")

    def test_invalid_level_rejected(self) -> None:
        resp = self.client.patch(
            "/api/v1/auth/profile", json={"education_level": "doctor"}, headers=STUDENT
        )
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_requires_login(self) -> None:
        self.assertEqual(self.client.patch("/api/v1/auth/profile", json={}).status_code, 401)

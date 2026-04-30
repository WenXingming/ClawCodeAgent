"""main_chat CLI 骨架兼容测试。"""

from __future__ import annotations

import unittest

from app.app_gateway import AppGateway
from main import main


class MainChatSkeletonTests(unittest.TestCase):
    def test_main_chat_stub_returns_zero(self) -> None:
        result = main([])
        self.assertEqual(result, 0)

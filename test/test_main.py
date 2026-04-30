"""main 入口与 AppGateway 极简骨架测试。"""

from __future__ import annotations

import unittest

from app.app_gateway import AppGateway
from main import main


class AppGatewaySkeletonTests(unittest.TestCase):
    def _make_gateway(self) -> AppGateway:
        return AppGateway(
            openai_client_cls=object,
            agent_cls=object,
            session_manager_cls=object,
        )

    def test_run_cli_stub_returns_zero(self) -> None:
        gateway = self._make_gateway()
        result = gateway.run_cli([])
        self.assertEqual(result, 0)

    def test_create_query_service_stub_returns_none(self) -> None:
        gateway = self._make_gateway()
        result = gateway.create_query_service(runtime_agent=object())
        self.assertIsNone(result)

    def test_main_entry_point_returns_zero(self) -> None:
        # main() 通过骨架 AppGateway.run_cli() 返回 0
        result = main([])
        self.assertEqual(result, 0)

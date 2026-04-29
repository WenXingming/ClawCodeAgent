"""ISSUE-026 发布门禁文档与脚本校验测试。"""

from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent


class ReleaseGateDocsTests(unittest.TestCase):
    def test_release_gate_artifacts_exist(self) -> None:
        expected_paths = (
            _ROOT / 'docs' / 'TEST_MATRIX.md',
            _ROOT / 'docs' / 'RELEASE_GATE_CHECKLIST.md',
            _ROOT / 'docs' / 'DEMO_SCRIPT.md',
            _ROOT / 'scripts' / 'run_release_gate.ps1',
        )

        for path in expected_paths:
            self.assertTrue(path.is_file(), f'missing release gate artifact: {path}')

    def test_test_matrix_and_checklist_reference_release_commands(self) -> None:
        test_matrix = (_ROOT / 'docs' / 'TEST_MATRIX.md').read_text(encoding='utf-8')
        checklist = (_ROOT / 'docs' / 'RELEASE_GATE_CHECKLIST.md').read_text(encoding='utf-8')

        self.assertIn('C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v', test_matrix)
        self.assertIn('scripts/run_release_gate.ps1', test_matrix)
        self.assertIn('scripts/run_release_gate.ps1', checklist)
        self.assertIn('test_release_gate_docs.py', checklist)

    def test_demo_script_covers_cli_and_query_service_flows(self) -> None:
        demo_script = (_ROOT / 'docs' / 'DEMO_SCRIPT.md').read_text(encoding='utf-8')

        self.assertIn('agent-chat', demo_script)
        self.assertIn('agent-resume', demo_script)
        self.assertIn('QueryService', demo_script)

    def test_release_gate_script_runs_required_steps(self) -> None:
        script = (_ROOT / 'scripts' / 'run_release_gate.ps1').read_text(encoding='utf-8')

        self.assertIn("Full unittest regression", script)
        self.assertIn("Orchestration regression", script)
        self.assertIn("Release docs validation", script)
        self.assertIn("agent-chat --help", script)
        self.assertIn("agent-resume --help", script)


if __name__ == '__main__':
    unittest.main()
"""递归装载无 __init__.py 测试树。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


_TEST_ROOT = Path(__file__).resolve().parent
_THIS_FILE = Path(__file__).resolve()
_SRC_ROOT = _TEST_ROOT.parent / 'src'


def _load_test_module(file_path: Path):
    relative = file_path.relative_to(_TEST_ROOT).with_suffix('')
    module_name = 'test_loader_' + '_'.join(relative.parts)
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f'Cannot load test module: {file_path}')

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None):
    src_path = str(_SRC_ROOT)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    suite = unittest.TestSuite()
    for file_path in sorted(_TEST_ROOT.rglob('test_*.py')):
        if file_path.resolve() == _THIS_FILE:
            continue
        module = _load_test_module(file_path)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
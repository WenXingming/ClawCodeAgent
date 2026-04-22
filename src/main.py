"""最小可运行命令行入口。

用法目标：
1) 通过环境变量或命令行参数注入模型配置。
2) 调用 LocalCodingAgent 执行单次任务。
3) 给出清晰的失败提示，便于本地快速实验。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .agent_runtime import LocalCodingAgent
from .contract_types import AgentPermissions, AgentRuntimeConfig, ModelConfig
from .openai_client import OpenAIClient, OpenAIClientError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run LocalCodingAgent with minimal CLI.')
    parser.add_argument('prompt', nargs='+', help='User prompt to run.')
    parser.add_argument('--model', default='', help='Model name. Fallback: OPENAI_MODEL.')
    parser.add_argument('--base-url', default='', help='OpenAI-compatible base URL. Fallback: OPENAI_BASE_URL.')
    parser.add_argument('--api-key', default='', help='API key. Fallback: OPENAI_API_KEY.')
    parser.add_argument('--cwd', default='.', help='Working directory for tools.')
    parser.add_argument('--max-turns', type=int, default=12, help='Maximum agent turns.')
    parser.add_argument('--temperature', type=float, default=0.0, help='Model temperature.')
    parser.add_argument('--allow-file-write', action='store_true', help='Allow write_file/edit_file tools.')
    parser.add_argument('--allow-shell', action='store_true', help='Allow bash tool.')
    parser.add_argument(
        '--allow-destructive-shell',
        action='store_true',
        help='Allow destructive shell commands (requires --allow-shell).',
    )
    return parser


def _required_value(cli_value: str, env_key: str, field_name: str) -> str:
    value = cli_value.strip() if cli_value.strip() else os.getenv(env_key, '').strip()
    if value:
        return value
    raise ValueError(f'Missing required {field_name}. Use --{field_name.replace("_", "-")} or {env_key}.')


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.allow_destructive_shell and not args.allow_shell:
            raise ValueError('allow_destructive_shell requires --allow-shell')

        model = _required_value(args.model, 'OPENAI_MODEL', 'model')
        api_key = _required_value(args.api_key, 'OPENAI_API_KEY', 'api_key')
        base_url = args.base_url.strip() or os.getenv('OPENAI_BASE_URL', '').strip() or 'http://127.0.0.1:8000/v1'

        model_config = ModelConfig(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
        )
        runtime_config = AgentRuntimeConfig(
            cwd=Path(args.cwd).resolve(),
            max_turns=args.max_turns,
            permissions=AgentPermissions(
                allow_file_write=args.allow_file_write,
                allow_shell_commands=args.allow_shell,
                allow_destructive_shell_commands=args.allow_destructive_shell,
            ),
        )

        client = OpenAIClient(model_config)
        agent = LocalCodingAgent(client, runtime_config)
        result = agent.run(' '.join(args.prompt))

        print(result.final_output)
        return 0
    except (ValueError, OpenAIClientError) as exc:
        print(f'[main] {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

# 演示脚本

## 1. 前提

1. 在仓库根目录执行以下命令。
2. 已配置真实可用的 OpenAI-compatible 后端。

```powershell
$env:OPENAI_MODEL = "your-model"
$env:OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:OPENAI_API_KEY = "your-api-key"
```

## 2. 控制面 smoke

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py agent --help
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-chat --help
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-resume --help
```

预期：三个命令都成功退出，且展示子命令参数说明。

## 3. 交互式 agent 演示

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py agent
```

在提示符里依次输入：

```text
/help
读取 README 并总结当前支持的主命令
/status
.exit
```

预期：

1. `/help` 与 `/status` 不触发模型调用。
2. 普通自然语言请求可返回总结。
3. 退出时会打印 session_id 提示。

## 4. agent-chat / agent-resume 演示

说明：交互式命令默认会打印 `[progress]` 过程日志；如果需要更干净的终端录屏，可改用 `--no-show-progress`。

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-chat
```

在提示符里输入：

```text
先记住一句话：ClawCodeAgent 支持 delegate_agent 和 QueryEngine
.exit
```

记录输出中的 session_id，然后执行：

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py agent-resume <session_id>
```

在提示符里输入：

```text
复述刚才记住的那句话
.exit
```

预期：resume 能延续上一轮上下文。

## 5. QueryEngine API 演示

```powershell
@'
from pathlib import Path

from core_contracts.config import AgentPermissions, AgentRuntimeConfig
from openai_client.openai_client import OpenAIClient
from orchestration.local_agent import LocalAgent
from orchestration.query_engine import QueryEngine
from session.session_store import AgentSessionStore

runtime_config = AgentRuntimeConfig(
    cwd=Path('.'),
    max_turns=4,
    session_directory=Path('.port_sessions') / 'agent',
    permissions=AgentPermissions(allow_file_write=True),
)
agent = LocalAgent(OpenAIClient.from_env(), runtime_config, AgentSessionStore(runtime_config.session_directory))
engine = QueryEngine.from_runtime_agent(agent)
turn = engine.submit('读取 README 并总结 QueryEngine 的职责')
print(turn.stop_reason)
print(turn.session_id)
print(engine.persist_session())
print(engine.render_summary())
'@ | C:/ProgramData/anaconda3/python.exe -
```

预期：

1. `submit()` 返回稳定的 `TurnResult`。
2. `persist_session()` 返回最近一次 session_path。
3. `render_summary()` 含 runtime event 与 transcript 统计。
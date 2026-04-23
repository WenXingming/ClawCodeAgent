# 快速开始（设置 API Key 直接实验）

## 1. 前置条件

- 已在根目录打开工程：D:/WorkSpace/ClawCodeAgent
- Python 环境可用（当前建议使用 C:/ProgramData/anaconda3/python.exe）
- 你有可用的 OpenAI-compatible 后端地址、模型名和 API Key

## 2. 设置环境变量（PowerShell）

```powershell
$env:OPENAI_MODEL = "your-model-name"
$env:OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:OPENAI_API_KEY = "your-api-key"
```

`src/` 现在是源码根目录，不再作为 `src` 包名参与导入。代码里应使用 `from core_contracts...`、`from runtime...` 这类顶层绝对导入。

## 3. 运行一次最小实验

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py "请读取当前目录结构并简要总结"
```

## 4. 常用参数示例

```powershell
C:/ProgramData/anaconda3/python.exe ./src/main.py \
  --cwd . \
  --max-turns 8 \
  --allow-file-write \
  "请在当前目录创建一个 demo.txt 并写入 hello"
```

## 5. 续跑已保存会话（Resume）

每次运行后会在 `.port_sessions/agent/` 目录生成一个 `<session_id>.json` 文件。  
使用 `--session-id` 可从上次结束的上下文继续执行：

```powershell
# 查找 session_id（从上次运行的输出或会话目录获得）
Get-ChildItem .port_sessions\agent\

# Resume（严格继承上次保存的 model/runtime 配置，只需提供新 prompt）
C:/ProgramData/anaconda3/python.exe ./src/main.py --session-id <session_id> "继续上次任务"
```

**常见错误**
- `Session not found`：session_id 不存在或文件已删除，请重新 run。
- `Corrupted session file`：session 文件损坏，无法恢复，请重新 run。

## 6. 预算控制（BudgetConfig）

通过 `BudgetConfig` 可以为每次运行设置多维度的安全上限：

| 字段 | 说明 | 触发 stop_reason |
|------|------|-----------------|
| `max_input_tokens` | 输入 token 硬上限（char/4 估算） | `token_limit` |
| `max_total_cost_usd` | 会话总成本上限（USD） | `cost_limit` |
| `max_tool_calls` | 工具调用次数上限 | `tool_call_limit` |
| `max_model_calls` | 模型调用次数上限 | `model_call_limit` |
| `max_session_turns` | 会话累计轮数上限（含 resume 历史） | `session_turns_limit` |

代码示例：

```python
from core_contracts.config import AgentRuntimeConfig, BudgetConfig
from runtime.agent_runtime import LocalCodingAgent

config = AgentRuntimeConfig(
    cwd='.',
    budget_config=BudgetConfig(
        max_input_tokens=32_000,   # 限制 prompt 不超过约 32K token
        max_total_cost_usd=0.10,   # 整个会话最多花 0.1 USD
        max_tool_calls=20,         # 最多调用工具 20 次
        max_model_calls=10,        # 最多调用模型 10 次
        max_session_turns=50,      # 含 resume 累计轮数不超过 50
    ),
)
agent = LocalCodingAgent(client, config)
result = agent.run('...')
print(result.stop_reason)  # 预算超限时返回对应的 *_limit 字符串
```

**软超限（is_soft_over）**：当 prompt 接近上限但尚未触发硬停止时，`token_budget` event 中的 `is_soft_over=True`，ISSUE-010/011 的 snip/compact 将据此压缩上下文。

## 7. 说明

- `--model`、`--base-url`、`--api-key` 都支持命令行覆盖。
- 若不传命令行参数，程序会回退读取环境变量：
  - OPENAI_MODEL
  - OPENAI_BASE_URL
  - OPENAI_API_KEY
- 默认是安全权限：不允许 shell，不允许危险 shell 命令。
- 从仓库根执行 Python 命令时，`sitecustomize.py` 会自动把 `src/` 注入 `sys.path`，因此测试与脚本都按源码根模式运行。
- 递归测试发现的标准命令是 `C:/ProgramData/anaconda3/python.exe -m unittest discover -s test -v`；它通过 `test/test_all.py` 在无 `__init__.py` 的测试树上继续递归装载所有测试。

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

## 3. 运行一次最小实验

```powershell
C:/ProgramData/anaconda3/python.exe -m src.main "请读取当前目录结构并简要总结"
```

## 4. 常用参数示例

```powershell
C:/ProgramData/anaconda3/python.exe -m src.main \
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
C:/ProgramData/anaconda3/python.exe -m src.main --session-id <session_id> "继续上次任务"
```

**常见错误**
- `Session not found`：session_id 不存在或文件已删除，请重新 run。
- `Corrupted session file`：session 文件损坏，无法恢复，请重新 run。

## 6. 说明

- `--model`、`--base-url`、`--api-key` 都支持命令行覆盖。
- 若不传命令行参数，程序会回退读取环境变量：
  - OPENAI_MODEL
  - OPENAI_BASE_URL
  - OPENAI_API_KEY
- 默认是安全权限：不允许 shell，不允许危险 shell 命令。

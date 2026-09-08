# 闲鱼自动回复 · MCP 接入说明

本文说明如何把这个机器人作为 **MCP 服务器** 安装到 AI 客户端（TRAE、Cursor、Claude Desktop 等），
安装后 AI 就能通过对话直接控制机器人：启停、查看状态、改配置、改规则、扫描消息、定向发送。

---

## 1. 能干什么（8 个工具）

| 工具 | 作用 | 安全级别 |
|---|---|---|
| `control_bot` | 启动/停止机器人（可指定试运行、回复模式） | 中：启动即自动回复 |
| `get_status` | 查看机器人运行状态、CDP 端口是否在线、最近日志 | 只读 |
| `read_log` | 读取机器人最近运行日志 | 只读 |
| `get_config` | 查看当前配置（模式、AI）与规则库 | 只读 |
| `update_config` | 修改模式 / AI Key / 兜底话术 / 忽略词 | 低：写配置 |
| `set_rules` | 整体替换回复规则库 | 低：写配置 |
| `scan_once` | 立即检测一轮，列出各会话最新买家消息与拟回复（**不发送**） | 只读 |
| `send_text` | 向指定会话定向发送一条消息 | **高：真实发送** |

安装后 AI 对话示例：
- “巡查一下现在有哪些买家发了新消息？” → `scan_once`
- “把模式切到纯AI，模型换成 deepseek-chat” → `update_config`
- “启动机器人（试运行）” → `control_bot`
- “给小号发一句：亲，晚上给你发教程哦” → `send_text`

---

## 2. 安装到各个 AI 客户端

统一命令（stdio 方式）：

```
命令：  d:\桌面\豆包\doubao\venv\Scripts\python.exe
参数：  d:\桌面\豆包\doubao\xianyu-bot\mcp_server.py
工作目录：d:\桌面\豆包\doubao\xianyu-bot
```

### TRAE
设置 → MCP → 添加服务器 → 本地 MCP（stdio）→ 服务名称填 `xianyu-bot` → 填上面命令/参数。

### Cursor
在项目根放 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "xianyu-bot": {
      "command": "d:\\桌面\\豆包\\doubao\\venv\\Scripts\\python.exe",
      "args": ["d:\\桌面\\豆包\\doubao\\xianyu-bot\\mcp_server.py"]
    }
  }
}
```

### Claude Desktop
编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "xianyu-bot": {
      "command": "d:\\桌面\\豆包\\doubao\\venv\\Scripts\\python.exe",
      "args": ["d:\\桌面\\豆包\\doubao\\xianyu-bot\\mcp_server.py"]
    }
  }
}
```

---

## 3. 前置条件（重要）

- 「闲鱼卖家客服」必须用调试模式打开（CDP 9222 端口可用），否则 `scan_once` / `send_text` / 机器人启动会失败。
  重新登录后若失效，先双击 `start_xianyu_debug.bat`。
- 机器人子进程由 MCP 管理（`control_bot start` 启动，`stop` 停止）；与手动双击 `start_bot.bat` 是**同一个 bot.py**，但两套入口不要同时运行（会重复回复）。
- `send_text` 是真实发送，调用前确认文案；建议先用 `scan_once` 了解会话内上下文。

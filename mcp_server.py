# -*- coding: utf-8 -*-
"""
闲鱼自动回复 · MCP 服务器
--------------------------
把闲鱼机器人封装成 MCP 工具，供 AI 客户端（TRAE / Cursor / Claude Desktop 等）安装调用。

提供工具：
  - control_bot      启动/停止机器人（可指定试运行与模式）
  - get_status       查看机器人运行状态、CDP 连接情况
  - read_log         读取机器人最近运行日志
  - get_config       查看当前配置与规则库
  - update_config    修改配置或规则库（保存到 config.json / rules.json）
  - scan_once        立即检测一轮，显示各会话最新买家消息与建议（不发送）
  - send_text        向指定会话定向发送一条消息（供 AI 人工干预/兜底）

启动方式（stdio）：
  venv\\Scripts\\python.exe mcp_server.py
"""

import json
import os
import signal
import subprocess
import sys
import time

from mcp.server.fastmcp import FastMCP

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
RULES_PATH = os.path.join(BASE_DIR, "rules.json")
LOG_PATH = os.path.join(BASE_DIR, "bot_run.log")

bot_proc: subprocess.Popen | None = None

mcp = FastMCP("闲鱼自动回复")


# ---------------- 内部工具 ----------------

def _read_json(path, default=None):
    try:
        # utf-8-sig：兼容带/不带 BOM 的文件
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------- 工具：机器人控制 ----------------

@mcp.tool()
def control_bot(action: str, dry_run: bool = False, mode: str = "") -> str:
    """
    启动或停止闲鱼自动回复机器人。
    action: "start" 或 "stop"
    dry_run: 是否试运行（只检测不发送，默认 False 表示真实回复）
    mode: rule(纯规则) / mixed(规则优先+AI兜底) / ai(纯AI)；
          留空则跟随 config.json 当前设置（推荐，之后改配置可热重载生效）
    """
    global bot_proc
    if action == "stop":
        if bot_proc and bot_proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/PID", str(bot_proc.pid), "/F", "/T"],
                               capture_output=True)
            except Exception as e:
                return f"停止失败：{e}"
            bot_proc = None
            return "机器人已停止。"
        return "机器人当前未在运行。"

    if action == "start":
        if bot_proc and bot_proc.poll() is None:
            return "机器人已在运行中，无需重复启动。"
        args = [sys.executable, os.path.join(BASE_DIR, "bot.py")]
        if dry_run:
            args.append("--dry-run")
        else:
            args.append("--no-dry-run")
        if mode:
            args += ["--mode", mode]
        mode_show = mode if mode else "跟随config.json"
        logf = open(LOG_PATH, "a", encoding="utf-8")
        logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 (mode={mode_show}, dry_run={dry_run}) =====\n")
        logf.flush()
        # windows 下隐藏控制台窗口
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        bot_proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                                    creationflags=creation, close_fds=True)
        time.sleep(2)
        return f"机器人已启动 (PID={bot_proc.pid}, mode={mode_show}, dry_run={dry_run})。" \
               f"可用 read_log 查看启动日志，get_status 查看状态。"

    return "未知操作，仅支持 start / stop"


@mcp.tool()
def get_status() -> str:
    """查看机器人当前运行状态、CDP 调试端口连接情况、最近日志结尾。"""
    global bot_proc
    # CDP 端口检测
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    cdp_ok = False
    try:
        cdp_ok = s.connect_ex(("127.0.0.1", 9222)) == 0
    finally:
        s.close()

    running = bool(bot_proc and bot_proc.poll() is None)
    lines = []
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-6:]
    except Exception:
        pass
    return json.dumps({
        "机器人运行中": running,
        "进程PID": bot_proc.pid if running else None,
        "CDP端口9222可用(闲鱼客服已开调试模式)": cdp_ok,
        "最近日志": "".join(lines[-5:]) or "(暂无日志，可先 control_bot start)",
    }, ensure_ascii=False)


@mcp.tool()
def read_log(lines: int = 30) -> str:
    """读取机器人最近运行日志（返回最后 N 行，默认 30 行）。"""
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-max(1, min(int(lines), 200)):])
    except FileNotFoundError:
        return "(暂无日志文件，机器人尚未运行过)"


# ---------------- 工具：配置管理 ----------------

@mcp.tool()
def get_config() -> str:
    """查看当前完整配置（运行模式、AI 厂商/Key/模型）和回复规则库（规则、忽略词、兜底话术）。"""
    return json.dumps({
        "config": _read_json(CONFIG_PATH),
        "rules": _read_json(RULES_PATH, {"rules": []}),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def update_config(change: str) -> str:
    """
    修改配置或规则库。
    change 为 JSON 字符串，支持以下片段（可同时给出）：
      {"mode": "rule|mixed|ai"}
      {"ai": {"api_key": "sk-xxx", "model": "x", "base_url": "y", "timeout": 30}}
      {"default": "兜底话术文字"}
      {"ignore": ["好的", "谢谢"]}
      {"rules_title": "不回"}            // 预留给提示；真正改规则请用 set_rules
    示例：{"mode": "ai", "ai": {"model": "deepseek-chat"}}
    """
    try:
        patch = json.loads(change)
    except json.JSONDecodeError as e:
        return f"参数不是合法 JSON：{e}"

    cfg = _read_json(CONFIG_PATH, {})
    rules = _read_json(RULES_PATH, {"rules": []})

    if "mode" in patch and patch["mode"] in ("rule", "mixed", "ai"):
        cfg["mode"] = patch["mode"]
    if "ai" in patch and isinstance(patch["ai"], dict):
        cfg.setdefault("ai", {}).update({k: v for k, v in patch["ai"].items() if v not in (None, "")})
    if "default" in patch:
        rules["default"] = patch["default"]
    if "ignore" in patch and isinstance(patch["ignore"], list):
        rules["ignore"] = [str(x) for x in patch["ignore"]]

    _write_json(CONFIG_PATH, cfg)
    _write_json(RULES_PATH, rules)
    return "已保存。若机器人正在运行，几秒内会自动热重载生效，无需重启。"


@mcp.tool()
def set_rules(new_rules: str) -> str:
    """
    整体替换回复规则库（高级操作）。
    new_rules 为 JSON 字符串：
      {"default": "兜底话术", "ignore": ["好的"], "rules": [
          {"desc": "规则名", "keywords": ["关键词1","关键词2"], "reply": "回复文案"}, ...
      ]}
    """
    try:
        data = json.loads(new_rules)
    except json.JSONDecodeError as e:
        return f"参数不是合法 JSON：{e}"
    if not isinstance(data.get("rules"), list):
        return "rules 必须是数组"
    for r in data["rules"]:
        r.setdefault("desc", "")
        r.setdefault("reply", "")
        r["keywords"] = [str(k) for k in (r.get("keywords") or []) if str(k).strip()]
    rules = {
        "_说明": "自动回复规则库（经 MCP 更新）。",
        "default": data.get("default") or "您好，感谢您的咨询～您的消息我已收到，正在为您处理，稍后详细答复～",
        "ignore": data.get("ignore") or [],
        "rules": data["rules"],
    }
    _write_json(RULES_PATH, rules)
    return f"规则库已更新，共 {len(data['rules'])} 条规则。"


# ---------------- 工具：监听与干预 ----------------

@mcp.tool()
def scan_once() -> str:
    """
    立即执行一轮检测：连接当前打开的闲鱼客服页面，
    扫描各会话最新一条买家消息，返回“是否需要回复”的判断摘要。不发送任何消息。
    """
    try:
        sys.path.insert(0, BASE_DIR)
        from bot import (JS_READ_SESSIONS, JS_CLICK_SESSION, JS_READ_LAST_BUYER_MSG,
                         SYSTEM_SKIP_KEYWORDS, generate_reply, load_rules, load_config, strip_nick,
                         select_kb)
        from ai_client import DoubaoAI
        from cdp_client import CDPClient
        import json as _json

        rules = load_rules()
        cfg = load_config()
        ai = DoubaoAI(cfg.get("ai") or {})
        c = CDPClient()
        c.connect(url_keyword="seller.goofish.com")
        sessions = c.evaluate(JS_READ_SESSIONS) or []
        if not sessions:
            return "未在页面读到会话列表，请确认闲鱼卖家客服已打开并处于消息工作台。"
        reports = []
        for s in sessions[:10]:
            key = s["name"]
            c.evaluate(JS_CLICK_SESSION % _json.dumps(key))
            time.sleep(0.9)
            msg = c.evaluate(JS_READ_LAST_BUYER_MSG)
            if not msg.get("ok"):
                reports.append(f"· {key}：无可见消息")
                continue
            if msg["isMine"]:
                reports.append(f"· {key}：最后消息是自己发的，无需回复")
                continue
            text = strip_nick(msg["text"], key)   # 去掉开头昵称，避免误当商品名
            if len(text) <= 1:
                reports.append(f"· {key}：最后消息是图片/表情，跳过")
                continue
            if any(k in text for k in SYSTEM_SKIP_KEYWORDS):
                reports.append(f"· {key}：系统卡片消息，跳过（{text[:24]}）")
                continue
            reply, desc = generate_reply(cfg.get("mode", "mixed"), rules, ai, text, [],
                                             shop_info=cfg.get("shop_info") or "")
            if reply is None:
                reports.append(f"· {key}：买家消息“{text[:30]}”→ 忽略，不回复")
            else:
                reports.append(f"· {key}：买家消息“{text[:30]}”→ 【{desc}】拟回复：{reply[:40]}")
        return "\n".join(reports) or "(无可汇报项)"
    except Exception as e:
        return f"检测失败：{e}"


@mcp.tool()
def send_text(session_name: str, text: str) -> str:
    """
    向指定会话定向发送一条消息（人工干预/兜底用）。
    session_name: 会话昵称（与左侧列表一致）
    text: 要发送的文案
    注意：会真实发送给买家，请确认文案无误后再调用。
    """
    try:
        import json as _json
        from cdp_client import CDPClient
        c = CDPClient()
        c.connect(url_keyword="seller.goofish.com")
        from bot import JS_CLICK_SESSION
        r = c.evaluate(JS_CLICK_SESSION % _json.dumps(session_name))
        if not r.get("ok"):
            return f"会话「{session_name}」不存在或无法点开。"
        time.sleep(1.2)
        from bot import JS_FOCUS_TEXTAREA, JS_READ_TEXTAREA, JS_GET_SEND_BTN, JS_CLEAR_TEXTAREA
        fr = c.evaluate(JS_FOCUS_TEXTAREA)
        if not fr.get("ok"):
            return "输入框定位失败。"
        time.sleep(0.3)
        c.insert_text(text)
        time.sleep(0.4)
        if c.evaluate(JS_READ_TEXTAREA) != text:
            c.evaluate(JS_CLEAR_TEXTAREA)
            return "输入框校验失败，已取消发送（可能被拦截）。"
        send = c.evaluate(JS_GET_SEND_BTN)
        if not send:
            return "找不到发送按钮，未发送。"
        c.dispatch_mouse_click(send["x"], send["y"])
        time.sleep(0.8)
        left = c.evaluate(JS_READ_TEXTAREA) or ""
        return f"已发送到「{session_name}」：{text}" + (f"（输入框剩余：{left}）" if left else "")
    except Exception as e:
        return f"发送失败：{e}"


if __name__ == "__main__":
    mcp.run()

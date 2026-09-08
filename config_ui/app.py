# -*- coding: utf-8 -*-
"""
闲鱼自动回复 - 本地可视化配置中心（Flask）
------------------------------------------
功能：
1. 读取/保存 config.json（模式 + 火山方舟 AI 配置）
2. 读取/保存 rules.json（规则、话术、忽略词表、兜底话术）
3. 一键测试 AI 连通性
启动：venv\\Scripts\\python.exe config_ui\\app.py
访问：http://127.0.0.1:5000
"""

import json
import os
import subprocess
import sys
import time

from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # xianyu-bot 根目录
sys.path.insert(0, BASE_DIR)  # 使上级目录的 ai_client 可被导入

from ai_client import DoubaoAI

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
RULES_PATH = os.path.join(BASE_DIR, "rules.json")
HEARTBEAT_PATH = os.path.join(BASE_DIR, "bot_heartbeat.json")
BOT_PID_FILE = os.path.join(BASE_DIR, "bot_web.pid")   # 网页启动的机器人 PID 记录
BOT_LOG_PATH = os.path.join(BASE_DIR, "bot_run.log")   # 与 MCP 共用，方便统一查看
REPLY_LOG_PATH = os.path.join(BASE_DIR, "reply_log.jsonl")        # 回复审计台账
PENDING_PATH = os.path.join(BASE_DIR, "pending_review.json")      # 半自动审核队列
BUYERS_PATH = os.path.join(BASE_DIR, "buyers.json")               # 买家策略

app = Flask(__name__)
app.json.ensure_ascii = False  # 接口返回的中文不转义


@app.after_request
def force_json_charset(resp):
    """JSON 响应显式带 charset=utf-8，防止非 UTF-8 客户端（如 PowerShell 5.1）误解码后写回乱码"""
    ct = resp.headers.get("Content-Type", "")
    if ct.startswith("application/json") and "charset" not in ct:
        resp.headers["Content-Type"] = ct + "; charset=utf-8"
    return resp


def read_json(path, default=None):
    try:
        # utf-8-sig：兼容带/不带 BOM 的文件
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mask_key(key):
    """API Key 脱敏：sk-2dfac503...0b2c，仅前4后4可见；非 sk- 前缀保留前3后3"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    if key.lower().startswith("sk-"):
        return key[:6] + "*" * 8 + key[-4:]
    return key[:3] + "*" * 6 + key[-3:]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = read_json(CONFIG_PATH)
    # API Key 脱敏回显（保存时若仍是掩码则沿用旧 Key）
    if isinstance(cfg.get("ai"), dict) and cfg["ai"].get("api_key"):
        cfg["ai"]["api_key"] = mask_key(cfg["ai"]["api_key"])
    return jsonify({
        "config": cfg,
        "rules": read_json(RULES_PATH, {"rules": []}),
        "buyers": read_json(BUYERS_PATH, {"blacklist": [], "overrides": {}}),
    })


@app.route("/api/status", methods=["GET"])
def get_status():
    """机器人运行状态：读心跳文件，配合端口检测"""
    import socket
    import time as _time

    hb = read_json(HEARTBEAT_PATH, {})
    running = False
    try:
        running = (_time.time() - os.path.getmtime(HEARTBEAT_PATH)) < 40  # 40 秒内有心跳即认为运行中
    except Exception:
        running = False

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.2)
    cdp_ok = False
    try:
        cdp_ok = s.connect_ex(("127.0.0.1", 9222)) == 0
    except Exception:
        pass
    finally:
        s.close()

    mode = hb.get("mode", "")
    mode_desc = {"rule": "纯规则", "mixed": "规则优先+AI兜底", "ai": "纯AI"}.get(mode, "")
    return jsonify({
        "robot_running": running,
        "robot_mode": mode_desc,
        "dry_run": hb.get("dry_run", False),
        "review_enabled": hb.get("review_enabled", False),
        "page_error": hb.get("page_error", ""),
        "today": hb.get("today", {}),
        "pid": hb.get("pid"),
        "last_log": hb.get("last_log", ""),
        "last_actions": hb.get("last_actions", []),
        "cdp_online": cdp_ok,
    })


@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    """一键启动机器人（正式回复模式，跟随 config.json 当前设置）"""
    body = request.get_json(force=True, silent=True) or {}
    dry_run = bool(body.get("dry_run"))

    # 已在运行则拒绝重复启动
    hb = read_json(HEARTBEAT_PATH, {})
    old = _process_alive(hb.get("pid"))
    if old:
        return jsonify({"ok": False, "msg": f"机器人已在运行（PID {old}），无需重复启动"})

    args = [bot_python(), os.path.join(BASE_DIR, "bot.py")]
    args.append("--dry-run" if dry_run else "--no-dry-run")
    try:
        logf = open(BOT_LOG_PATH, "a", encoding="utf-8")
        logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 网页启动 (dry_run={dry_run}) =====\n")
        logf.flush()
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                                creationflags=creation, close_fds=True)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"启动失败：{e}"})

    with open(BOT_PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    time.sleep(2)
    return jsonify({"ok": True, "msg": f"机器人已启动（PID {proc.pid}）",
                    "dry_run": dry_run, "pid": proc.pid})


@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    """停止机器人（网页启动的进程，或心跳记录中的进程）"""
    targets = []
    try:
        with open(BOT_PID_FILE, "r", encoding="utf-8") as f:
            targets.append(int(f.read().strip()))
    except Exception:
        pass
    hb = read_json(HEARTBEAT_PATH, {})
    try:
        targets.append(int(hb.get("pid")))
    except (TypeError, ValueError):
        pass

    killed, skipped = [], []
    for pid in set(targets):
        if _process_alive(pid):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                               capture_output=True, timeout=10)
                killed.append(pid)
            except Exception as e:
                skipped.append(f"{pid}({e})")
    # 清理 PID 记录与心跳（让状态栏立即显示未运行）
    try:
        os.remove(BOT_PID_FILE)
    except OSError:
        pass
    try:
        os.remove(HEARTBEAT_PATH)
    except OSError:
        pass
    msg = f"已停止 {len(killed)} 个进程：{killed or '无'}"
    if skipped:
        msg += f"；失败：{skipped}"
    if not killed and not skipped:
        msg = "当前没有运行中的机器人"
    return jsonify({"ok": True, "msg": msg, "killed": killed})


def _process_alive(pid):
    """判断进程是否存活"""
    try:
        import ctypes
        # Windows 下用 OpenProcess 探测；非 Windows 用 os.kill(pid, 0)
        if os.name == "nt":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return 0
            ctypes.windll.kernel32.CloseHandle(handle)
            return int(pid)
        os.kill(int(pid), 0)
        return int(pid)
    except Exception:
        return 0


def bot_python():
    """选择能运行 bot.py 的解释器：优先使用项目 venv（父目录 venv），否则回退当前进程的解释器"""
    venv_py = os.path.join(os.path.dirname(BASE_DIR), "venv", "Scripts", "python.exe")
    if os.path.isfile(venv_py):
        return venv_py
    return sys.executable


@app.route("/api/save", methods=["POST"])
def save():
    body = request.get_json(force=True)
    config = body.get("config") or {}
    rules = body.get("rules") or {}
    buyers = body.get("buyers")

    # 基本校验
    if config.get("mode") not in ("rule", "mixed", "ai"):
        config["mode"] = "mixed"
    if not isinstance(rules.get("rules"), list):
        return jsonify({"ok": False, "msg": "规则必须是数组"}), 400
    for r in rules["rules"]:
        r.setdefault("desc", "")
        r.setdefault("reply", "")
        if isinstance(r.get("keywords"), list):
            r["keywords"] = [str(k).strip() for k in r["keywords"] if str(k).strip()]
        else:
            r["keywords"] = []

    # API Key 脱敏保护：网页回显的是掩码，若用户未修改（仍含 *）则沿用旧 Key
    if isinstance(config.get("ai"), dict):
        new_key = (config["ai"].get("api_key") or "").strip()
        if new_key and "*" in new_key:
            old_cfg = read_json(CONFIG_PATH)
            old_key = ((old_cfg.get("ai") or {}).get("api_key") or "").strip()
            config["ai"]["api_key"] = old_key
        # 新配置字段约束
        config["review_mode"] = bool(config.get("review_mode"))
        try:
            config["cooldown_seconds"] = max(0, int(config.get("cooldown_seconds") or 120))
        except (TypeError, ValueError):
            config["cooldown_seconds"] = 120
        # 商品知识库：数组，每项 {match 关键词逗号串, info 文本}，过滤空 info
        kb = config.get("product_kb")
        if isinstance(kb, list):
            cleaned = []
            for e in kb:
                if isinstance(e, dict) and str(e.get("info") or "").strip():
                    cleaned.append({
                        "match": str(e.get("match") or "").strip(),
                        "info": str(e.get("info") or "").strip(),
                    })
            config["product_kb"] = cleaned
        else:
            config["product_kb"] = config.get("product_kb") or []
        config.pop("shop_info", None)   # 旧全局知识库字段已废弃
        # 网盘发货资源库：数组，每项 {match 关键词逗号串, content 发货文本}，过滤空 content
        dk = config.get("delivery_kb")
        if isinstance(dk, list):
            cleaned_d = []
            for e in dk:
                if isinstance(e, dict) and str(e.get("content") or "").strip():
                    cleaned_d.append({
                        "match": str(e.get("match") or "").strip(),
                        "content": str(e.get("content") or "").strip(),
                    })
            config["delivery_kb"] = cleaned_d
        else:
            config["delivery_kb"] = []

    # 买家策略（独立文件）
    if buyers is not None:
        bl = [str(x).strip() for x in (buyers.get("blacklist") or []) if str(x).strip()]
        ov = {}
        for k, v in (buyers.get("overrides") or {}).items():
            k = str(k).strip()
            v = str(v).strip()
            if k and v in ("skip", "rule", "mixed", "ai"):
                ov[k] = v
        write_json(BUYERS_PATH, {"_说明": read_json(BUYERS_PATH, {}).get("_说明", ""),
                                 "blacklist": bl, "overrides": ov})

    write_json(CONFIG_PATH, config)
    write_json(RULES_PATH, rules)
    return jsonify({"ok": True, "msg": "保存成功（机器人运行中会自动热重载）"})


@app.route("/api/test-ai", methods=["POST"])
def test_ai():
    body = request.get_json(force=True) or {}
    key = (body.get("api_key") or "").strip()
    # 掩码未修改时沿用已保存的 Key
    if key and "*" in key:
        key = ((read_json(CONFIG_PATH).get("ai") or {}).get("api_key") or "").strip()
    cfg = {
        "base_url": body.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": key,
        "model": (body.get("model") or "doubao-lite-32k").strip(),
        "timeout": int(body.get("timeout") or 30),
    }
    ai = DoubaoAI(cfg)
    if not ai.is_ready():
        return jsonify({"ok": False, "msg": "API Key 未填写或仍是占位符，请先在方舟控制台创建 Key"})
    reply = ai.chat("用一句简短的自然中文回复。", "你好，测试一下连通性")
    if reply:
        return jsonify({"ok": True, "msg": "连接成功！AI 回复：" + reply})
    return jsonify({"ok": False, "msg": "连接失败：请检查 Key/模型名/网络，详见启动窗口或上方弹出的错误信息"})


# ---------------- 回复审计台账 ----------------

@app.route("/api/reply-log", methods=["GET"])
def reply_log():
    """返回最近 N 条回复审计记录（倒序，最新在前）"""
    limit = int(request.args.get("limit", 50))
    limit = max(1, min(limit, 200))
    lines = []
    try:
        with open(REPLY_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except FileNotFoundError:
        pass
    out = []
    for line in reversed(lines):   # 最新在前
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return jsonify({"items": out, "total_lines": len(lines)})


# ---------------- 半自动审核队列 ----------------

@app.route("/api/pending", methods=["GET"])
def pending_list():
    items = read_json(PENDING_PATH, [])
    return jsonify({"items": items})


@app.route("/api/pending/decide", methods=["POST"])
def pending_decide():
    body = request.get_json(force=True) or {}
    pid = str(body.get("id") or "")
    action = body.get("action")   # confirm / reject
    if not pid or action not in ("confirm", "reject"):
        return jsonify({"ok": False, "msg": "参数错误"}), 400
    items = read_json(PENDING_PATH, [])
    found = False
    for it in items:
        if str(it.get("id")) == pid and it.get("status") == "pending":
            it["status"] = "approved" if action == "confirm" else "rejected"
            found = True
            break
    if not found:
        return jsonify({"ok": False, "msg": "未找到该待确认项（可能已被处理）"})
    write_json(PENDING_PATH, items)
    return jsonify({"ok": True, "msg": "已确认发送" if action == "confirm" else "已放弃该回复"})


# ---------------- 规则 / AI 在线测试器 ----------------

@app.route("/api/dry-test", methods=["POST"])
def dry_test():
    """模拟一条买家消息：按当前配置返回命中规则或 AI 拟回复（不连接闲鱼页面，不发送）"""
    body = request.get_json(force=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "msg": "请输入模拟的买家消息"}), 400
    try:
        from bot import generate_reply, load_rules, select_kb
        rules = load_rules()
        cfg = read_json(CONFIG_PATH)
        ai = DoubaoAI(cfg.get("ai") or {})
        # 无商品上下文时使用默认知识库条目
        kb_info, _ = select_kb("", cfg.get("product_kb") or [])
        reply, desc = generate_reply(cfg.get("mode") or "mixed", rules, ai, text, [],
                                     shop_info=kb_info)
        if reply is None:
            return jsonify({"ok": True, "desc": desc, "reply": None,
                            "msg": f"不回复（{desc}）"})
        return jsonify({"ok": True, "desc": desc, "reply": reply,
                        "msg": f"回复来源：{desc}"})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"测试失败：{e}"})


# ---------------- 日志滚动清理 ----------------

@app.route("/api/cleanup", methods=["POST"])
def cleanup():
    """截断机器人运行日志与审计台账（保留最近 500 行），清理失效会话状态"""
    done = []
    for path, keep in ((BOT_LOG_PATH, 500), (REPLY_LOG_PATH, 500)):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            if len(lines) > keep:
                lines = lines[-keep:]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            done.append(os.path.basename(path))
        except FileNotFoundError:
            continue
    # state.json 清理：只保留月内活跃会话
    try:
        state_path = os.path.join(BASE_DIR, "state.json")
        if os.path.exists(state_path):
            st = read_json(state_path, {})
            cutoff = time.time() - 30 * 86400
            alive = {}
            for k, v in st.items():
                # 会话类型状态才参与清理
                if isinstance(v, dict) and (v.get("last_checked_at") or 0) >= cutoff:
                    alive[k] = v
                elif not isinstance(v, dict):
                    alive[k] = v
            write_json(state_path, alive)
            done.append("state.json")
    except Exception:
        pass
    msg = "已清理：" + "、".join(done) if done else "暂无日志可清理"
    return jsonify({"ok": True, "msg": msg})


if __name__ == "__main__":
    print("配置中心已启动，请用浏览器打开：http://127.0.0.1:5000")
    print(f"配置文件位于：{CONFIG_PATH} 与 {RULES_PATH}")
    app.run(host="127.0.0.1", port=5000, debug=False)

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
import sys

from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # xianyu-bot 根目录
sys.path.insert(0, BASE_DIR)  # 使上级目录的 ai_client 可被导入

from ai_client import DoubaoAI

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
RULES_PATH = os.path.join(BASE_DIR, "rules.json")

app = Flask(__name__)
app.json.ensure_ascii = False  # 接口返回的中文不转义


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({"config": read_json(CONFIG_PATH), "rules": read_json(RULES_PATH, {"rules": []})})


@app.route("/api/save", methods=["POST"])
def save():
    body = request.get_json(force=True)
    config = body.get("config") or {}
    rules = body.get("rules") or {}

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

    write_json(CONFIG_PATH, config)
    write_json(RULES_PATH, rules)
    return jsonify({"ok": True, "msg": "保存成功"})


@app.route("/api/test-ai", methods=["POST"])
def test_ai():
    body = request.get_json(force=True) or {}
    cfg = {
        "base_url": body.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": (body.get("api_key") or "").strip(),
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


if __name__ == "__main__":
    print("配置中心已启动，请用浏览器打开：http://127.0.0.1:5000")
    print(f"配置文件位于：{CONFIG_PATH} 与 {RULES_PATH}")
    app.run(host="127.0.0.1", port=5000, debug=False)
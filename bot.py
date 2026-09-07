# -*- coding: utf-8 -*-
"""
闲鱼卖家客服 - 自动回复机器人（规则版 + 豆包AI）
--------------------------------------------------
功能：
1. 连接「闲鱼卖家客服」的 CDP 调试端口
2. 轮询会话列表，检测新消息（会话最后消息发生变化）
3. 点开会话读取最新一条买家消息
4. 生成回复：mode 决定策略（rule/mixed/ai，优先级和命令行列外）
5. 自动填入输入框并发送 / 试运行只检测
启动参数：
  --dry-run    试运行（只检测不回发，默认）
  --no-dry-run 真实自动发送
  --mode rule|mixed|ai   临时覆盖 config.json 的模式
用法：
  venv\\Scripts\\python.exe bot.py --dry-run            # 试运行
  venv\\Scripts\\python.exe bot.py --no-dry-run         # 真实回复（规则优先，AI兜底）
  venv\\Scripts\\python.exe bot.py --no-dry-run --mode ai   # 真实回复（全部AI生成）
"""

import json
import os
import sys
import time

from ai_client import DoubaoAI
from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")

# 豆包 AI 的系统提示词（约束其卖家客服口吻）
AI_SYSTEM_PROMPT = (
    "你是一位闲鱼上的店主卖家，正在和买家聊天。请用简短、自然、亲切的中文口语回复买家，"
    "控制在10~60字。不要提及你是AI、机器人或助手；不要使用感叹号堆叠和营销套话；"
    "根据买家意图（咨询商品/物流发货/售后退款/议价等）给出实在的答复，"
    "不确定时可以礼貌询问清楚。"
)

# 系统卡片/提醒消息关键词：命中的消息不自动回复（避免对系统模板说话）
SYSTEM_SKIP_KEYWORDS = [
    "去评价", "我完成了评价", "期待你的评价", "快给ta一个评价吧",
    "说说这次的交易体验", "交易成功", "评价已给出", "对方已评价",
    "卖家已发货", "您的宝贝已发货", "订单已", "提醒您",
]

# ---------------- JS 代码片段 ----------------

JS_READ_SESSIONS = r"""
(() => {
  const holder = document.querySelector('.rc-virtual-list-holder-inner');
  if (!holder) return [];
  const items = [...holder.children].filter(el => el.className && String(el.className).includes('conversation-item'));
  const out = [];
  items.forEach((el, idx) => {
    const nameDiv = el.querySelector('div[style*="max-width"]');
    const name = nameDiv ? (nameDiv.innerText || '').trim() : '';
    if (!name) return;                       // 过滤分组/标题等无昵称条目
    const parts = (el.innerText || '').replace(/\s+/g, ' ').trim();
    out.push({ idx, name, last: parts.slice(0, 120), active: String(el.className).includes('active') });
  });
  return out;
})()
"""

JS_CLICK_SESSION = r"""
(() => {
  const holder = document.querySelector('.rc-virtual-list-holder-inner');
  if (!holder) return { ok: false };
  const items = [...holder.children].filter(el => el.className && String(el.className).includes('conversation-item'));
  // 精确匹配：昵称 div 文本
  const target = items.find(el => {
    const nameDiv = el.querySelector('div[style*="max-width"]');
    return nameDiv && nameDiv.innerText.trim() === %s;
  });
  if (!target) return { ok: false, msg: '会话不存在' };
  target.click();
  return { ok: true };
})()
"""

JS_READ_LAST_BUYER_MSG = r"""
(() => {
  const rows = [...document.querySelectorAll('[class*="message-row"]')]
    .filter(el => {
      const r = el.getBoundingClientRect();
      return r.top >= 100 && r.bottom <= window.innerHeight - 65 && r.width > 0 && r.height > 0;
    });
  if (!rows.length) return { ok: false, msg: '无可视消息行' };
  rows.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
  const last = rows[0];
  const isMine = !!last.querySelector('[class*="read-status"]');
  const text = (last.innerText || '').trim().replace(/\s+/g, ' ');
  return {
    ok: true,
    text: text.slice(0, 100),
    isMine,
    cls: String(last.className || ''),
    count: rows.length
  };
})()
"""

JS_READ_HISTORY = r"""
(() => {
  const rows = [...document.querySelectorAll('[class*="message-row"]')]
    .filter(el => {
      const r = el.getBoundingClientRect();
      return r.top >= 100 && r.bottom <= window.innerHeight - 65 && r.width > 0 && r.height > 0;
    })
    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  return rows.slice(-6).map(el => ({
    from: !!el.querySelector('[class*="read-status"]') ? '卖家' : '买家',
    text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80)
  }));
})()
"""

JS_GET_SEND_BTN = r"""
(() => {
  const btns = [...document.querySelectorAll('button')];
  const sb = btns.find(el => {
    const t = (el.innerText || '').replace(/\s+/g, '');
    return t.includes('发送') && !!el.offsetParent;
  });
  if (!sb) return null;
  const r = sb.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), text: (sb.innerText || '').trim() };
})()
"""

JS_FOCUS_TEXTAREA = r"""
(() => {
  const ta = document.querySelector('textarea[placeholder*="Enter"]');
  if (!ta) return { ok: false };
  ta.focus();
  ta.click();
  return { ok: true, focused: document.activeElement === ta };
})()
"""

JS_READ_TEXTAREA = r"""
(() => {
  const ta = document.querySelector('textarea[placeholder*="Enter"]');
  return ta ? ta.value : null;
})()
"""

JS_CLEAR_TEXTAREA = r"""
(() => {
  const ta = document.querySelector('textarea[placeholder*="Enter"]');
  if (ta) ta.value = '';
  return !!ta;
})()
"""


# ---------------- 工具函数 ----------------

def load_rules():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_config():
    path = os.path.join(BASE_DIR, "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def match_rules(rules, text):
    """纯规则匹配：命中返回 (reply, desc)；忽略词表返回 (None, desc)；未命中返回 None"""
    t = text.lower()
    ignores = rules.get("ignore", [])
    if len(t) <= 12:
        for kw in ignores:
            if kw.lower() == t or (len(kw) >= 2 and kw.lower() in t):
                return None, "忽略（确认/感谢类短消息）"
    for rule in rules["rules"]:
        for kw in rule["keywords"]:
            if kw.lower() in t:
                return rule["reply"], rule["desc"]
    return None, "无规则命中"


def generate_reply(mode, rules, ai, text, history):
    """
    根据模式生成回复，返回 (reply_text, source_desc)。
    规则命中/忽略的判断：
      - ignore（确认感谢短消息）命中任何模式都返回 None => 不回复
      - rule 模式：只走规则，未命中 => (default, 兜底)
      - mixed 模式：规则命中用规则；未命中走 AI；AI 失败回退 default
      - ai 模式：全部走 AI；AI 失败回退 default
    """
    # ignore 表优先级最高：短确认消息一律不回
    for kw in rules.get("ignore", []):
        t = text.lower()
        if len(t) <= 12 and (kw.lower() == t or (len(kw) >= 2 and kw.lower() in t)):
            return None, "忽略（确认/感谢类短消息）"

    hit, desc = match_rules(rules, text)   # desc: 命中规则名 或 "无规则命中"

    # 纯规则模式
    if mode == "rule":
        reply = hit or rules["default"]
        return reply, (desc if hit else "兜底话术")

    # 需要 AI 的情况：mode==ai（全走AI） 或 mode==mixed（仅未命中时）
    need_ai = (mode == "ai") or (mode == "mixed" and not hit)
    if need_ai and ai.is_ready():
        context = "\n".join(f"{m['from']}: {m['text']}" for m in history if m.get("text"))
        user_text = f"你和买家的最近对话如下：\n{context}\n\n买家最新消息：{text}\n请以卖家身份回复："
        ai_reply = ai.chat(AI_SYSTEM_PROMPT, user_text)
        if ai_reply:
            return ai_reply, "豆包AI生成"

    # 回退：规则命中优先，否则兜底
    reply = hit or rules["default"]
    return reply, (desc if hit else "兜底话术")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- 主流程 ----------------

def main():
    dry_run = "--dry-run" in sys.argv or "--no-dry-run" not in sys.argv

    # 解析 --mode 参数（临时覆盖 config.json）
    cfg = load_config()
    mode = (cfg.get("mode") or "mixed").lower()
    if "--mode" in sys.argv:
        i = sys.argv.index("--mode")
        if i + 1 < len(sys.argv):
            mode = sys.argv[i + 1].lower()
    if mode not in ("rule", "ai", "mixed"):
        mode = "mixed"

    rules = load_rules()
    state = load_state()
    ai = DoubaoAI(cfg.get("ai") or {})
    c = CDPClient()
    c.connect(url_keyword=URL_KEYWORD)
    mode_desc = {"rule": "纯规则", "ai": "纯AI生成", "mixed": "规则优先+AI兜底"}[mode]
    log(f"已连接「闲鱼卖家客服」工作台页面，模式：{'试运行(只检测不回发)' if dry_run else '正式自动回复'} | {mode_desc}")
    log(f"规则总数：{len(rules['rules'])} 条；AI状态：{'已启用(豆包)' if ai.is_ready() else '未配置Key，未命中规则时将用兜底话术'}")

    last_reset = time.time()

    while True:
        try:
            sessions = c.evaluate(JS_READ_SESSIONS)
            if sessions is None:
                time.sleep(2)
                continue

            # 当前正在查看的会话（保持用户界面体验，回复后切回）
            active_name = ""
            for s in sessions:
                if s["idx"] == 0 and s["active"]:
                    pass
            actives = [s["name"] for s in sessions if s["active"]]
            origin = actives[0] if actives else ""

            # 找出"最后消息"发生变化的会话 => 可能需要回复（含15秒冷却防反复点开）
            now = time.time()
            pending = []
            for s in sessions:
                key = s["name"]
                st = state.setdefault(key, {})     # 确保回写 state
                if st.get("last_list") != s["last"]:
                    st["last_list"] = s["last"]
                    last_check = st.get("last_checked_at") or 0
                    if now - last_check < 15:
                        continue
                    if (st.get("last_fp") or "").startswith("__READ_FAIL@"):
                        continue   # 读取失败/无消息会话：仅当列表内容再次变化才重试
                    pending.append(s)

            if pending:
                log(f"检测到 {len(pending)} 个会话有新消息变化：{[p['name'] for p in pending]}")
                for s in pending:
                    key = s["name"]
                    state.setdefault(key, {})["last_checked_at"] = now
                    # 1. 点开会话
                    r = c.evaluate(JS_CLICK_SESSION % json.dumps(key))
                    if not r.get("ok"):
                        log(f"  [{key}] 无法点开会话，跳过")
                        continue
                    time.sleep(1.5)

                    # 2. 读取最新一条买家消息
                    msg = c.evaluate(JS_READ_LAST_BUYER_MSG)
                    st = state.setdefault(key, {})
                    if not msg.get("ok"):
                        st["last_fp"] = "__READ_FAIL@" + str(int(time.time()))
                        log(f"  [{key}] 读取消息失败（可能无消息/未加载）：{msg.get('msg')}")
                        continue
                    st["last_fp"] = None  # 稍后有有效文本时再写
                    if msg["isMine"]:
                        st["last_fp"] = msg["text"]
                        log(f"  [{key}] 最新消息是自己发的（{msg['text'][:20]}），无需回复")
                        continue

                    text = msg["text"]
                    # 过滤：图片/表情等无文本消息（innerText 只剩昵称）或过短消息
                    if len(text) <= len(key) + 1:
                        st["last_fp"] = text
                        log(f"  [{key}] 最后消息为图片/无文本（{repr(text)}），跳过")
                        continue
                    # 过滤：系统卡片/提醒消息（如评价通知、订单通知卡片）
                    if any(k in text for k in SYSTEM_SKIP_KEYWORDS):
                        st["last_fp"] = text
                        log(f"  [{key}] 系统卡片/提醒消息，跳过：{text[:30]}")
                        continue

                    # 3. 指纹去重：同一条买家消息只回复一次
                    if st.get("last_fp") == text:
                        log(f"  [{key}] 该消息已回复过（{text[:20]}），跳过")
                        continue

                    # 4. 生成回复（mode 决定 规则/AI 策略；ignore 命中返回 None => 不回复）
                    history = c.evaluate(JS_READ_HISTORY) or []
                    reply, desc = generate_reply(mode, rules, ai, text, history)
                    log(f"  [{key}] 买家消息：{text}")
                    if reply is None:
                        log(f"  [{key}] {desc}，不回复")
                        st["last_fp"] = text
                        continue
                    log(f"  [{key}] 回复生成：【{desc}】{reply}")

                    if dry_run:
                        log(f"  [{key}] [试运行] 不发送。正式运行请加 --no-dry-run")
                        st["last_fp"] = text
                        continue

                    # 5. 填写并发送
                    fr = c.evaluate(JS_FOCUS_TEXTAREA)
                    if not fr.get("ok"):
                        log(f"  [{key}] 输入框定位失败，跳过发送")
                        continue
                    time.sleep(0.3)
                    c.insert_text(reply)
                    time.sleep(0.4)
                    val = c.evaluate(JS_READ_TEXTAREA)
                    if val != reply:
                        log(f"  [{key}] 输入框校验失败（现为{val}），放弃本次发送")
                        time.sleep(0.2)
                        c.evaluate(JS_CLEAR_TEXTAREA)
                        continue
                    send = c.evaluate(JS_GET_SEND_BTN)
                    if not send:
                        log(f"  [{key}] 未找到发送按钮")
                        continue
                    log(f"  [{key}] 文本已填入，点击发送按钮 @{send['x']},{send['y']}")
                    c.dispatch_mouse_click(send["x"], send["y"])
                    time.sleep(0.8)
                    left = c.evaluate(JS_READ_TEXTAREA) or ""
                    log(f"  [{key}] 发送完成，输入框剩余：{repr(left)}")
                    state.setdefault(key, {})["last_fp"] = text

                    save_state(state)

                # 6. 恢复原会话查看（若当前查看会话被切走）
                if origin and origin != pending[-1]["name"]:
                    # 重新定位原会话并点击回来（列表可能重排）
                    c.evaluate(JS_CLICK_SESSION % json.dumps(origin))
            else:
                # 无变化时，每 120 秒自动保存一次状态
                if time.time() - last_reset > 120:
                    save_state(state)
                    last_reset = time.time()

            time.sleep(2)
        except KeyboardInterrupt:
            log("收到退出信号，保存状态并退出")
            save_state(state)
            break
        except Exception as e:
            log(f"主循环异常：{e}，3秒后重试…")
            time.sleep(3)


if __name__ == "__main__":
    main()
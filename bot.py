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
6. 配置热重载：config.json / rules.json 被修改后自动生效，无需重启
启动参数：
  --dry-run    试运行（只检测不回发，默认）
  --no-dry-run 真实自动发送
  --mode rule|mixed|ai   临时覆盖 config.json 的模式（指定后热重载不再切换模式）
用法：
  venv\\Scripts\\python.exe bot.py --dry-run            # 试运行
  venv\\Scripts\\python.exe bot.py --no-dry-run         # 真实回复（规则优先，AI兜底）
  venv\\Scripts\\python.exe bot.py --no-dry-run --mode ai   # 真实回复（全部AI生成）
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ai_client import DoubaoAI
from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
RULES_PATH = os.path.join(BASE_DIR, "rules.json")
REPLY_LOG_PATH = os.path.join(BASE_DIR, "reply_log.jsonl")   # 回复审计台账
PENDING_PATH = os.path.join(BASE_DIR, "pending_review.json") # 半自动审核待确认队列
BUYERS_PATH = os.path.join(BASE_DIR, "buyers.json")          # 买家策略（黑名单/单人模式）
MODE_DESC = {"rule": "纯规则", "ai": "纯AI生成", "mixed": "规则优先+AI兜底"}

# 豆包 AI 的系统提示词（约束其卖家客服口吻）
AI_SYSTEM_PROMPT = (
    "你是一位闲鱼上的店主卖家，正在和买家聊天。请用简短、自然、亲切的中文口语回复买家，"
    "控制在10~60字。不要提及你是AI、机器人或助手；不要使用感叹号堆叠和营销套话；"
    "根据买家意图（咨询商品/物流发货/售后退款/议价等）给出实在的答复，"
    "不确定时可以礼貌询问清楚。"
    "注意：聊天记录中每条消息行首的名字是发送方的昵称，不是商品名也不是店铺名，"
    "请完全无视昵称，只依据名字后面的实际消息内容理解意图；"
    "切勿把买方或卖方的昵称当作商品名称，也不要在回复中提及昵称。"
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

JS_READ_PRODUCT = r"""
(() => {
  // 右侧「咨询过的宝贝/当前宝贝」卡片：读取正在售卖的宝贝标题与价格
  const all = [...document.querySelectorAll('*')];
  let anchor = null;
  for (const tn of ['咨询过的宝贝', '当前宝贝']) {
    anchor = all.find(el => el.children.length === 0 && (el.innerText || '').trim() === tn);
    if (anchor) break;
  }
  if (!anchor) return { ok: false, msg: '未找到宝贝tab' };
  let tab = anchor;
  for (let i = 0; i < 6 && tab; i++) {
    if (String(tab.className || '').includes('ant-tabs ')) break;
    tab = tab.parentElement;
  }
  if (!tab || !String(tab.className || '').includes('ant-tabs ')) return { ok: false, msg: '未找到tabs容器' };
  const texts = [];
  tab.querySelectorAll('*').forEach(ch => {
    if (ch.children.length === 0) {
      const t = (ch.innerText || '').trim();
      const r = ch.getBoundingClientRect();
      if (t && r.width > 0 && r.height > 0) texts.push(t);
    }
  });
  return { ok: true, texts };
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

# ---- 订单面板（自动发货用）：右侧订单 tabs 与面板 ----

JS_CLICK_ORDER_TAB = r"""
(() => {
  // 点击「待发货」订单 tab（文本可能带数量，如 待发货(2)）
  const el = [...document.querySelectorAll('*')].find(e =>
    e.children.length === 0 && (e.innerText || '').trim().startsWith('待发货'));
  if (!el) return { ok: false };
  el.click();
  return { ok: true };
})()
"""

JS_READ_ORDER_PANE = r"""
(() => {
  // 定位「待发货」tab 所在的订单 tabs 容器，读取其激活面板文本（空态为「没有查询到订单」）
  const tab = [...document.querySelectorAll('*')].find(e =>
    e.children.length === 0 && (e.innerText || '').trim().startsWith('待发货'));
  if (!tab) return { ok: false, msg: '未找到待发货tab' };
  let container = tab;
  for (let i = 0; i < 8 && container; i++) {
    if (String(container.className || '').includes('ant-tabs ')) break;
    container = container.parentElement;
  }
  if (!container || !String(container.className || '').includes('ant-tabs ')) {
    return { ok: false, msg: '未找到订单tabs容器' };
  }
  const active = container.querySelector('.ant-tabs-tabpane-active');
  if (!active) return { ok: false, msg: '未找到激活面板' };
  return { ok: true, text: (active.innerText || '').replace(/\s+/g, ' ').trim() };
})()
"""


# ---------------- 工具函数 ----------------

def load_rules():
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def file_mtime(path):
    """取文件最后修改时间；文件不存在/不可访问返回 0"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


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


# ---------------- 心跳（供配置页展示运行状态） ----------------

HEARTBEAT_PATH = os.path.join(BASE_DIR, "bot_heartbeat.json")
_last_logs = []          # 最近日志（供心跳携带给配置页）
_last_actions = []       # 最近一次业务动作关键词
_health = {"page_error": ""}   # 页面健康状态（会话列表读取失败等）
_stats = {"date": "", "replies": 0, "ai": 0, "rule": 0}   # 今日统计


def log(msg):
    global _last_logs
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    _last_logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    if len(_last_logs) > 5:
        _last_logs.pop(0)


def set_action(action):
    global _last_actions
    _last_actions.append(action)
    if len(_last_actions) > 3:
        _last_actions.pop(0)


def _stats_today():
    """保证统计按天重置"""
    global _stats
    d = time.strftime("%Y-%m-%d")
    if _stats["date"] != d:
        _stats = {"date": d, "replies": 0, "ai": 0, "rule": 0}
    return _stats


def audit_reply(session, buyer_text, reply, desc, status="已发送"):
    """回复审计台账：追加一条记录到 reply_log.jsonl 并更新今日统计"""
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "session": session,
        "buyer_text": (buyer_text or "")[:80],
        "reply": (reply or "")[:200],
        "desc": desc,
        "status": status,
    }
    try:
        with open(REPLY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    if status == "已发送":
        st = _stats_today()
        st["replies"] += 1
        if "AI" in (desc or ""):
            st["ai"] += 1
        else:
            st["rule"] += 1


def load_pending():
    try:
        with open(PENDING_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_pending(items):
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def add_pending(session, buyer_text, reply, desc):
    """半自动模式：把待确认回复写入队列，等网页确认"""
    items = load_pending()
    items.append({
        "id": f"{int(time.time() * 1000)}-{len(items)}",
        "session": session,
        "buyer_text": (buyer_text or "")[:80],
        "reply": reply,
        "desc": desc,
        "created_at": time.strftime("%m-%d %H:%M:%S"),
        "status": "pending",
    })
    save_pending(items)


def load_buyers():
    """买家策略：{"blacklist": ["昵称"...], "overrides": {"昵称": "skip|rule|mixed|ai"}}"""
    try:
        with open(BUYERS_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        data = {}
    return {
        "blacklist": [str(x) for x in (data.get("blacklist") or [])],
        "overrides": {str(k): str(v) for k, v in (data.get("overrides") or {}).items()},
    }


def update_heartbeat(mode, dry_run, review_enabled=False):
    """写入心跳文件：配置中心读取它判断机器人是否在运行"""
    try:
        st = _stats_today()
        data = {
            "pid": os.getpid(),
            "mode": mode,
            "dry_run": bool(dry_run),
            "review_enabled": bool(review_enabled),
            "page_error": _health.get("page_error", ""),
            "today": {"replies": st["replies"], "ai": st["ai"], "rule": st["rule"]},
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_log": _last_logs[-1] if _last_logs else "",
            "last_actions": list(_last_actions),
        }
        with open(HEARTBEAT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
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


def strip_nick(text, nick):
    """去掉消息文本开头的发送方昵称（如『八仙曹國舅 你好』→『你好』），避免昵称被误当商品名"""
    if nick and text and text.startswith(nick):
        return text[len(nick):].strip()
    return text


def parse_product(prod_raw):
    """
    从商品卡片的可见文本列表解析 (title, price, tag)。
    文本格式如：["在线", "【直冲…】豆包会员 30天月", "｜", "¥", "13.99", "2人小刀价"]
    """
    if not prod_raw or not prod_raw.get("ok"):
        return None
    texts = prod_raw.get("texts") or []
    # 价格：'¥' 后面的数字（在原始文本里找，避免被过滤）
    price = ""
    for i, t in enumerate(texts):
        if t == "¥" and i + 1 < len(texts):
            price = texts[i + 1].strip()
            break
    junk = {"当前宝贝", "收藏宝贝", "咨询过的宝贝", "在线", "离线", "｜"}
    parts = [t for t in texts if t not in junk]
    if not parts:
        return None
    title = ""
    tag = ""
    for t in parts:
        if t in ("¥", "｜") or (price and t == price):
            continue
        if t and t != title and len(t) > len(title):
            title = t
    for t in parts:
        if "小刀" in t and t != title:
            tag = t
    if not title:
        return None
    return {"title": title, "price": price, "tag": tag}


def select_kb(product_title, kb_entries):
    """
    按商品标题从知识库列表选择专属须知。
    kb_entries: [{"match": "关键词1,关键词2", "info": "..."}, ...]
    规则：有 match 的条目，标题命中任一关键词即选中（先排在前优先）；
          全部未命中则用 match 为空的默认条目；返回 (info, matched_keywords)
    """
    if not kb_entries:
        return "", []
    title = product_title or ""
    default_info = ""
    for e in kb_entries:
        if not isinstance(e, dict):
            continue
        kws = [k.strip() for k in str(e.get("match") or "").replace("，", ",").split(",") if k.strip()]
        info = str(e.get("info") or "").strip()
        if not kws:
            default_info = info or default_info   # 默认条目（match 留空）
            continue
        if title and any(k.lower() in title.lower() for k in kws):
            return info, kws
    return default_info, []


def delivery_from_cfg(c):
    """发货资源库：[{"match": "商品关键词", "content": "网盘链接+提取码"}, ...]，过滤空 content"""
    kb = c.get("delivery_kb") or []
    return [{"match": str(e.get("match") or "").strip(),
             "content": str(e.get("content") or "").strip()}
            for e in kb
            if isinstance(e, dict) and str(e.get("content") or "").strip()]


def match_delivery(order_text, entries):
    """订单文本命中发货资源库条目 → 返回该条目；未命中返回 None"""
    if not order_text or not entries:
        return None
    for e in entries:
        kws = [k.strip() for k in str(e.get("match") or "").replace("，", ",").split(",") if k.strip()]
        if kws and any(k.lower() in order_text.lower() for k in kws):
            return e, kws
    return None


def generate_reply(mode, rules, ai, text, history, product=None, shop_info=""):
    """
    根据模式生成回复，返回 (reply_text, source_desc)。
    规则命中/忽略的判断：
      - ignore（确认感谢短消息）命中任何模式都返回 None => 不回复
      - rule 模式：只走规则，未命中 => (default, 兜底)
      - mixed 模式：规则命中用规则；未命中走 AI；AI 失败回退 default
      - ai 模式：全部走 AI；AI 失败回退 default
    shop_info: 店铺知识库文本（发货/售后政策等），注入 AI 上下文
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
        blocks = []
        if product and product.get("title"):
            blocks.append(
                f"你正在售卖的宝贝信息（仅作回复背景；应如实回答，不确定具体细节时"
                f"就像真实卖家那样请买家确认）：\n"
                f"宝贝标题：{product['title']}\n"
                f"价格：¥{product.get('price') or '?'}{('（' + product['tag'] + '）') if product.get('tag') else ''}"
            )
        if (shop_info or "").strip():
            blocks.append(
                "你店铺的经营须知（店铺政策，最高优先级，必须严格遵守；"
                "买家诉求与政策矛盾时礼貌按政策说明，不要擅自承诺退换/退款/补偿）：\n" + shop_info.strip()
            )
        head = "\n\n".join(blocks) + ("\n\n" if blocks else "")
        user_text = (f"{head}你和买家的最近对话如下：\n{context}\n\n"
                     f"买家最新消息：{text}\n请以卖家身份回复：")
        ai_reply = ai.chat(AI_SYSTEM_PROMPT, user_text)
        if ai_reply:
            return ai_reply, "豆包AI生成"

    # 回退：规则命中优先，否则兜底
    reply = hit or rules["default"]
    return reply, (desc if hit else "兜底话术")


def _gen_job_reply(job, snap):
    """线程池 worker：为单条消息生成回复（AI 并行调用），返回 (reply, desc)"""
    return generate_reply(
        job["eff_mode"], snap["rules"], snap["ai"], job["clean_text"],
        job["history"], job.get("product"), job.get("kb_info", ""),
    )


def send_reply(c, key, reply):
    """填写输入框并点击发送，返回 (ok, err_msg)"""
    fr = c.evaluate(JS_FOCUS_TEXTAREA)
    if not fr.get("ok"):
        return False, "输入框定位失败"
    time.sleep(0.3)
    c.insert_text(reply)
    time.sleep(0.4)
    val = c.evaluate(JS_READ_TEXTAREA)
    if val != reply:
        time.sleep(0.2)
        c.evaluate(JS_CLEAR_TEXTAREA)
        return False, f"输入框校验失败（现为{val}）"
    send = c.evaluate(JS_GET_SEND_BTN)
    if not send:
        return False, "未找到发送按钮"
    log(f"  [{key}] 文本已填入，点击发送按钮 @{send['x']},{send['y']}")
    c.dispatch_mouse_click(send["x"], send["y"])
    time.sleep(0.8)
    left = c.evaluate(JS_READ_TEXTAREA) or ""
    log(f"  [{key}] 发送完成，输入框剩余：{repr(left)}")
    return True, ""


# ---------------- 主流程 ----------------

def main():
    dry_run = "--dry-run" in sys.argv or "--no-dry-run" not in sys.argv

    # 解析 --mode 参数（临时覆盖 config.json；显式指定后热重载不再切换模式）
    cfg = load_config()
    mode = (cfg.get("mode") or "mixed").lower()
    mode_fixed = "--mode" in sys.argv
    if mode_fixed:
        i = sys.argv.index("--mode")
        if i + 1 < len(sys.argv):
            mode = sys.argv[i + 1].lower()
    if mode not in MODE_DESC:
        mode = "mixed"

    rules = load_rules()
    state = load_state()
    ai = DoubaoAI(cfg.get("ai") or {})

    def kb_from_cfg(c):
        """读取商品知识库；兼容旧的全局 shop_info 字段（自动转默认条目）"""
        kb = c.get("product_kb") or []
        if not kb and (c.get("shop_info") or "").strip():
            kb = [{"match": "", "info": c["shop_info"]}]
        return [e for e in kb if isinstance(e, dict) and str(e.get("info") or "").strip()]

    kb_entries = kb_from_cfg(cfg)                  # 商品知识库（按商品匹配）
    delivery_entries = delivery_from_cfg(cfg)       # 网盘发货资源库
    cooldown = max(0, int(cfg.get("cooldown_seconds") or 120))  # 同会话冷却
    buyers = load_buyers()                          # 买家策略
    cfg_mtime = file_mtime(CONFIG_PATH)      # 热重载：记录启动时的文件时间戳
    rules_mtime = file_mtime(RULES_PATH)
    buyers_mtime = file_mtime(BUYERS_PATH)
    c = CDPClient()
    c.connect(url_keyword=URL_KEYWORD)
    log(f"已连接「闲鱼卖家客服」工作台页面，模式：{'试运行(只检测不回发)' if dry_run else '正式自动回复'} | {MODE_DESC[mode]}")
    log(f"规则总数：{len(rules['rules'])} 条；AI状态：{'已启用(豆包)' if ai.is_ready() else '未配置Key，未命中规则时将用兜底话术'}")
    kb_desc = f"{len(kb_entries)} 条商品知识库" + (f"（专版 {sum(1 for e in kb_entries if (e.get('match') or '').strip())} + 默认 {sum(1 for e in kb_entries if not (e.get('match') or '').strip())}）" if kb_entries else "")
    log(f"半自动审核：{'开启（AI回复待网页确认后发送）' if cfg.get('review_mode') else '关闭'}；同会话冷却 {cooldown}s；黑名单 {len(buyers['blacklist'])} 人")
    log(f"商品知识库：{kb_desc or '未配置'}")
    log(f"网盘发货资源：{len(delivery_entries)} 条{'（订单状态自动触发）' if delivery_entries else '（未配置，不自动发货）'}")
    log("配置热重载已开启：网页/MCP 修改配置后自动生效，无需重启")

    last_reset = time.time()
    last_page_err = 0        # 会话列表连续读取失败起始时间（用于自动重连）
    order_scan_idx = 0       # 发货订单巡检轮转指针
    order_scan_at = 0        # 上次巡检时间戳

    def config_snapshot():
        """当前有效配置的只读快照（供回复流程使用）"""
        return {
            "cfg": cfg, "mode": mode, "rules": rules, "ai": ai,
            "kb_entries": kb_entries, "delivery_entries": delivery_entries,
            "cooldown": cooldown, "buyers": buyers,
        }

    def check_order_delivery(key):
        """巡检指定会话买家的「待发货」订单：命中发货资源库则自动发送（不重复发）"""
        snap = config_snapshot()
        entries = snap["delivery_entries"]
        if not entries or dry_run:
            return
        r = c.evaluate(JS_CLICK_SESSION % json.dumps(key))
        if not r.get("ok"):
            return
        time.sleep(1.2)
        c.evaluate(JS_CLICK_ORDER_TAB)
        time.sleep(1.0)
        pane = c.evaluate(JS_READ_ORDER_PANE)
        if not pane or not pane.get("ok"):
            return
        text = (pane.get("text") or "").strip()
        if not text or "没有查询到订单" in text:
            return   # 该买家当前无待发货订单
        hit, kws = match_delivery(text, entries)
        if not hit:
            return   # 订单商品不在发货资源库中
        st = state.setdefault(key, {})
        fp = text[:120]          # 订单指纹：面板文本前120字
        if st.get("last_order_fp") == fp:
            return               # 该订单已发过资源
        log(f"  [{key}] [自动发货] 检测到待发货订单，命中资源「{'、'.join(kws)}」")
        ok, err = send_reply(c, key, hit["content"])
        if not ok:
            log(f"  [{key}] [自动发货] 发送失败：{err}（下轮重试）")
            return
        st["last_order_fp"] = fp
        st["last_reply_at"] = time.time()
        audit_reply(key, "", hit["content"], "网盘自动发货")
        set_action(f"自动发货：{key}")
        save_state(state)
        log(f"  [{key}] [自动发货] 已完成，资源已发送")

    def process_pending_reviews():
        """处理网页端已确认/已拒绝的待审核回复"""
        items = load_pending()
        changed = False
        for it in items:
            if it.get("status") == "approved":
                key = it["session"]
                log(f"  [{key}] [审核] 网页已确认，发送：{it['reply'][:40]}")
                r = c.evaluate(JS_CLICK_SESSION % json.dumps(key))
                if not r.get("ok"):
                    log(f"  [{key}] [审核] 无法点开会话，稍后重试")
                    continue
                time.sleep(1.5)
                ok, err = send_reply(c, key, it["reply"])
                if ok:
                    audit_reply(key, it.get("buyer_text", ""), it["reply"], it.get("desc", ""))
                    state.setdefault(key, {})["last_reply_at"] = time.time()
                    save_state(state)
                    log(f"  [{key}] [审核] 发送成功")
                else:
                    log(f"  [{key}] [审核] 发送失败：{err}，退回待确认列表")
                    continue
                it["_done"] = True
            elif it.get("status") == "rejected":
                audit_reply(it["session"], it.get("buyer_text", ""), it["reply"],
                            it.get("desc", ""), status="已拒绝")
                log(f"  [{it['session']}] [审核] 网页已拒绝该回复")
                it["_done"] = True
        if any(it.get("_done") for it in items):
            save_pending([it for it in items if not it.get("_done")])

    while True:
        try:
            # ---- 处理审核确认（半自动模式） ----
            process_pending_reviews()

            # ---- 配置热重载：config.json / rules.json / buyers.json 变化后自动生效 ----
            m = file_mtime(CONFIG_PATH)
            if m and m != cfg_mtime:
                new_cfg = load_config()
                if new_cfg:
                    cfg_mtime = m
                    cfg = new_cfg
                    # 模式切换（命令行显式指定 --mode 时以命令行为准）
                    if not mode_fixed:
                        new_mode = (cfg.get("mode") or "mixed").lower()
                        if new_mode not in MODE_DESC:
                            new_mode = "mixed"
                        if new_mode != mode:
                            mode = new_mode
                            log(f"[热重载] 模式切换为：{MODE_DESC[mode]}")
                    # AI 配置变化时重建客户端
                    new_ai = DoubaoAI(cfg.get("ai") or {})
                    if (new_ai.base_url, new_ai.api_key, new_ai.model, new_ai.timeout) != \
                       (ai.base_url, ai.api_key, ai.model, ai.timeout):
                        ai = new_ai
                        log(f"[热重载] AI 配置已更新（{'已启用' if ai.is_ready() else '未配置Key'}）")
                    # 知识库 / 冷却 / 审核模式 / 发货资源
                    kb_entries = kb_from_cfg(cfg)
                    delivery_entries = delivery_from_cfg(cfg)
                    cooldown = max(0, int(cfg.get("cooldown_seconds") or 120))
                    log(f"[热重载] 半自动审核{'开启' if cfg.get('review_mode') else '关闭'}，冷却 {cooldown}s，商品知识库 {len(kb_entries)} 条，发货资源 {len(delivery_entries)} 条")
                else:
                    cfg_mtime = 0   # 读取失败（可能正被写入），下一轮重试
            m = file_mtime(RULES_PATH)
            if m and m != rules_mtime:
                try:
                    new_rules = load_rules()
                    rules_mtime = m
                    log(f"[热重载] 规则库已重新加载：{len(new_rules['rules'])} 条规则")
                    rules = new_rules
                except Exception as e:
                    rules_mtime = 0   # 解析失败，下一轮重试
                    log(f"[热重载] rules.json 解析失败（{e}），继续使用旧规则")
            m = file_mtime(BUYERS_PATH)
            if m and m != buyers_mtime:
                buyers_mtime = m
                buyers = load_buyers()
                log(f"[热重载] 买家策略已更新：黑名单 {len(buyers['blacklist'])} 人，单人覆盖 {len(buyers['overrides'])} 人")

            # ---- 会话列表读取（失败则记录健康状态并尝试自动重连） ----
            sessions = c.evaluate(JS_READ_SESSIONS)
            if sessions is None:
                now_err = time.time()
                if not last_page_err:
                    last_page_err = now_err
                    _health["page_error"] = "会话列表读取失败（可能未登录/页面异常）"
                    log("[健康] 会话列表读取失败，30 秒后自动重连…")
                if now_err - last_page_err > 30:
                    log("[健康] 尝试重新连接 CDP…")
                    try:
                        c.close()
                        c.connect(url_keyword=URL_KEYWORD)
                    except Exception as e:
                        log(f"[健康] 重连失败：{e}")
                    last_page_err = now_err
                time.sleep(3)
                continue
            if last_page_err:
                last_page_err = 0
                _health["page_error"] = ""
                log("[健康] 会话列表恢复正常")
            if not sessions:
                _health["page_error"] = "会话列表为空（请检查是否已登录闲鱼）"
            else:
                _health["page_error"] = ""

            snap = config_snapshot()

            # 当前正在查看的会话（保持用户界面体验，回复后切回）
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
                set_action(f"检测到新消息：{pending[0]['name']} 等 {len(pending)} 个会话")
                log(f"检测到 {len(pending)} 个会话有新消息变化：{[p['name'] for p in pending]}")

                # ---- 阶段1：列表预览判定（不点开），筛掉无需点开的会话 ----
                todo = []      # 需点开复核/生成：[{s, st, eff_mode}]
                for s in pending:
                    key = s["name"]
                    st = state.setdefault(key, {})
                    st["last_checked_at"] = now

                    # 买家策略：黑名单 / 单人模式覆盖
                    if key in snap["buyers"]["blacklist"]:
                        st["last_fp"] = s["last"]
                        log(f"  [{key}] 黑名单买家，跳过不回复")
                        continue
                    eff_mode = snap["mode"]
                    override = snap["buyers"]["overrides"].get(key)
                    if override == "skip":
                        st["last_fp"] = s["last"]
                        log(f"  [{key}] 单人策略：不回复（skip），跳过")
                        continue
                    if override in MODE_DESC:
                        eff_mode = override
                        log(f"  [{key}] 单人策略：使用 {MODE_DESC[override]}")

                    # 预览文本（剥离开头昵称）先行廉价过滤：系统卡片 / 图片无文本
                    preview = strip_nick(s["last"], key) or ""
                    if any(k in preview for k in SYSTEM_SKIP_KEYWORDS):
                        st["last_fp"] = s["last"]
                        log(f"  [{key}] 系统卡片/提醒消息，跳过：{preview[:30]}")
                        continue
                    if len(preview) <= 1:
                        st["last_fp"] = s["last"]
                        log(f"  [{key}] 最后消息为图片/无文本，跳过")
                        continue
                    # 指纹去重：同一条消息只回复一次（以列表原文为指纹）
                    if st.get("last_fp") == s["last"]:
                        log(f"  [{key}] 该消息已回复过（{preview[:20]}），跳过")
                        continue
                    # 冷却限流：同一会话冷却期内不连续回复（防连环轰炸/风控）
                    last_reply = st.get("last_reply_at") or 0
                    if snap["cooldown"] > 0 and now - last_reply < snap["cooldown"]:
                        st["last_fp"] = s["last"]
                        log(f"  [{key}] {snap['cooldown']}s 冷却中（距上次回复 {int(now - last_reply)}s），跳过")
                        continue
                    todo.append({"s": s, "st": st, "eff_mode": eff_mode})

                # ---- 阶段2：逐个点开，复核买家消息；规则命中直接定稿，需 AI 的收集上下文 ----
                jobs = []    # 需 AI 并行生成
                ready = []   # 已定稿待发送
                for t in todo:
                    key, st = t["s"]["name"], t["st"]
                    eff_mode = t["eff_mode"]
                    r = c.evaluate(JS_CLICK_SESSION % json.dumps(key))
                    if not r.get("ok"):
                        log(f"  [{key}] 无法点开会话，跳过")
                        continue
                    time.sleep(1.5)

                    # 读取最新一条买家消息（点开后精确复核）
                    msg = c.evaluate(JS_READ_LAST_BUYER_MSG)
                    if not msg.get("ok"):
                        st["last_fp"] = "__READ_FAIL@" + str(int(time.time()))
                        log(f"  [{key}] 读取消息失败（可能无消息/未加载）：{msg.get('msg')}")
                        continue
                    if msg["isMine"]:
                        st["last_fp"] = t["s"]["last"]
                        log(f"  [{key}] 最新消息是自己发的（{msg['text'][:20]}），无需回复")
                        continue
                    raw_text = msg["text"]
                    # 过滤：图片/表情等无文本消息（innerText 只剩昵称）或过短消息
                    if len(raw_text) <= len(key) + 1:
                        st["last_fp"] = t["s"]["last"]
                        log(f"  [{key}] 最后消息为图片/无文本（{repr(raw_text)}），跳过")
                        continue
                    # 过滤：系统卡片/提醒消息（如评价通知、订单通知卡片）
                    if any(k in raw_text for k in SYSTEM_SKIP_KEYWORDS):
                        st["last_fp"] = t["s"]["last"]
                        log(f"  [{key}] 系统卡片/提醒消息，跳过：{raw_text[:30]}")
                        continue

                    # 传给规则/AI 的文本去掉开头昵称，避免昵称被误当成商品名
                    clean_text = strip_nick(raw_text, key)
                    hit, mdesc = match_rules(snap["rules"], clean_text)
                    # ignore（确认/感谢短消息）优先级最高：任何模式都不回复
                    if hit is None and "忽略" in (mdesc or ""):
                        st["last_fp"] = t["s"]["last"]
                        log(f"  [{key}] {mdesc}，不回复")
                        continue
                    if eff_mode == "rule":
                        reply = hit or snap["rules"]["default"]
                        ready.append({"key": key, "s": t["s"], "st": st, "raw_text": raw_text,
                                      "reply": reply, "desc": (mdesc if hit else "兜底话术")})
                        continue
                    if eff_mode == "mixed" and hit:
                        ready.append({"key": key, "s": t["s"], "st": st, "raw_text": raw_text,
                                      "reply": hit, "desc": mdesc})
                        continue

                    # —— 需要 AI：读取历史 / 关联宝贝 / 专属知识库 ——
                    history = c.evaluate(JS_READ_HISTORY) or []
                    for hm in history:
                        if hm.get("text"):
                            hm["text"] = strip_nick(hm["text"], key)
                    product = parse_product(c.evaluate(JS_READ_PRODUCT))
                    if product:
                        log(f"  [{key}] 关联宝贝：{product['title']} ¥{product['price']}{('（' + product['tag'] + '）') if product.get('tag') else ''}")
                    kb_info, kb_kws = select_kb(product.get("title") if product else "", snap["kb_entries"])
                    if kb_info:
                        log(f"  [{key}] 知识库：{'命中商品「' + '、'.join(kb_kws) + '」' if kb_kws else '默认须知'}")
                    jobs.append({"key": key, "s": t["s"], "st": st, "raw_text": raw_text,
                                 "clean_text": clean_text, "history": history,
                                 "product": product, "kb_info": kb_info, "eff_mode": eff_mode})

                # ---- 阶段3：并行 AI 生成（多个会话同时等待，提速明显） ----
                if jobs:
                    log(f"[并行AI] {len(jobs)} 条消息并发生成回复…")
                    replies = {}
                    with ThreadPoolExecutor(max_workers=min(3, len(jobs))) as ex:
                        futs = {ex.submit(_gen_job_reply, j, snap): j for j in jobs}
                        for fut in as_completed(futs):
                            j = futs[fut]
                            try:
                                rp, rd = fut.result()
                            except Exception as e:
                                log(f"  [{j['key']}] AI 生成异常：{e}，回退兜底话术")
                                rp, rd = snap["rules"]["default"], "兜底话术"
                            replies[j["key"]] = (rp, rd)
                    for j in jobs:
                        rp, rd = replies[j["key"]]
                        if rp:
                            ready.append({"key": j["key"], "s": j["s"], "st": j["st"],
                                          "raw_text": j["raw_text"], "reply": rp, "desc": rd})

                # ---- 阶段4：逐个发送（dry-run 只检测 / 半自动入审核队列） ----
                for item in ready:
                    key, st = item["key"], item["st"]
                    log(f"  [{key}] 买家消息：{item['raw_text']}")
                    log(f"  [{key}] 回复生成：【{item['desc']}】{item['reply']}")

                    if dry_run:
                        log(f"  [{key}] [试运行] 不发送。正式运行请加 --no-dry-run")
                        st["last_fp"] = item["s"]["last"]
                        continue

                    # 半自动审核模式：写入待确认队列，由网页决定发送/放弃
                    if snap["cfg"].get("review_mode"):
                        add_pending(key, item["raw_text"], item["reply"], item["desc"])
                        audit_reply(key, item["raw_text"], item["reply"], item["desc"], status="待确认")
                        st["last_fp"] = item["s"]["last"]
                        log(f"  [{key}] [半自动] 已暂停发送，等待网页确认（回复历史可见）")
                        set_action(f"待确认：{key} 的回复")
                        save_state(state)
                        continue

                    # 自动发送
                    ok, err = send_reply(c, key, item["reply"])
                    if not ok:
                        log(f"  [{key}] 发送失败：{err}，跳过（不会重发该消息）")
                        st["last_fp"] = item["s"]["last"]
                        continue
                    st["last_fp"] = item["s"]["last"]
                    st["last_reply_at"] = time.time()
                    audit_reply(key, item["raw_text"], item["reply"], item["desc"])
                    save_state(state)
                    set_action(f"已回复：{key}")

                # 恢复原会话查看（若本次点开/发送过其他会话）
                if origin and (todo or ready):
                    # 重新定位原会话并点击回来（列表可能重排）
                    c.evaluate(JS_CLICK_SESSION % json.dumps(origin))
            else:
                # 无变化时，每 120 秒自动保存一次状态
                if time.time() - last_reset > 120:
                    save_state(state)
                    last_reset = time.time()

                # ---- 发货订单巡检：空闲时轮转检查各会话的待发货订单（自动发送网盘资源） ----
                if sessions and time.time() - order_scan_at >= 45:
                    order_scan_at = time.time()
                    target = sessions[order_scan_idx % len(sessions)]
                    order_scan_idx += 1
                    origin_key = actives[0] if actives else ""
                    check_order_delivery(target["name"])
                    if origin_key and origin_key != target["name"]:
                        # 恢复原会话查看
                        c.evaluate(JS_CLICK_SESSION % json.dumps(origin_key))

            # 心跳：每 10 秒向配置页上报一次运行状态
            if time.time() - getattr(main, "_last_hb", 0) > 10:
                update_heartbeat(mode, dry_run, review_enabled=bool(cfg.get("review_mode")))
                main._last_hb = time.time()

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

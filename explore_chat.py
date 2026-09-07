# -*- coding: utf-8 -*-
"""探索交互：点击会话列表某个会话，输出聊天区结构"""

import json
import sys

from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"

CLICK_FIRST_SESSION = r"""
(() => {
  const items = [...document.querySelectorAll('.rc-virtual-list-holder-inner > *')];
  const target = items.find(el => (el.innerText || '').includes(%s));
  if (!target) return { ok: false, msg: '未找到会话', total: items.length };
  const r = target.getBoundingClientRect();
  target.click();
  return { ok: true, text: target.innerText.replace(/\s+/g, ' ').slice(0, 50), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
})()
"""

DUMP_CHAT = r"""
(() => {
  const out = {};
  out.leftText = document.body.innerText.includes('尚未选择任何联系人') ? '未选中会话' : '已选中会话';
  const inputs = [...document.querySelectorAll('input, textarea, [contenteditable="true"]')]
    .map((el, i) => ({ i, tag: el.tagName, type: el.type || '', ph: el.getAttribute('placeholder') || '', cls: (el.className||'').toString().slice(0,60) }));
  out.inputs = inputs;
  out.buttons = [...document.querySelectorAll('button')]
    .map((el, i) => ({ i, text: (el.innerText||'').trim().slice(0,12), cls: (el.className||'').toString().slice(0,60), aria: el.getAttribute('aria-label')||'', vis: !!(el.offsetParent) }))
    .filter(b => (b.text || b.aria) && b.vis);
  // 候选输入框：靠近发送按钮的 div（可能 contenteditable 或 div）
  const sendBtn = out.buttons.find(b => b.text.includes('发') && b.text.includes('送')) || out.buttons.find(b => b.text === '发' || b.text === '发送');
  if (sendBtn) {
    const btns = [...document.querySelectorAll('button')];
    const sb = btns.find(el => (el.innerText||'').trim().includes('发送') && !!el.offsetParent);
    if (sb) {
      const r = sb.getBoundingClientRect();
      // 向上找输入区：离发送按钮最近的可输入元素
      const cands = [...document.querySelectorAll('[contenteditable], textarea, .input, .editor')]
        .filter(el => { const rr = el.getBoundingClientRect(); return rr.bottom <= r.top + 5 && Math.abs(rr.bottom - r.top) < 200; });
      out.suggestInput = cands.slice(0,5).map(el => ({ tag: el.tagName, cls: (el.className||'').toString().slice(0,60), text: (el.innerText||'').trim().slice(0,30) }));
      out.sendRect = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    }
  }
  return out;
})()
"""

if __name__ == "__main__":
    who = sys.argv[1] if len(sys.argv) > 1 else "万事嘟灵"
    c = CDPClient()
    c.connect(url_keyword=URL_KEYWORD)
    r = c.evaluate(CLICK_FIRST_SESSION % json.dumps(who))
    print("点击结果:", r)
    if r.get("ok"):
        import time
        time.sleep(1.5)
        out = c.evaluate(DUMP_CHAT)
        import json as j
        print(j.dumps(out, ensure_ascii=False, indent=2))
    c.close()
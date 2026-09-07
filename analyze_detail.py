# -*- coding: utf-8 -*-
"""深度分析 v2：会话项完整HTML(找未读徽标) + 消息气泡结构"""

import json

from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"

JS_SESSION_FULL = r"""
(() => {
  const holder = document.querySelector('.rc-virtual-list-holder-inner');
  if (!holder) return { ok: false, msg: 'no list' };
  const items = [...holder.children];
  return {
    total: items.length,
    firstHtml: items[0].outerHTML,
    firstText: (items[0].innerText||'').replace(/\s+/g,' ').slice(0,80)
  };
})()
"""

JS_MSG_DETAIL = r"""
(() => {
  const btns = [...document.querySelectorAll('button')];
  const sendBtn = btns.find(el => { const t=(el.innerText||'').replace(/\s+/g,''); return t.includes('发送') && !!el.offsetParent; });
  if (!sendBtn) return { ok: false, msg: 'no send btn', hasTextarea: !!document.querySelector('textarea') };
  const sendTop = sendBtn.getBoundingClientRect().top;
  const best = {};
  document.querySelectorAll('div').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.bottom >= sendTop - 8) return;
    if (r.width > 500) return;
    const t = (el.innerText||'').trim();
    if (t.length < 2 || t.length > 90) return;
    const key = el.className && (el.className).toString().slice(-50);
    best[t.slice(0,24) + '@' + Math.round(r.y)] = {
      cls: key, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), t: t.slice(0,24), outer: el.outerHTML.slice(0, 400)
    };
  });
  const msgs = Object.values(best).sort((a,b) => a.y - b.y);
  return { ok: true, sendRect: [sendBtn.getBoundingClientRect().x, sendBtn.getBoundingClientRect().y], msgs: msgs.slice(-10) };
})()
"""

if __name__ == "__main__":
    c = CDPClient()
    c.connect(url_keyword=URL_KEYWORD)
    print("=== 会话项完整HTML(第1个) ===")
    r1 = c.evaluate(JS_SESSION_FULL)
    print(json.dumps(r1, ensure_ascii=False, indent=1)[:2500])
    print("=== 消息气泡 ===")
    r2 = c.evaluate(JS_MSG_DETAIL)
    print(json.dumps(r2, ensure_ascii=False, indent=1)[:4000])
    c.close()
# -*- coding: utf-8 -*-
"""定位聊天记录容器：以消息特征为锚点分析方向判断逻辑"""

import json

from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"

JS_FIND_CONTAINER = r"""
(() => {
  const btns = [...document.querySelectorAll('button')];
  const sendBtn = btns.find(el => { const t=(el.innerText||'').replace(/\s+/g,''); return t.includes('发送') && !!el.offsetParent; });
  if (!sendBtn) return { ok:false, msg:'no send btn' };
  const sendTop = sendBtn.getBoundingClientRect().top;

  // 找聊天滚动容器：位于发送钮上方，包含 "已读" 文本的 div
  const readEl = [...document.querySelectorAll('*')].find(el => (el.childElementCount === 0) && (el.textContent||'').trim() === '已读' && el.getBoundingClientRect().width < 120);
  if (!readEl) return { ok:false, msg:'no 已读 marker' };
  let cur = readEl, depth = 0;
  const chain = [];
  while (cur && depth < 8) {
    const r = cur.getBoundingClientRect();
    chain.push({ tag: cur.tagName, cls: (cur.className||'').toString().slice(0,70), w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.y), children: cur.children.length });
    cur = cur.parentElement; depth++;
  }
  return { ok:true, chain };
})()
"""

JS_MSG_ROWS = r"""
(() => {
  const btns = [...document.querySelectorAll('button')];
  const sendBtn = btns.find(el => { const t=(el.innerText||'').replace(/\s+/g,''); return t.includes('发送') && !!el.offsetParent; });
  if (!sendBtn) return { ok:false, msg:'no send btn' };
  const sendTop = sendBtn.getBoundingClientRect().top;
  const rows = [];
  // 候选消息行：高度 30~90、宽度在 聊天区内(>330)、y < sendTop
  document.querySelectorAll('div').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.y < 120 || r.bottom > sendTop - 5) return;
    if (r.x < 330 || r.width > 400 || r.height > 100 || r.height < 24) return;
    const t = (el.innerText||'').trim().replace(/\s+/g,' ');
    if (!t || t.length > 100) return;
    if (el.children.length !== 1) return;   // 叶子级 或 单子元素
    rows.push({ cls:(el.className||'').toString().slice(0,60), x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height), t: t.slice(0,60), outer: el.outerHTML.slice(0,240) });
  });
  // 按 y 分组去重
  const uniq = rows.filter((r, i) => rows.findIndex(o => o.t === r.t && o.y === r.y) === i).sort((a,b)=>a.y-b.y);
  return { ok:true, rows: uniq.slice(-16) };
})()
"""

if __name__ == "__main__":
    c = CDPClient()
    c.connect(url_keyword=URL_KEYWORD)
    print("=== 聊天容器链条 ===")
    print(json.dumps(c.evaluate(JS_FIND_CONTAINER), ensure_ascii=False, indent=1))
    print("=== 消息行候选 ===")
    print(json.dumps(c.evaluate(JS_MSG_ROWS), ensure_ascii=False, indent=1)[:4500])
    c.close()
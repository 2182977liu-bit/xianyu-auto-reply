# -*- coding: utf-8 -*-
"""分析 message-row 结构，确定买卖家方向判断方法"""

import json

from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"

JS = r"""
(() => {
  const visible = [];
  document.querySelectorAll('.message-row--pIWaXNhZ, div[class*="message-row"]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.top > 30 && r.bottom < window.innerHeight - 60) {
      visible.push({ cls: (el.className||'').toString(), t: (el.innerText||'').replace(/\s+/g,' ').slice(0,60), x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), html: el.outerHTML.slice(0, 800) });
    }
  });
  return { total: document.querySelectorAll('[class*="message-row"]').length, visible: visible.slice(0, 8) };
})()
"""

if __name__ == "__main__":
    c = CDPClient()
    c.connect(url_keyword=URL_KEYWORD)
    out = c.evaluate(JS)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    c.close()
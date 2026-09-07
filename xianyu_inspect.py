# -*- coding: utf-8 -*-
"""
界面结构分析工具：连接「消息 - 闲鱼卖家工作台」页面，输出关键 DOM 结构信息
用法：venv\Scripts\python.exe inspect.py [模块]
模块：overview | text | elements
"""

import json
import sys

from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"

JS_OVERVIEW = r"""
(() => {
  const out = { title: document.title, url: location.href, scrollY: window.scrollY, viewport: [window.innerWidth, window.innerHeight] };
  const inputs = [...document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]')]
    .map((el, i) => ({
      i, tag: el.tagName, type: el.type || '', ph: el.getAttribute('placeholder') || '',
      cls: (el.className || '').toString().slice(0, 60), vis: !!(el.offsetParent || el.getClientRects().length)
    }));
  out.inputs = inputs;
  const btns = [...document.querySelectorAll('button, [role="button"]')]
    .map((el, i) => ({
      i, text: (el.innerText || '').trim().slice(0, 20), cls: (el.className || '').toString().slice(0, 60),
      vis: !!(el.offsetParent), aria: el.getAttribute('aria-label') || ''
    }))
    .filter(b => (b.text || b.aria) && b.vis)
    .slice(0, 40);
  out.buttons = btns;
  return out;
})()
"""

JS_TEXT = r"""
(() => {
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = (n.textContent || '').trim();
    if (t) {
      const p = n.parentElement;
      texts.push({ t: t.slice(0, 40), tag: p ? p.tagName : '', cls: p ? (p.className || '').toString().slice(0, 40) : '' });
    }
  }
  return texts.slice(0, 120);
})()
"""

JS_ELEMENTS = r"""
(() => {
  // 寻找可疑的列表容器：子元素数量较多的容器
  const els = [...document.querySelectorAll('div,ul,li,section')].map((el) => ({
    tag: el.tagName,
    cls: (el.className || '').toString().slice(0, 70),
    children: el.children.length,
    text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 50),
    vis: !!(el.offsetParent || el.getClientRects().length),
    rect: (() => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })()
  }))
  .filter(e => e.vis && e.children >= 3 && e.rect[2] > 50 && e.rect[3] > 30)
  .sort((a, b) => b.children - a.children)
  .slice(0, 40);
  return els;
})()
"""


def main():
    mod = sys.argv[1] if len(sys.argv) > 1 else "overview"
    c = CDPClient()
    target = c.connect(url_keyword=URL_KEYWORD)
    print("== 已连接页面:", target.get("title"), target.get("url"))
    if mod == "overview":
        out = c.evaluate(JS_OVERVIEW)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif mod == "text":
        out = c.evaluate(JS_TEXT)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif mod == "elements":
        out = c.evaluate(JS_ELEMENTS)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    c.close()


if __name__ == "__main__":
    main()
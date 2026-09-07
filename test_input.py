# -*- coding: utf-8 -*-
"""
输入能力自测（安全模式：不点发送按钮）
流程：聚焦输入框 -> 插入文本 -> 读取校验 -> 全选删除恢复原状
"""

import time

from cdp_client import CDPClient

URL_KEYWORD = "seller.goofish.com"

FOCUS_TEXTAREA = r"""
(() => {
  const ta = document.querySelector('textarea[placeholder*="按Enter键发送"]');
  if (!ta) return { ok: false, msg: '未找到输入框' };
  ta.focus();
  ta.click();
  return { ok: true, hasFocus: document.activeElement === ta };
})()
"""

READ_VALUE = r"""
(() => {
  const ta = document.querySelector('textarea[placeholder*="按Enter键发送"]');
  return ta ? ta.value : null;
})()
"""

if __name__ == "__main__":
    c = CDPClient()
    c.connect(url_keyword=URL_KEYWORD)
    print("1. 聚焦输入框:", c.evaluate(FOCUS_TEXTAREA))
    time.sleep(0.5)
    print("2. 插入文本 ...")
    c.insert_text("（自动回复测试·勿回）在的哦~")
    time.sleep(0.5)
    v = c.evaluate(READ_VALUE)
    print("3. 输入框当前内容:", repr(v))
    if v:
        # 全选删除，恢复原状（Ctrl+A + Backspace）
        c.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2})
        c.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2})
        c.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
        c.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})
        time.sleep(0.4)
        print("4. 清空后内容:", repr(c.evaluate(READ_VALUE)))
    c.close()
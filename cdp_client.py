# -*- coding: utf-8 -*-
"""
CDP 客户端：通过 Chrome DevTools Protocol 控制 Electron 应用「闲鱼卖家客服」
用法：
    from cdp_client import CDPClient
    c = CDPClient()
    pages = c.list_pages()
    c.connect(url_keyword="xxx")   # 或 connect(page_index=0)
    c.evaluate("document.title")
"""

import json
import urllib.request

import websocket


class CDPClient:
    def __init__(self, port=9222, host="127.0.0.1", timeout=30):
        self.base = f"http://{host}:{port}"
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ws = None
        self._id = 0

    # ---------- 连接管理 ----------

    def list_pages(self):
        """获取 /json 页面列表"""
        with urllib.request.urlopen(f"{self.base}/json", timeout=5) as r:
            return json.loads(r.read())

    def connect(self, page_index=None, url_keyword=None):
        """
        连接目标页面。
        - url_keyword: 按 URL 包含关键字匹配（优先）
        - page_index:   按索引选择（默认为第一个 type=page 的）
        返回目标信息 dict
        """
        pages = self.list_pages()
        target = None
        if url_keyword:
            for p in pages:
                if p.get("type") == "page" and url_keyword in (p.get("url") or ""):
                    target = p
                    break
        if target is None:
            candidates = [p for p in pages if p.get("type") == "page"]
            idx = page_index if page_index is not None else 0
            if idx >= len(candidates):
                raise RuntimeError(f"页面索引越界，可用页面: {len(candidates)}")
            target = candidates[idx]
        if not target.get("webSocketDebuggerUrl"):
            raise RuntimeError("目标没有 webSocketDebuggerUrl，确认是否以 --remote-debugging-port 启动")
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=self.timeout)
        # 开启 Runtime 域，确保网络数据不丢失
        # self.send("Runtime.enable")
        return target

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None

    # ---------- 底层命令 ----------

    def send(self, method, params=None):
        """发送 CDP 命令并等待同步响应"""
        if not self.ws:
            raise RuntimeError("尚未连接，请先调用 connect()")
        self._id += 1
        req_id = self._id
        self.ws.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP 错误: {msg['error']}")
                return msg.get("result", {})
            # 其他消息（事件）忽略

    def evaluate(self, expression, container=None):
        """
        在页面执行 JS，返回 value 值。
        container: 可选 dict，提供 JS 变量（会先 eval 定义再传入）— 简单场景用 JSON 字符串拼进 expression 即可
        """
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        res = result.get("result", {})
        subtype = res.get("subtype")
        if subtype == "error":
            raise RuntimeError(f"JS 执行报错: {res.get('description')}")
        return res.get("value")

    # ---------- 输入模拟 ----------

    def insert_text(self, text):
        """向当前聚焦元素插入文本（模拟真实输入，适合中文）"""
        self.send("Input.insertText", {"text": text})

    def dispatch_mouse_click(self, x, y, button="left", click_count=1):
        """在页面坐标 (x, y) 处模拟真实鼠标点击"""
        self.send(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": button, "clickCount": click_count},
        )
        self.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": button, "clickCount": click_count},
        )

    def key_event(self, key_code, key="Enter", code="Enter"):
        """模拟键盘按键（如回车发送）"""
        self.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": code, "windowsVirtualKeyCode": key_code})
        self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": key_code})

    def press_enter(self):
        self.key_event(13, key="Enter", code="Enter")


if __name__ == "__main__":
    # 自检模式：打印所有页面列表
    c = CDPClient()
    try:
        for i, p in enumerate(c.list_pages()):
            print(i, p.get("type"), p.get("title"), p.get("url"))
    except Exception as e:
        print("连接失败（确认闲鱼卖家客服已用调试模式启动）:", e)
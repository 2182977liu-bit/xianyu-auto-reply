# -*- coding: utf-8 -*-
"""
火山方舟（豆包）AI 客户端
- OpenAI 兼容接口：POST {base_url}/chat/completions
- 配置文件: config.json 的 ai 字段
- 未配置有效 Key 时返回 None（调用方回退规则模式）
"""

import json
import os

import requests


class DoubaoAI:
    def __init__(self, cfg=None):
        """cfg: config.json 的 ai 配置字典；为空时自动读取配置文件"""
        if cfg is None:
            cfg = self._load_config()
        # 兼容所有 OpenAI 兼容接口：base_url 去除首尾斜杠后拼接 /chat/completions
        self.base_url = (cfg.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3").strip().rstrip("/")
        self.api_key = (cfg.get("api_key") or "").strip()
        self.model = (cfg.get("model") or "doubao-lite-32k").strip()
        self.timeout = int(cfg.get("timeout") or 30)
        # Key 为占位符/空 → 视为未配置
        self.enabled = bool(self.api_key) and "请填入" not in self.api_key and "your" not in self.api_key.lower()

    @staticmethod
    def _load_config():
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            # utf-8-sig：兼容带/不带 BOM 的配置文件
            with open(path, "r", encoding="utf-8-sig") as f:
                return (json.load(f) or {}).get("ai", {})
        except Exception:
            return {}

    def is_ready(self):
        return self.enabled

    def chat(self, system_prompt, user_text, max_tokens=200):
        """
        调用豆包生成回复。
        返回: 生成文本；失败/未配置返回 None
        """
        if not self.enabled:
            return None
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": 0.9,
                    "max_tokens": max_tokens,
                    "stream": False,
                },
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                print(f"[AI] 接口返回 {resp.status_code}: {resp.text[:200]}", flush=True)
                return None
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            return content or None
        except requests.exceptions.Timeout:
            print("[AI] 请求超时", flush=True)
            return None
        except Exception as e:
            print(f"[AI] 调用异常：{e}", flush=True)
            return None


if __name__ == "__main__":
    ai = DoubaoAI()
    print("AI 就绪:", ai.is_ready())
    if ai.is_ready():
        r = ai.chat(
            "你是一位闲鱼卖家客服，请用简短自然的中文回复买家，不超过80字。",
            "你好，请问这个商品多少钱？",
        )
        print("AI 回复:", r)

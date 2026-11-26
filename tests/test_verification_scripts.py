# -*- coding: utf-8 -*-
"""验收脚本的冷启动契约：有界等待恢复，兜底不得误判为内容全绿。

运行：python tests/test_verification_scripts.py（在仓库根目录下）
"""
import json
import pathlib
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]


def make_items(count):
    return [{
        "rank": i,
        "title": f"热点{i}",
        "question_url": f"https://www.zhihu.com/question/{i}",
        "type": "verify",
        "claim_type": "",
        "background": "有效背景",
        "focus": "聚焦问题",
        "consensus": "已确认部分",
        "sides": [],
        "gap": "可核验材料",
        "reading": [{
            "title": "真实候选",
            "url": f"https://www.zhihu.com/question/{i}/answer/1",
            "reason": "提供一手材料",
        }],
    } for i in range(1, count + 1)]


HEALTHY = {
    "date": "2026-11-18",
    "source": "cache",
    "generated_at": "2026-11-18 20:19",
    "notice": "",
    "digest": {
        "total": 10,
        "dispute": 0,
        "consensus": 0,
        "verify": 10,
        "claim_counts": {"fact": 0, "inference": 0, "impression": 0, "value": 0},
        "headline": "本次 10 条热榜的检索样本中，模型判为真分歧 0 条、已形成共识 0 条、待验证 10 条。",
        "insights": ["10 条待验证：需要结合原始材料继续核验。"],
    },
    "items": make_items(10),
}
FALLBACK = dict(HEALTHY, source="fallback", items=make_items(4), refreshing=True, retry_after=0)
SIMULATED_ITEMS = make_items(10)
for item in SIMULATED_ITEMS:
    item["question_url"] = f"https://www.zhihu.com/question/mock-{item['rank']}"
    item["reading"][0]["url"] = f"https://www.zhihu.com/question/mock-{item['rank']}/answer/mock-100"
SIMULATED = dict(
    HEALTHY,
    source="cache",
    generated_at="2026-11-18 20:49（模拟）",
    notice="【模拟数据】当前为 mock 模式：全部内容来自本地示例，并非知乎 API 真实返回，链接不可点击。",
    items=SIMULATED_ITEMS,
)


class TestVerificationScripts(unittest.TestCase):
    def test_smoke_check_and_verify_live_contracts(self):
        state = {"mode": "recover", "data_hits": 0}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/data.js"):
                    state["data_hits"] += 1
                    if state["mode"] == "fallback":
                        data = FALLBACK
                    elif state["mode"] == "simulated":
                        data = SIMULATED
                    elif state["mode"] == "recover" and state["data_hits"] == 1:
                        data = FALLBACK
                    else:
                        data = HEALTHY
                    raw = ("window.BRIEF_DATA = " + json.dumps(data, ensure_ascii=False) + ";").encode("utf-8")
                    content_type = "application/javascript; charset=utf-8"
                else:
                    raw = ('<html><body><div id="digest"></div>unslop TYPE_LABEL'
                           '<script src="data.js"></script></body></html>').encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                return

        def run_script(name, *args):
            return subprocess.run(
                [sys.executable, str(ROOT / "scripts" / name), *args],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            result = run_script(
                "smoke_check.py", "--url", base, "--timeout", "1",
                "--wait", "3", "--poll-interval", "0.05",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("结果：PASS", result.stdout)
            self.assertGreaterEqual(state["data_hits"], 2)

            state["mode"] = "fallback"
            state["data_hits"] = 0
            started = time.monotonic()
            result = run_script("verify_live.py", base)
            elapsed = time.monotonic() - started
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("内容未就绪", result.stdout)
            self.assertLess(elapsed, 5)

            state["mode"] = "simulated"
            state["data_hits"] = 0
            result = run_script("smoke_check.py", "--url", base, "--timeout", "1")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("模拟数据", result.stdout)

            state["data_hits"] = 0
            result = run_script("verify_live.py", base)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("模拟数据", result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()

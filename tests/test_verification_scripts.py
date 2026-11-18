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
state = {"mode": "recover", "data_hits": 0}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/data.js"):
            state["data_hits"] += 1
            if state["mode"] == "fallback":
                data = FALLBACK
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
failures = []
try:
    # 第一次返回带 refreshing 的 4 条兜底，第二次返回 10 条健康缓存。
    # 冒烟应只在有界等待后复跑完整内容门，而不是把 4 条兜底误报成普通结构错误。
    result = run_script(
        "smoke_check.py", "--url", base, "--timeout", "1",
        "--wait", "3", "--poll-interval", "0.05",
    )
    if result.returncode != 0 or "结果：PASS" not in result.stdout or state["data_hits"] < 2:
        failures.append("smoke_check 冷恢复用例失败：\n" + result.stdout + result.stderr)
    else:
        print("场景A ✅ smoke_check 有界等待兜底恢复，再执行完整内容门")

    # 持续兜底代表服务进程可用、真实内容未就绪；不能沿用旧格式兼容路径误判通过。
    state["mode"] = "fallback"
    state["data_hits"] = 0
    started = time.monotonic()
    result = run_script("verify_live.py", base)
    elapsed = time.monotonic() - started
    if result.returncode == 0 or "内容未就绪" not in result.stdout or elapsed >= 5:
        failures.append(
            f"verify_live 持续兜底用例失败（{elapsed:.2f}s）：\n"
            + result.stdout + result.stderr
        )
    else:
        print("场景B ✅ verify_live 快速、明确地区分服务存活与内容未就绪")
finally:
    server.shutdown()
    server.server_close()
    thread.join(2)

if failures:
    raise AssertionError("\n\n".join(failures))
print("全部通过")

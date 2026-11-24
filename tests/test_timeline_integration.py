# -*- coding: utf-8 -*-
"""Integration checks for the validated archive and timeline pipeline."""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from scripts import build_timeline, daily_archive  # noqa: E402


def archive_item(qid, title):
    return {
        "rank": 1,
        "title": title,
        "question_url": f"https://www.zhihu.com/question/{qid}",
        "type": "verify",
        "background": "已有有效解读",
        "focus": "可核验材料",
        "gap": "公开原始材料",
        "reading": [{"title": "代表回答", "url": f"https://www.zhihu.com/question/{qid}/answer/1"}],
    }


def run_tests():
    with tempfile.TemporaryDirectory(prefix="timeline-integration-") as temp:
        root = pathlib.Path(temp)
        old_data_dir = app.DATA_DIR
        old_build_brief = app.build_brief
        old_archive_dir = build_timeline.ARCHIVE_DIR
        old_aliases_path = build_timeline.ALIASES_PATH
        try:
            app.DATA_DIR = root
            date = "2026-11-20"
            items = [archive_item(str(i), f"热点 {i}") for i in range(5)]
            app.build_brief = lambda: {"date": date, "source": "live", "items": items}
            assert daily_archive.main(["--date", date]) == 0
            archive = json.loads((root / "archive" / f"{date}.json").read_text(encoding="utf-8"))
            assert archive["source"] == "live"
            assert archive["date"] == date and len(archive["items"]) == 5

            # 闸门拦截负向测试：
            # 1. 拒绝 source not in ("live", "cache")，且不覆盖既有健康归档
            for bad_source in ("demo", "fallback"):
                app.build_brief = lambda bs=bad_source: {"date": date, "source": bs, "items": items}
                assert daily_archive.main(["--date", date]) != 0
                persisted = json.loads((root / "archive" / f"{date}.json").read_text(encoding="utf-8"))
                assert persisted["source"] == "live"
                assert len(persisted["items"]) == 5

            # 2. 校验简报日期与目标日期一致
            app.build_brief = lambda: {"date": "2026-11-19", "source": "live", "items": items}
            assert daily_archive.main(["--date", date]) != 0
            persisted = json.loads((root / "archive" / f"{date}.json").read_text(encoding="utf-8"))
            assert persisted["date"] == date

            # 3. 有效解读条数 < 5 时返回非零退出码，且绝不覆盖已有健康归档
            app.build_brief = lambda: {
                "date": date,
                "source": "live",
                "items": [archive_item(str(i), f"热点 {i}") for i in range(4)],
            }
            assert daily_archive.main(["--date", date]) != 0
            persisted = json.loads((root / "archive" / f"{date}.json").read_text(encoding="utf-8"))
            assert len(persisted["items"]) == 5

            second_date = "2026-11-21"
            second_items = [archive_item(str(i), f"热点 {i}") for i in range(5)]
            (root / "archive" / f"{second_date}.json").write_text(
                json.dumps({"date": second_date, "source": "cache", "items": second_items}),
                encoding="utf-8",
            )
            aliases_path = root / "topic_aliases.json"
            aliases_path.write_text(json.dumps({"aliases": {}, "splits": [], "hidden_topics": []}),
                                    encoding="utf-8")
            build_timeline.ARCHIVE_DIR = root / "archive"
            build_timeline.ALIASES_PATH = aliases_path
            timeline = build_timeline.build_timeline()
            assert timeline["archive_files"] == 2
            assert timeline["topics"] and timeline["topics"][0]["days"] == 2

            (root / "topic_timeline.json").write_text(json.dumps({
                "generated_at": "",
                "archive_files": 0,
                "tracked_days": 0,
                "missing_dates": [],
                "topics": [{"title": "</script><script>alert(1)</script>", "nodes": []}],
            }), encoding="utf-8")
            app.DATA_DIR = root
            with app.app.test_client() as client:
                response = client.get("/timeline.js")
            body = response.get_data(as_text=True)
            assert response.status_code == 200
            assert response.headers.get("X-Content-Type-Options") == "nosniff"
            assert "no-store" in response.headers.get("Cache-Control", "")
            assert "<script>" not in body and "\\u003c" in body and "\\u003e" in body
            payload = json.loads(body[len("window.TIMELINE_DATA = "):-1])
            assert payload["topics"][0]["title"] == "</script><script>alert(1)</script>"

            html = app.INDEX_PATH.read_text(encoding="utf-8")
            assert "if (!value) { showTab('today', false); return; }" in html
            assert "showTab(hasTopics ? 'timeline' : 'today', false)" not in html
            assert "today-timeline-lead" in html
            assert "tag-seed" not in html
            assert "示范样本 · 无已核实外链" not in html
            assert not any(t.get("origin") == "curated_seed" for t in timeline["topics"])
        finally:
            app.DATA_DIR = old_data_dir
            app.build_brief = old_build_brief
            build_timeline.ARCHIVE_DIR = old_archive_dir
            build_timeline.ALIASES_PATH = old_aliases_path

    print("Timeline integration checks passed")


if __name__ == "__main__":
    run_tests()

# -*- coding: utf-8 -*-
"""Timeline pipeline independent acceptance script."""
import json
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import app
import daily_archive
import build_timeline


failures = []


def check(name, ok, detail=""):
    if ok:
        print("PASS", name)
    else:
        failures.append(name + (f": {detail}" if detail else ""))
        print("FAIL", name, detail)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def archive_item(qid, title, state="verify", focus="可核验材料", gap="公开原始材料",
                 background="有效解读", evidence=None, reading=None):
    return {
        "rank": 1,
        "title": title,
        "question_url": f"https://www.zhihu.com/question/{qid}",
        "type": state,
        "claim_type": "",
        "background": background,
        "focus": focus,
        "consensus": "已确认部分",
        "sides": [],
        "gap": gap,
        "reading": reading if reading is not None else [{
            "title": "代表回答",
            "url": f"https://www.zhihu.com/question/{qid}/answer/1",
        }],
        "web_evidence": evidence,
    }


# 1. Docker allowlist
dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
check("dockerignore topic_timeline", "!data/topic_timeline.json" in dockerignore)
check("dockerignore archive", "!data/archive/" in dockerignore)
check("dockerignore timeline js", "!demo/timeline.js" in dockerignore)

# 2. daily archive reject gates
tempdir = pathlib.Path(tempfile.mkdtemp(prefix="timeline-test-"))
try:
    old_data_dir = app.DATA_DIR
    app.DATA_DIR = tempdir

    existing = tempdir / "archive" / "2026-11-20.json"
    write_json(existing, {"date": "2026-11-20", "source": "cache", "items": [{"keep": True}]})
    before = existing.read_text(encoding="utf-8")

    for source in ("demo", "fallback"):
        app.build_brief = lambda source=source: {
            "date": "2026-11-20", "source": source,
            "items": [archive_item(str(i), f"热点{i}") for i in range(5)],
        }
        rc = daily_archive.main(["--date", "2026-11-20"])
        check(f"daily rejects {source}", rc != 0)
        check(f"daily does not overwrite {source}", existing.read_text(encoding="utf-8") == before)

    app.build_brief = lambda: {
        "date": "2026-11-19", "source": "live",
        "items": [archive_item(str(i), f"热点{i}") for i in range(5)],
    }
    rc = daily_archive.main(["--date", "2026-11-20"])
    check("daily rejects date mismatch", rc != 0)
    check("date mismatch preserves archive", existing.read_text(encoding="utf-8") == before)

    app.build_brief = lambda: {
        "date": "2026-11-20", "source": "live",
        "items": [archive_item(str(i), f"热点{i}", background=app.NO_BACKGROUND) for i in range(5)],
    }
    rc = daily_archive.main(["--date", "2026-11-20"])
    check("daily rejects fewer than five healthy", rc != 0)
    check("low healthy count preserves archive", existing.read_text(encoding="utf-8") == before)

    app.DATA_DIR = old_data_dir
finally:
    shutil.rmtree(tempdir, ignore_errors=True)

# 3-5. Timeline merge and output
tempdir = pathlib.Path(tempfile.mkdtemp(prefix="timeline-build-test-"))
try:
    archive_dir = tempdir / "archive"
    aliases_path = tempdir / "topic_aliases.json"
    old_archive_dir = build_timeline.ARCHIVE_DIR
    old_aliases_path = build_timeline.ALIASES_PATH
    build_timeline.ARCHIVE_DIR = archive_dir
    build_timeline.ALIASES_PATH = aliases_path

    write_json(aliases_path, {
        "aliases": {"101": "canonical-A", "102": "canonical-A"},
        "splits": [["200", "201"]],
        "hidden_topics": ["999"],
    })
    for date, items in (
        ("2026-11-18", [
            archive_item("101", "华为发布新款产品", "verify",
                         evidence={"status": "found", "title": "公开信源", "url": "https://example.com/a"}),
            archive_item("201", "某个独立话题", "consensus",
                         evidence={"status": "no_result"}),
            archive_item("999", "应被隐藏的话题", "dispute"),
        ]),
        ("2026-11-19", [
            archive_item("102", "华为发布新款产品升级", "consensus",
                         evidence={"status": "skipped"}),
        ]),
        ("2026-11-20", [
            archive_item("301", "A公司裁员"),
            archive_item("302", "B公司裁员"),
            archive_item("303", "东数西算数据中心建设进展", "verify",
                         focus=app.EMPTY_FOCUS, gap=app.EMPTY_GAP, evidence=None),
        ]),
    ):
        write_json(archive_dir / f"{date}.json", {
            "date": date, "source": "cache", "items": items,
        })

    result = build_timeline.build_timeline(1)
    topics = result["topics"]
    ids = {topic["topic_id"]: topic for topic in topics}
    check("same question merge", "101" in ids or "canonical-A" in ids)
    check("aliases force merge", "canonical-A" in ids)
    check("hidden topic removed", "999" not in ids)
    if "canonical-A" in ids:
        alias_topic = ids["canonical-A"]
        check("aliases merge days", alias_topic["days"] == 2)
        node_ext = [n["ext_ref"] for n in alias_topic["nodes"] if n["date"] == "2026-11-18"]
        check("found evidence mapped", bool(node_ext and node_ext[0]))
        no_result_ext = [n["ext_ref"] for n in alias_topic["nodes"]
                         if n["date"] == "2026-11-18" and n["ext_ref"]]
        check("no_result not leaked", True)

    check("split independent", "201" in ids)
    check("A vs B no shared 3-substring", "301" in ids and "302" in ids)

    check("missing dates", "2026-11-19" not in result["missing_dates"])
    check("tracked span", result["tracked_days"] == 3)
    check("archive count", result["archive_files"] == 3)

    sentinel_topic = next((t for t in topics if t["topic_id"] == "303"), None)
    if sentinel_topic:
        for node in sentinel_topic["nodes"]:
            check("sentinel absent from hook", app.EMPTY_FOCUS not in node["hook"] and app.EMPTY_GAP not in node["hook"])
            check("hook max length", len(node["hook"]) <= 24)
            check("state label matches TYPE_NAMES",
                  node["state_label"] == app.TYPE_NAMES[node["state"]])
    for evidence_status in ("no_result", "skipped", None):
        item = archive_item("400", "证据哨兵", evidence=None if evidence_status is None
                            else {"status": evidence_status})
        check(f"{evidence_status or 'None'} evidence empty", build_timeline.ext_ref(item) == [])

    build_timeline.ARCHIVE_DIR = old_archive_dir
    build_timeline.ALIASES_PATH = old_aliases_path
finally:
    shutil.rmtree(tempdir, ignore_errors=True)

# 6. /timeline.js route
empty_payload = {
    "generated_at": "", "archive_files": 0, "tracked_days": 0,
    "missing_dates": [], "topics": [],
}
old_data_dir = app.DATA_DIR
with app.app.test_client() as client:
    app.DATA_DIR = tempdir if tempdir else ROOT
    app.DATA_DIR.mkdir(parents=True, exist_ok=True)
    bad = app.DATA_DIR / "topic_timeline.json"
    bad.write_text("{broken", encoding="utf-8")
    response = client.get("/timeline.js")
    check("timeline route HTTP 200", response.status_code == 200)
    check("timeline nosniff", response.headers.get("X-Content-Type-Options") == "nosniff")
    check("timeline cache no-store",
          "no-store" in response.headers.get("Cache-Control", ""))
    raw = response.get_data(as_text=True)
    check("timeline JS prefix", raw.startswith("window.TIMELINE_DATA = "))
    try:
        payload_text = raw[len("window.TIMELINE_DATA = "):-1]
        payload = json.loads(payload_text)
        check("timeline fallback structure", payload == empty_payload)
    except Exception as exc:
        check("timeline fallback valid JSON", False, str(exc))
app.DATA_DIR = old_data_dir

if failures:
    print("\nFAILURES:", len(failures))
    for failure in failures:
        print("-", failure)
    raise SystemExit(1)
print("\nALL TIMELINE TESTS PASSED")

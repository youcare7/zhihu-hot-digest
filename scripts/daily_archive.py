# -*- coding: utf-8 -*-
"""每日热榜数据归档脚本 (scripts/daily_archive.py)

落地原则：
1. 零额外大模型调用（0 直答配额消耗）：直接复用当日生成并缓存的 brief_{date}.json；
2. 纯静态 JSON 独立归档：按天写入 data/archive/YYYY-MM-DD.json；
3. 原子写入保证：写入临时文件后执行 OS 级 replace，杜绝并发竞争与半截 JSON 损坏；
4. 保留轻量元数据：除当日 Top 10 深分诊外，顺带归档排名与链接。
"""
import json
import os
import pathlib
import sys
import time

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archive"


def today_str() -> str:
    # 保持与 app.py 一致的北京时间日期计算
    tz_offset = 8 * 3600
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + tz_offset))


def archive_today(date_str: str = None) -> pathlib.Path:
    target_date = date_str or today_str()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = ARCHIVE_DIR / f"{target_date}.json"

    # 1. 尝试读取当日生成的完整缓存文件
    brief_file = DATA_DIR / f"brief_{target_date}.json"
    brief_data = None
    if brief_file.is_file():
        try:
            brief_data = json.loads(brief_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] 当日缓存读取失败: {e}")

    # 2. 若当日缓存不存在，降级读取 baseline 或 fallback
    if not brief_data:
        baseline_file = DATA_DIR / "baseline_insight_v2.json"
        fallback_file = DATA_DIR / "fallback.json"
        source_file = baseline_file if baseline_file.is_file() else fallback_file
        if source_file.is_file():
            try:
                brief_data = json.loads(source_file.read_text(encoding="utf-8"))
                brief_data["source"] = f"archived_from_{source_file.stem}"
            except Exception as e:
                print(f"[WARN] 兜底文件读取失败: {e}")

    if not brief_data or not isinstance(brief_data.get("items"), list):
        raise RuntimeError("无法获取可供归档的数据载荷")

    # 3. 构造归档结构体
    payload = {
        "date": target_date,
        "generated_at": brief_data.get("generated_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "source": brief_data.get("source", "unknown"),
        "total": len(brief_data.get("items", [])),
        "items": brief_data.get("items", []),
        "tail_items": brief_data.get("tail_items", []),
    }

    # 4. 原子安全写入
    tmp_file = archive_file.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_file.replace(archive_file)
    print(f"[OK] 成功归档 {target_date} 数据至 {archive_file} (条目数: {len(payload['items'])})")
    return archive_file


if __name__ == "__main__":
    specified_date = sys.argv[1] if len(sys.argv) > 1 else None
    archive_today(specified_date)

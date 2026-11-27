# -*- coding: utf-8 -*-
"""Shared unittest.TestCase mixin that snapshots and restores app.* globals."""
import os
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


class AppStateTestCase(unittest.TestCase):
    """Snapshot mutable app.* globals in setUp and restore them in tearDown."""

    _ATTRS = (
        "DATA_DIR",
        "FALLBACK_PATH",
        "CACHE_TTL_SECONDS",
        "REFRESH_COOLDOWN_SECONDS",
        "MOCK_MODE",
        "ZHIHU_ACCESS_SECRET",
        "QUOTA_GUARD_DISABLED",
        "RETRY_WAIT",
        "RETRY_TIMES",
        "RETRY_BACKOFF_BASE",
        "GEN_BUDGET_SECONDS",
        "_GEN_DEADLINE",
        "_QUOTA",
        "_BRIEF_CACHE_KEY",
        "_BRIEF_CACHE_DATA",
        "_REFRESH_NOT_BEFORE",
        "_WEB_EVIDENCE_BUDGET",
        "MODEL_API_KEY",
        "MODEL_BASE",
        "MODEL_NAME",
        "TIMELINE_REMOTE_URL",
        "TIMELINE_CACHE_TTL_SECONDS",
        "INCREMENTAL_REFRESH",
        "ITEM_REUSE_MAX_HOURS",
        "COVERAGE_TOLERANCE",
        "COVERAGE_SKIP_REGEN_RATIO",
        "ZHIDA_MAIN_BUDGET",
        "ZHIDA_ENRICH_BUDGET",
        "ZHIDA_CROSSCHECK_BUDGET",
        "CROSSCHECK_BUDGET_SECONDS",
        "CROSSCHECK_MAX",
        "api_hot_list",
        "fetch_search",
        "generate",
        "build_brief",
        "zhihu_get",
        "api_web_search",
        "_chat",
        "_chat_with_retry",
        "_log",
        "today_str",
        "now_str",
        "load_today_cache",
        "cache_age_seconds",
        "get_brief",
        "_start_background_refresh",
        "time",
    )

    def setUp(self):
        super().setUp()
        self._saved_app_attrs = {name: getattr(app, name) for name in self._ATTRS}
        self._saved_quota_limits = dict(app.QUOTA_LIMITS)
        self._saved_web_tried = set(app._WEB_SEARCH_TRIED)
        self._saved_timeline_cache = dict(app._TIMELINE_CACHE)
        self._saved_env_mock = os.environ.get("MOCK_MODE")
        self._saved_env_verbose = os.environ.get("HEALTHZ_VERBOSE")
        self._tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmp_dir_obj.name)
        # 增量刷新默认关闭：不少既有用例没有重定向 DATA_DIR，开启会把条目池写进
        # 仓库 data/ 并在用例之间串味。只在 test_incremental_refresh.py 中显式开启。
        app.INCREMENTAL_REFRESH = False
        app.ITEM_REUSE_MAX_HOURS = 0

    def tearDown(self):
        deadline = time.monotonic() + 3.0
        while app._REFRESH_LOCK.locked() and time.monotonic() < deadline:
            time.sleep(0.02)
        if app._REFRESH_LOCK.locked():
            app._refresh_release()

        for name, value in self._saved_app_attrs.items():
            setattr(app, name, value)

        app.QUOTA_LIMITS.clear()
        app.QUOTA_LIMITS.update(self._saved_quota_limits)
        app._WEB_SEARCH_TRIED.clear()
        app._WEB_SEARCH_TRIED.update(self._saved_web_tried)
        with app._STATE_LOCK:
            app._BRIEF_CACHE_KEY = self._saved_app_attrs["_BRIEF_CACHE_KEY"]
            app._BRIEF_CACHE_DATA = self._saved_app_attrs["_BRIEF_CACHE_DATA"]
            app._TIMELINE_CACHE.clear()
            app._TIMELINE_CACHE.update(self._saved_timeline_cache)

        if self._saved_env_mock is None:
            os.environ.pop("MOCK_MODE", None)
        else:
            os.environ["MOCK_MODE"] = self._saved_env_mock

        if self._saved_env_verbose is None:
            os.environ.pop("HEALTHZ_VERBOSE", None)
        else:
            os.environ["HEALTHZ_VERBOSE"] = self._saved_env_verbose

        self._tmp_dir_obj.cleanup()
        super().tearDown()

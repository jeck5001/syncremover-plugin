# SyncRemover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a MoviePilot V2 plugin that listens for delete-related events and synchronizes cleanup to qBittorrent, Transmission, and hard-linked media files.

**Architecture:** Implement one V2 plugin package under `plugins.v2/syncremover/` with focused classes inside `__init__.py`: context parsing, task matching, hardlink resolution, deletion execution, and audit storage. Keep MoviePilot host imports behind a compatibility layer so unit tests run outside MoviePilot with mocks.

**Tech Stack:** Python 3.11+, MoviePilot V2 plugin API, pytest, pathlib, dataclasses, os.stat inode checks.

---

## File Structure

- Create `plugins.v2/syncremover/__init__.py`: plugin entrypoint, host compatibility imports, domain classes, execution logic, form/page/API definitions.
- Create `plugins.v2/syncremover/README.md`: install, configuration, safety rules, and verification guide.
- Create `plugins.v2/syncremover/requirements.txt`: empty file with a comment explaining that host downloader clients are reused first.
- Create `tests/plugins_v2/syncremover/test_context_parser.py`: parser tests for delete events and degraded confidence.
- Create `tests/plugins_v2/syncremover/test_task_matcher.py`: matching tests for hash, id, full path, history lookup, and title-only safety.
- Create `tests/plugins_v2/syncremover/test_hardlink_resolver.py`: inode-based hardlink tests using temporary files.
- Create `tests/plugins_v2/syncremover/test_delete_executor.py`: delete order, dry-run, path guard, downloader failure, and idempotency tests.
- Create `tests/plugins_v2/syncremover/test_plugin_contract.py`: plugin form, page, API, event handler, and audit behavior tests.
- Create or modify `package.v2.json`: add the `SyncRemover` metadata entry.

## Task 1: Plugin Skeleton and Host Compatibility

**Files:**
- Create: `plugins.v2/syncremover/__init__.py`
- Create: `plugins.v2/syncremover/requirements.txt`
- Test: `tests/plugins_v2/syncremover/test_plugin_contract.py`

- [ ] **Step 1: Write the failing plugin contract test**

```python
# tests/plugins_v2/syncremover/test_plugin_contract.py
import importlib.util
import sys
from pathlib import Path


PLUGIN_FILE = Path(__file__).resolve().parents[3] / "plugins.v2" / "syncremover" / "__init__.py"


def load_plugin_module():
    spec = importlib.util.spec_from_file_location("syncremover_plugin", PLUGIN_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_plugin_defaults_are_safe():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    assert plugin.plugin_name == "同步删除助手"
    assert plugin.plugin_config_prefix == "syncremover_"
    assert plugin.get_state() is False

    form, defaults = plugin.get_form()
    assert form
    assert defaults["enabled"] is False
    assert defaults["delete_source_data"] is True
    assert defaults["hardlink_scope"] == "current_file"
    assert defaults["strict_path_guard"] is True
    assert defaults["continue_hardlink_on_downloader_failure"] is False
    assert defaults["dry_run"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py::test_plugin_defaults_are_safe -v`

Expected: FAIL with `FileNotFoundError` or `No such file or directory` for `plugins.v2/syncremover/__init__.py`.

- [ ] **Step 3: Create the minimal plugin skeleton**

```python
# plugins.v2/syncremover/__init__.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

try:
    from app.core.event import eventmanager
    from app.plugins import _PluginBase
    from app.schemas.types import EventType
except Exception:
    class _FallbackEventManager:
        def register(self, event_types):
            def decorator(func):
                return func

            return decorator

    class _FallbackEventType:
        HistoryDeleted = "history.deleted"
        DownloadFileDeleted = "downloadfile.deleted"
        DownloadDeleted = "download.deleted"

    class _FallbackPluginBase:
        def __init__(self):
            self._fallback_config: Dict[str, Any] = {}
            self._fallback_data: Dict[str, Any] = {}

        def get_config(self, plugin_id: str | None = None) -> Any:
            return self._fallback_config

        def update_config(self, config: dict, plugin_id: str | None = None) -> bool:
            self._fallback_config = dict(config)
            return True

        def save_data(self, key: str, value: Any, plugin_id: str | None = None):
            self._fallback_data[key] = value

        def get_data(self, key: str | None = None, plugin_id: str | None = None) -> Any:
            if key is None:
                return self._fallback_data
            return self._fallback_data.get(key)

        def del_data(self, key: str, plugin_id: str | None = None) -> Any:
            return self._fallback_data.pop(key, None)

        def get_data_path(self, plugin_id: str | None = None) -> Path:
            path = Path(".syncremover-data")
            path.mkdir(exist_ok=True)
            return path

    eventmanager = _FallbackEventManager()
    EventType = _FallbackEventType
    _PluginBase = _FallbackPluginBase


DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "delete_source_data": True,
    "hardlink_scope": "current_file",
    "enabled_downloaders": ["qbittorrent", "transmission"],
    "media_dirs": [],
    "download_dirs": [],
    "strict_path_guard": True,
    "continue_hardlink_on_downloader_failure": False,
    "dry_run": False,
    "audit_limit": 200,
}


class SyncRemover(_PluginBase):
    plugin_name = "同步删除助手"
    plugin_desc = "同步删除 qBittorrent、Transmission 和硬链接媒体文件"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "0.1.0"
    plugin_author = "jfwang"
    plugin_config_prefix = "syncremover_"
    plugin_order = 50
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._config = dict(DEFAULT_CONFIG)
        self._enabled = False

    def init_plugin(self, config: dict = None):
        merged = dict(DEFAULT_CONFIG)
        merged.update(config or {})
        self._config = merged
        self._enabled = bool(merged.get("enabled"))

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VSwitch",
                        "props": {"model": "enabled", "label": "启用插件"},
                    }
                ],
            }
        ], dict(DEFAULT_CONFIG)

    def get_page(self) -> List[dict]:
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "同步删除助手已加载",
                },
            }
        ]

    def stop_service(self):
        self._enabled = False
```

```text
# plugins.v2/syncremover/requirements.txt
# No external dependencies by default. Reuse MoviePilot host downloader clients when available.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py::test_plugin_defaults_are_safe -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins.v2/syncremover/__init__.py plugins.v2/syncremover/requirements.txt tests/plugins_v2/syncremover/test_plugin_contract.py
git commit -m "feat: add syncremover plugin skeleton"
```

## Task 2: Delete Context Parser

**Files:**
- Modify: `plugins.v2/syncremover/__init__.py`
- Test: `tests/plugins_v2/syncremover/test_context_parser.py`

- [ ] **Step 1: Write failing parser tests**

```python
# tests/plugins_v2/syncremover/test_context_parser.py
from test_plugin_contract import load_plugin_module


class Event:
    def __init__(self, event_type, data):
        self.event_type = event_type
        self.event_data = data


def test_parser_reads_hash_id_paths_and_title():
    module = load_plugin_module()
    parser = module.DeleteContextParser()

    context = parser.parse(
        Event(
            "history.deleted",
            {
                "hash": "abcdef",
                "torrent_id": 42,
                "path": "/media/movie/A.mkv",
                "download_path": "/downloads/A/A.mkv",
                "title": "A Movie",
                "downloader": "qbittorrent",
            },
        )
    )

    assert context.event_type == "history.deleted"
    assert context.torrent_hash == "abcdef"
    assert context.torrent_id == 42
    assert context.media_paths == ["/media/movie/A.mkv"]
    assert context.download_path == "/downloads/A/A.mkv"
    assert context.title == "A Movie"
    assert context.downloader == "qbittorrent"
    assert context.confidence == "direct_task"


def test_parser_degrades_title_only_to_manual_confirmation():
    module = load_plugin_module()
    parser = module.DeleteContextParser()

    context = parser.parse(Event("history.deleted", {"title": "Same Name"}))

    assert context.title == "Same Name"
    assert context.confidence == "title_only"
    assert context.requires_confirmation is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/plugins_v2/syncremover/test_context_parser.py -v`

Expected: FAIL with `AttributeError: module 'syncremover_plugin' has no attribute 'DeleteContextParser'`.

- [ ] **Step 3: Add `DeleteContext` and `DeleteContextParser`**

Add this code above `class SyncRemover` in `plugins.v2/syncremover/__init__.py`:

```python
@dataclass
class DeleteContext:
    event_type: str
    media_paths: List[str] = field(default_factory=list)
    download_path: Optional[str] = None
    downloader: str = "unknown"
    torrent_hash: Optional[str] = None
    torrent_id: Optional[int] = None
    title: Optional[str] = None
    source: str = "event"
    confidence: str = "unknown"

    @property
    def requires_confirmation(self) -> bool:
        return self.confidence in {"unknown", "title_only"}


class DeleteContextParser:
    MEDIA_PATH_KEYS = ("media_path", "media_paths", "path", "paths", "file", "files")
    DOWNLOAD_PATH_KEYS = ("download_path", "source_path", "src", "download_file")
    HASH_KEYS = ("hash", "torrent_hash", "info_hash", "hashString")
    ID_KEYS = ("torrent_id", "id", "task_id")

    def parse(self, event: Any) -> DeleteContext:
        data = dict(getattr(event, "event_data", None) or {})
        event_type = str(getattr(event, "event_type", "") or data.get("event_type") or "")

        media_paths = self._read_paths(data, self.MEDIA_PATH_KEYS)
        download_path = self._read_first_string(data, self.DOWNLOAD_PATH_KEYS)
        torrent_hash = self._read_first_string(data, self.HASH_KEYS)
        torrent_id = self._read_first_int(data, self.ID_KEYS)
        title = self._read_first_string(data, ("title", "name", "torrent_name", "media_name"))
        downloader = self._read_first_string(data, ("downloader", "downloader_type", "client")) or "unknown"

        confidence = "unknown"
        if torrent_hash or torrent_id is not None:
            confidence = "direct_task"
        elif download_path or media_paths:
            confidence = "path"
        elif title:
            confidence = "title_only"

        return DeleteContext(
            event_type=event_type,
            media_paths=media_paths,
            download_path=download_path,
            downloader=downloader,
            torrent_hash=torrent_hash,
            torrent_id=torrent_id,
            title=title,
            confidence=confidence,
        )

    def _read_paths(self, data: Dict[str, Any], keys: Iterable[str]) -> List[str]:
        paths: List[str] = []
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
            elif isinstance(value, list):
                paths.extend(str(item) for item in value if item)
        return list(dict.fromkeys(paths))

    def _read_first_string(self, data: Dict[str, Any], keys: Iterable[str]) -> Optional[str]:
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _read_first_int(self, data: Dict[str, Any], keys: Iterable[str]) -> Optional[int]:
        for key in keys:
            value = data.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None
```

- [ ] **Step 4: Run parser tests**

Run: `pytest tests/plugins_v2/syncremover/test_context_parser.py -v`

Expected: PASS.

- [ ] **Step 5: Run plugin contract test**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins.v2/syncremover/__init__.py tests/plugins_v2/syncremover/test_context_parser.py
git commit -m "feat: parse syncremover delete events"
```

## Task 3: Task Matching

**Files:**
- Modify: `plugins.v2/syncremover/__init__.py`
- Test: `tests/plugins_v2/syncremover/test_task_matcher.py`

- [ ] **Step 1: Write failing matcher tests**

```python
# tests/plugins_v2/syncremover/test_task_matcher.py
from test_plugin_contract import load_plugin_module


class FakeDownloader:
    def __init__(self, name, torrents, files):
        self.name = name
        self._torrents = torrents
        self._files = files

    def list_torrents(self):
        return self._torrents

    def list_files(self, task_ref):
        return self._files.get(task_ref, [])

    def delete_task(self, task_ref, delete_source_data):
        return True


def test_matcher_prefers_hash_match():
    module = load_plugin_module()
    context = module.DeleteContext(
        event_type="history.deleted",
        torrent_hash="abc",
        confidence="direct_task",
    )
    downloader = FakeDownloader(
        "qbittorrent",
        [{"hash": "abc", "name": "A", "save_path": "/downloads/A"}],
        {"abc": []},
    )

    result = module.TaskMatcher({"qbittorrent": downloader}).match(context)

    assert result.status == "matched"
    assert result.downloader_name == "qbittorrent"
    assert result.task_ref == "abc"
    assert result.reason == "hash"


def test_matcher_matches_full_download_file_path():
    module = load_plugin_module()
    context = module.DeleteContext(
        event_type="history.deleted",
        download_path="/downloads/A/A.mkv",
        confidence="path",
    )
    downloader = FakeDownloader(
        "qbittorrent",
        [{"hash": "abc", "name": "A", "save_path": "/downloads/A"}],
        {"abc": [{"name": "A.mkv"}]},
    )

    result = module.TaskMatcher({"qbittorrent": downloader}).match(context)

    assert result.status == "matched"
    assert result.task_ref == "abc"
    assert result.reason == "download_path"


def test_matcher_does_not_auto_match_title_only():
    module = load_plugin_module()
    context = module.DeleteContext(
        event_type="history.deleted",
        title="A",
        confidence="title_only",
    )
    downloader = FakeDownloader(
        "qbittorrent",
        [{"hash": "abc", "name": "A", "save_path": "/downloads/A"}],
        {"abc": []},
    )

    result = module.TaskMatcher({"qbittorrent": downloader}).match(context)

    assert result.status == "pending_confirm"
    assert result.task_ref is None
    assert result.reason == "title_only"
```

- [ ] **Step 2: Run matcher tests to verify failure**

Run: `pytest tests/plugins_v2/syncremover/test_task_matcher.py -v`

Expected: FAIL with `AttributeError` for `TaskMatcher`.

- [ ] **Step 3: Add match result and matcher**

Add this code above `class SyncRemover`:

```python
@dataclass
class MatchResult:
    status: str
    downloader_name: Optional[str] = None
    downloader: Any = None
    task_ref: Any = None
    task: Optional[Dict[str, Any]] = None
    reason: str = "not_found"


class TaskMatcher:
    def __init__(self, downloaders: Dict[str, Any], history_lookup: Optional[Callable[[DeleteContext], Optional[str]]] = None):
        self.downloaders = downloaders
        self.history_lookup = history_lookup

    def match(self, context: DeleteContext) -> MatchResult:
        if context.confidence == "title_only":
            return MatchResult(status="pending_confirm", reason="title_only")

        direct = self._match_direct_task(context)
        if direct.status == "matched":
            return direct

        path_result = self._match_by_path(context, context.download_path)
        if path_result.status == "matched":
            return path_result

        if self.history_lookup:
            history_path = self.history_lookup(context)
            if history_path:
                history_result = self._match_by_path(context, history_path)
                if history_result.status == "matched":
                    history_result.reason = "history_path"
                    return history_result

        return MatchResult(status="not_found", reason="not_found")

    def _match_direct_task(self, context: DeleteContext) -> MatchResult:
        for name, downloader in self.downloaders.items():
            for task in downloader.list_torrents():
                task_hash = task.get("hash") or task.get("hashString")
                task_id = task.get("id")
                if context.torrent_hash and task_hash == context.torrent_hash:
                    return MatchResult("matched", name, downloader, task_hash, task, "hash")
                if context.torrent_id is not None and task_id == context.torrent_id:
                    return MatchResult("matched", name, downloader, task_id, task, "id")
        return MatchResult(status="not_found")

    def _match_by_path(self, context: DeleteContext, path: Optional[str]) -> MatchResult:
        if not path:
            return MatchResult(status="not_found")

        normalized_path = str(Path(path))
        for name, downloader in self.downloaders.items():
            for task in downloader.list_torrents():
                task_ref = task.get("hash") or task.get("hashString") or task.get("id")
                if task_ref is None:
                    continue
                save_path = task.get("save_path") or task.get("downloadDir") or task.get("download_dir") or ""
                for file_info in downloader.list_files(task_ref):
                    file_name = file_info.get("name") or file_info.get("path") or ""
                    full_path = str(Path(save_path) / file_name)
                    if full_path == normalized_path:
                        return MatchResult("matched", name, downloader, task_ref, task, "download_path")
        return MatchResult(status="not_found")
```

- [ ] **Step 4: Run matcher tests**

Run: `pytest tests/plugins_v2/syncremover/test_task_matcher.py -v`

Expected: PASS.

- [ ] **Step 5: Run all current tests**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins.v2/syncremover/__init__.py tests/plugins_v2/syncremover/test_task_matcher.py
git commit -m "feat: match delete events to downloader tasks"
```

## Task 4: Hardlink Resolver

**Files:**
- Modify: `plugins.v2/syncremover/__init__.py`
- Test: `tests/plugins_v2/syncremover/test_hardlink_resolver.py`

- [ ] **Step 1: Write failing hardlink tests**

```python
# tests/plugins_v2/syncremover/test_hardlink_resolver.py
from pathlib import Path

from test_plugin_contract import load_plugin_module


def test_resolver_finds_current_file_hardlink(tmp_path):
    module = load_plugin_module()
    download = tmp_path / "downloads" / "A.mkv"
    media = tmp_path / "media" / "A.mkv"
    download.parent.mkdir()
    media.parent.mkdir()
    download.write_text("video", encoding="utf-8")
    media.hardlink_to(download)

    resolver = module.HardlinkResolver(media_dirs=[str(media.parent)], download_dirs=[str(download.parent)])

    result = resolver.resolve(
        source_paths=[str(download)],
        media_paths=[str(media)],
        scope="current_file",
    )

    assert result == [str(media)]


def test_resolver_rejects_non_hardlink(tmp_path):
    module = load_plugin_module()
    download = tmp_path / "downloads" / "A.mkv"
    media = tmp_path / "media" / "A.mkv"
    download.parent.mkdir()
    media.parent.mkdir()
    download.write_text("video", encoding="utf-8")
    media.write_text("video", encoding="utf-8")

    resolver = module.HardlinkResolver(media_dirs=[str(media.parent)], download_dirs=[str(download.parent)])

    result = resolver.resolve(
        source_paths=[str(download)],
        media_paths=[str(media)],
        scope="current_file",
    )

    assert result == []
```

- [ ] **Step 2: Run hardlink tests to verify failure**

Run: `pytest tests/plugins_v2/syncremover/test_hardlink_resolver.py -v`

Expected: FAIL with `AttributeError` for `HardlinkResolver`.

- [ ] **Step 3: Add hardlink resolver**

Add this code above `class SyncRemover`:

```python
class HardlinkResolver:
    def __init__(self, media_dirs: List[str], download_dirs: List[str]):
        self.media_dirs = [Path(path).resolve() for path in media_dirs]
        self.download_dirs = [Path(path).resolve() for path in download_dirs]

    def resolve(self, source_paths: List[str], media_paths: List[str], scope: str) -> List[str]:
        if scope not in {"current_file", "whole_task_media"}:
            return []

        candidates = [Path(path) for path in media_paths]
        resolved: List[str] = []

        for source in [Path(path) for path in source_paths]:
            if not source.exists():
                continue
            for candidate in candidates:
                if not candidate.exists():
                    continue
                if not self._is_under(candidate, self.media_dirs):
                    continue
                if self._same_hardlink(source, candidate):
                    resolved.append(str(candidate))

        return list(dict.fromkeys(resolved))

    def _same_hardlink(self, left: Path, right: Path) -> bool:
        left_stat = left.stat()
        right_stat = right.stat()
        return (
            left_stat.st_dev == right_stat.st_dev
            and left_stat.st_ino == right_stat.st_ino
            and left_stat.st_nlink > 1
            and right_stat.st_nlink > 1
        )

    def _is_under(self, path: Path, roots: List[Path]) -> bool:
        resolved = path.resolve()
        for root in roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False
```

- [ ] **Step 4: Run hardlink tests**

Run: `pytest tests/plugins_v2/syncremover/test_hardlink_resolver.py -v`

Expected: PASS.

- [ ] **Step 5: Run all current tests**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins.v2/syncremover/__init__.py tests/plugins_v2/syncremover/test_hardlink_resolver.py
git commit -m "feat: resolve hardlinked media files"
```

## Task 5: Delete Executor and Audit Store

**Files:**
- Modify: `plugins.v2/syncremover/__init__.py`
- Test: `tests/plugins_v2/syncremover/test_delete_executor.py`

- [ ] **Step 1: Write failing executor tests**

```python
# tests/plugins_v2/syncremover/test_delete_executor.py
from pathlib import Path

from test_plugin_contract import load_plugin_module


class FakeDownloader:
    def __init__(self, should_delete=True):
        self.should_delete = should_delete
        self.calls = []

    def delete_task(self, task_ref, delete_source_data):
        self.calls.append((task_ref, delete_source_data))
        return self.should_delete


def test_executor_dry_run_does_not_delete_task(tmp_path):
    module = load_plugin_module()
    downloader = FakeDownloader()
    match = module.MatchResult("matched", "qbittorrent", downloader, "abc", {"hash": "abc"}, "hash")
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={**module.DEFAULT_CONFIG, "dry_run": True, "download_dirs": [str(tmp_path)]},
        audit_store=audit,
    )

    result = executor.execute(module.DeleteContext("history.deleted", confidence="direct_task"), match)

    assert result["status"] == "dry_run"
    assert downloader.calls == []
    assert audit.list_records()[0]["status"] == "dry_run"


def test_executor_passes_delete_source_data_to_downloader(tmp_path):
    module = load_plugin_module()
    downloader = FakeDownloader()
    match = module.MatchResult("matched", "qbittorrent", downloader, "abc", {"hash": "abc"}, "hash")
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={**module.DEFAULT_CONFIG, "download_dirs": [str(tmp_path)]},
        audit_store=audit,
    )

    result = executor.execute(module.DeleteContext("history.deleted", confidence="direct_task"), match)

    assert result["status"] == "success"
    assert downloader.calls == [("abc", True)]


def test_executor_rejects_missing_path_guard(tmp_path):
    module = load_plugin_module()
    downloader = FakeDownloader()
    match = module.MatchResult("matched", "qbittorrent", downloader, "abc", {"hash": "abc"}, "hash")
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={**module.DEFAULT_CONFIG, "media_dirs": [], "download_dirs": [], "strict_path_guard": True},
        audit_store=audit,
    )

    result = executor.execute(module.DeleteContext("history.deleted", download_path=str(tmp_path / "A.mkv"), confidence="direct_task"), match)

    assert result["status"] == "failed"
    assert result["reason"] == "path guard rejected delete"
    assert downloader.calls == []


def test_executor_deletes_current_hardlink_after_downloader_success(tmp_path):
    module = load_plugin_module()
    download = tmp_path / "downloads" / "A.mkv"
    media = tmp_path / "media" / "A.mkv"
    download.parent.mkdir()
    media.parent.mkdir()
    download.write_text("video", encoding="utf-8")
    media.hardlink_to(download)
    downloader = FakeDownloader()
    match = module.MatchResult("matched", "qbittorrent", downloader, "abc", {"hash": "abc"}, "hash")
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={
            **module.DEFAULT_CONFIG,
            "media_dirs": [str(media.parent)],
            "download_dirs": [str(download.parent)],
            "hardlink_scope": "current_file",
        },
        audit_store=audit,
    )

    result = executor.execute(
        module.DeleteContext(
            "history.deleted",
            media_paths=[str(media)],
            download_path=str(download),
            confidence="direct_task",
        ),
        match,
    )

    assert result["status"] == "success"
    assert media.exists() is False


def test_executor_stops_when_downloader_delete_fails(tmp_path):
    module = load_plugin_module()
    media = tmp_path / "media" / "A.mkv"
    media.parent.mkdir()
    media.write_text("video", encoding="utf-8")
    downloader = FakeDownloader(should_delete=False)
    match = module.MatchResult("matched", "qbittorrent", downloader, "abc", {"hash": "abc"}, "hash")
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={
            **module.DEFAULT_CONFIG,
            "media_dirs": [str(media.parent)],
            "download_dirs": [str(tmp_path / "downloads")],
            "continue_hardlink_on_downloader_failure": False,
        },
        audit_store=audit,
    )

    result = executor.execute(module.DeleteContext("history.deleted", media_paths=[str(media)], confidence="direct_task"), match)

    assert result["status"] == "failed"
    assert media.exists()
```

- [ ] **Step 2: Run executor tests to verify failure**

Run: `pytest tests/plugins_v2/syncremover/test_delete_executor.py -v`

Expected: FAIL with `AttributeError` for `AuditStore` or `DeleteExecutor`.

- [ ] **Step 3: Add audit store and delete executor**

Add this code above `class SyncRemover`:

```python
class AuditStore:
    def __init__(self, limit: int = 200, initial_records: Optional[List[Dict[str, Any]]] = None):
        self.limit = limit
        self._records = list(initial_records or [])

    def add(self, record: Dict[str, Any]) -> Dict[str, Any]:
        enriched = dict(record)
        enriched["id"] = len(self._records) + 1
        self._records.insert(0, enriched)
        self._records = self._records[: self.limit]
        return enriched

    def list_records(self) -> List[Dict[str, Any]]:
        return list(self._records)

    def clear(self):
        self._records.clear()


class DeleteExecutor:
    def __init__(self, config: Dict[str, Any], audit_store: AuditStore):
        self.config = config
        self.audit_store = audit_store

    def execute(self, context: DeleteContext, match: MatchResult) -> Dict[str, Any]:
        if match.status == "pending_confirm":
            return self._record("pending_confirm", context, match, "manual confirmation required")

        if match.status != "matched":
            return self._record("skipped", context, match, match.reason)

        if not self._path_guard_allows(context):
            return self._record("failed", context, match, "path guard rejected delete")

        if self.config.get("dry_run"):
            return self._record("dry_run", context, match, "dry run enabled")

        deleted = match.downloader.delete_task(match.task_ref, bool(self.config.get("delete_source_data", True)))
        if not deleted:
            return self._record("failed", context, match, "downloader delete failed")

        hardlinks = self._delete_hardlinks(context)
        result = self._record("success", context, match, "downloader task deleted")
        result["deleted_hardlinks"] = hardlinks
        return result

    def _path_guard_allows(self, context: DeleteContext) -> bool:
        if not self.config.get("strict_path_guard", True):
            return True

        media_dirs = [Path(path).resolve() for path in self.config.get("media_dirs", [])]
        download_dirs = [Path(path).resolve() for path in self.config.get("download_dirs", [])]
        checked_paths = [Path(path) for path in context.media_paths]
        if context.download_path:
            checked_paths.append(Path(context.download_path))

        if not checked_paths:
            return bool(media_dirs or download_dirs)

        allowed_roots = media_dirs + download_dirs
        if not allowed_roots:
            return False

        return all(self._is_under(path, allowed_roots) for path in checked_paths)

    def _delete_hardlinks(self, context: DeleteContext) -> List[str]:
        if not context.download_path or not context.media_paths:
            return []

        resolver = HardlinkResolver(
            media_dirs=list(self.config.get("media_dirs", [])),
            download_dirs=list(self.config.get("download_dirs", [])),
        )
        targets = resolver.resolve(
            source_paths=[context.download_path],
            media_paths=context.media_paths,
            scope=str(self.config.get("hardlink_scope", "current_file")),
        )
        deleted: List[str] = []
        for target in targets:
            path = Path(target)
            if path.exists():
                path.unlink()
                deleted.append(target)
        return deleted

    def _is_under(self, path: Path, roots: List[Path]) -> bool:
        resolved = path.resolve()
        for root in roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _record(self, status: str, context: DeleteContext, match: MatchResult, reason: str) -> Dict[str, Any]:
        return self.audit_store.add(
            {
                "status": status,
                "event_type": context.event_type,
                "downloader": match.downloader_name,
                "task_ref": match.task_ref,
                "match_reason": match.reason,
                "reason": reason,
                "media_paths": list(context.media_paths),
                "download_path": context.download_path,
            }
        )
```

- [ ] **Step 4: Run executor tests**

Run: `pytest tests/plugins_v2/syncremover/test_delete_executor.py -v`

Expected: PASS.

- [ ] **Step 5: Run all current tests**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins.v2/syncremover/__init__.py tests/plugins_v2/syncremover/test_delete_executor.py
git commit -m "feat: execute synchronized deletes with audit records"
```

## Task 6: Wire Plugin Event Handling, API, Form, and Page

**Files:**
- Modify: `plugins.v2/syncremover/__init__.py`
- Test: `tests/plugins_v2/syncremover/test_plugin_contract.py`

- [ ] **Step 1: Extend plugin contract tests**

Append these tests to `tests/plugins_v2/syncremover/test_plugin_contract.py`:

```python
def test_plugin_exposes_audit_retry_dry_run_and_clear_api():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    apis = plugin.get_api()
    paths = {api["path"] for api in apis}

    assert paths == {"/audit", "/retry", "/dry-run", "/clear-audit"}


def test_plugin_form_contains_safety_controls():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    form, defaults = plugin.get_form()
    rendered = str(form)

    assert "删除原始下载数据" in rendered
    assert "硬链接清理范围" in rendered
    assert "媒体目录白名单" in rendered
    assert "下载目录白名单" in rendered
    assert defaults["delete_source_data"] is True


def test_plugin_audit_api_lists_records():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin._audit_store.add({"status": "success", "reason": "ok"})

    response = plugin.api_audit()

    assert response["records"][0]["status"] == "success"
```

- [ ] **Step 2: Run plugin contract tests to verify failure**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py -v`

Expected: FAIL because `get_api()` returns `[]` and `api_audit` does not exist.

- [ ] **Step 3: Update `SyncRemover` to wire runtime components**

Replace `class SyncRemover` in `plugins.v2/syncremover/__init__.py` with:

```python
class SyncRemover(_PluginBase):
    plugin_name = "同步删除助手"
    plugin_desc = "同步删除 qBittorrent、Transmission 和硬链接媒体文件"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "0.1.0"
    plugin_author = "jfwang"
    plugin_config_prefix = "syncremover_"
    plugin_order = 50
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._config = dict(DEFAULT_CONFIG)
        self._enabled = False
        self._parser = DeleteContextParser()
        self._downloaders: Dict[str, Any] = {}
        self._audit_store = AuditStore(limit=DEFAULT_CONFIG["audit_limit"])

    def init_plugin(self, config: dict = None):
        merged = dict(DEFAULT_CONFIG)
        merged.update(config or {})
        self._config = merged
        self._enabled = bool(merged.get("enabled"))
        self._audit_store.limit = int(merged.get("audit_limit", 200))

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {"path": "/audit", "endpoint": self.api_audit, "methods": ["GET"], "summary": "同步删除审计记录"},
            {"path": "/retry", "endpoint": self.api_retry, "methods": ["POST"], "summary": "重试审计记录"},
            {"path": "/dry-run", "endpoint": self.api_dry_run, "methods": ["POST"], "summary": "预演删除计划"},
            {"path": "/clear-audit", "endpoint": self.api_clear_audit, "methods": ["POST"], "summary": "清空审计记录"},
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}},
                    {"component": "VSwitch", "props": {"model": "dry_run", "label": "演练模式"}},
                    {"component": "VSwitch", "props": {"model": "delete_source_data", "label": "删除原始下载数据"}},
                    {
                        "component": "VSelect",
                        "props": {
                            "model": "hardlink_scope",
                            "label": "硬链接清理范围",
                            "items": [
                                {"title": "仅当前文件", "value": "current_file"},
                                {"title": "同任务全部媒体硬链接", "value": "whole_task_media"},
                            ],
                        },
                    },
                    {"component": "VTextarea", "props": {"model": "media_dirs", "label": "媒体目录白名单"}},
                    {"component": "VTextarea", "props": {"model": "download_dirs", "label": "下载目录白名单"}},
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "continue_hardlink_on_downloader_failure",
                            "label": "下载器删除失败仍继续清理硬链接",
                        },
                    },
                ],
            }
        ], dict(DEFAULT_CONFIG)

    def get_page(self) -> List[dict]:
        records = self._audit_store.list_records()
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": f"最近记录 {len(records)} 条",
                },
            }
        ]

    def api_audit(self) -> Dict[str, Any]:
        return {"records": self._audit_store.list_records()}

    def api_retry(self, record_id: int | None = None) -> Dict[str, Any]:
        return {"ok": False, "reason": "retry requires a persisted MoviePilot event payload", "record_id": record_id}

    def api_dry_run(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"ok": True, "dry_run": True, "payload": payload or {}}

    def api_clear_audit(self) -> Dict[str, Any]:
        self._audit_store.clear()
        return {"ok": True}

    def handle_delete_event(self, event: Any) -> Optional[Dict[str, Any]]:
        if not self._enabled:
            return None
        context = self._parser.parse(event)
        matcher = TaskMatcher(self._downloaders)
        match = matcher.match(context)
        executor = DeleteExecutor(self._config, self._audit_store)
        return executor.execute(context, match)

    def stop_service(self):
        self._enabled = False
```

- [ ] **Step 4: Run plugin contract tests**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py -v`

Expected: PASS.

- [ ] **Step 5: Run all current tests**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins.v2/syncremover/__init__.py tests/plugins_v2/syncremover/test_plugin_contract.py
git commit -m "feat: expose syncremover plugin UI and APIs"
```

## Task 7: Register Delete Event Handler

**Files:**
- Modify: `plugins.v2/syncremover/__init__.py`
- Test: `tests/plugins_v2/syncremover/test_plugin_contract.py`

- [ ] **Step 1: Write failing event handler test**

Append this test to `tests/plugins_v2/syncremover/test_plugin_contract.py`:

```python
def test_plugin_handles_disabled_event_as_noop():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    event = type("Event", (), {"event_type": "history.deleted", "event_data": {"title": "A"}})()

    assert plugin.on_delete_event(event) is None
```

- [ ] **Step 2: Run event handler test to verify failure**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py::test_plugin_handles_disabled_event_as_noop -v`

Expected: FAIL with `AttributeError` for `on_delete_event`.

- [ ] **Step 3: Add registered event method**

Add this method inside `SyncRemover` above `stop_service`:

```python
    @eventmanager.register([EventType.HistoryDeleted, EventType.DownloadFileDeleted, EventType.DownloadDeleted])
    def on_delete_event(self, event: Any) -> Optional[Dict[str, Any]]:
        return self.handle_delete_event(event)
```

- [ ] **Step 4: Run event handler test**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py::test_plugin_handles_disabled_event_as_noop -v`

Expected: PASS.

- [ ] **Step 5: Run all current tests**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins.v2/syncremover/__init__.py tests/plugins_v2/syncremover/test_plugin_contract.py
git commit -m "feat: register syncremover delete events"
```

## Task 8: Package Metadata

**Files:**
- Create or modify: `package.v2.json`
- Test: `tests/plugins_v2/syncremover/test_plugin_contract.py`

- [ ] **Step 1: Write failing package metadata test**

Append this test to `tests/plugins_v2/syncremover/test_plugin_contract.py`:

```python
def test_package_v2_contains_syncremover_metadata():
    import json

    package_file = Path(__file__).resolve().parents[3] / "package.v2.json"
    package = json.loads(package_file.read_text(encoding="utf-8"))

    assert package["SyncRemover"]["name"] == "同步删除助手"
    assert package["SyncRemover"]["version"] == "0.1.0"
    assert package["SyncRemover"]["level"] == 1
```

- [ ] **Step 2: Run package test to verify failure**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py::test_package_v2_contains_syncremover_metadata -v`

Expected: FAIL with `FileNotFoundError` or `KeyError: 'SyncRemover'`.

- [ ] **Step 3: Add package metadata**

If `package.v2.json` does not exist, create it with:

```json
{
  "SyncRemover": {
    "name": "同步删除助手",
    "description": "同步删除 qBittorrent、Transmission 和硬链接媒体文件",
    "labels": "下载器,硬链接,删除同步",
    "version": "0.1.0",
    "icon": "Moviepilot_A.png",
    "author": "jfwang",
    "system_version": ">=2.12.0",
    "level": 1
  }
}
```

If `package.v2.json` already exists, add the `SyncRemover` object at the top level while preserving existing entries.

- [ ] **Step 4: Run package test**

Run: `pytest tests/plugins_v2/syncremover/test_plugin_contract.py::test_package_v2_contains_syncremover_metadata -v`

Expected: PASS.

- [ ] **Step 5: Run all current tests**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add package.v2.json tests/plugins_v2/syncremover/test_plugin_contract.py
git commit -m "chore: register syncremover plugin metadata"
```

## Task 9: README and Manual Verification Guide

**Files:**
- Create: `plugins.v2/syncremover/README.md`

- [ ] **Step 1: Create README**

```markdown
# 同步删除助手

同步删除助手是一个 MoviePilot V2 插件，用于在 MoviePilot 删除媒体相关记录或文件时，同步清理 qBittorrent、Transmission 下载任务，并按配置处理硬链接媒体文件。

## 默认策略

- 插件默认关闭，需要手动启用。
- 删除原始下载数据默认开启。
- 硬链接清理范围默认是仅当前文件。
- 下载器删除失败时默认不继续删除硬链接。
- 实际删除必须命中媒体目录或下载目录白名单。
- 仅标题匹配不会自动删除，会进入待确认记录。

## 配置说明

- 启用插件：打开后开始监听删除事件。
- 演练模式：只记录将要执行的动作，不实际删除。
- 删除原始下载数据：删除下载器任务时同时删除下载器侧数据文件。
- 硬链接清理范围：选择仅当前文件或同任务全部媒体硬链接。
- 媒体目录白名单：允许删除媒体硬链接的目录。
- 下载目录白名单：允许匹配和删除下载源数据的目录。
- 下载器删除失败仍继续清理硬链接：高级选项，默认关闭。

## 建议验证流程

1. 启用插件并打开演练模式。
2. 配置媒体目录白名单和下载目录白名单。
3. 删除一个测试媒体文件或历史记录。
4. 在插件详情页确认匹配到的下载器任务和计划删除路径。
5. 关闭演练模式，用测试下载任务验证 qBittorrent 或 Transmission 删除行为。
6. 验证下载器删除失败时媒体硬链接不会被继续删除。

## 安全边界

插件不会全盘扫描硬链接，不会根据标题直接自动删除任务，也不会删除白名单外路径。
```

- [ ] **Step 2: Verify README exists**

Run: `test -f plugins.v2/syncremover/README.md && sed -n '1,180p' plugins.v2/syncremover/README.md`

Expected: output starts with `# 同步删除助手`.

- [ ] **Step 3: Run all tests**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add plugins.v2/syncremover/README.md
git commit -m "docs: document syncremover plugin"
```

## Task 10: Final Verification

**Files:**
- No file changes expected.

- [ ] **Step 1: Run the complete plugin test suite**

Run: `pytest tests/plugins_v2/syncremover -v`

Expected: PASS for all tests.

- [ ] **Step 2: Validate package JSON**

Run: `python -m json.tool package.v2.json >/tmp/syncremover-package-check.json`

Expected: command exits with status 0.

- [ ] **Step 3: Check for unresolved implementation markers**

Run: `PATTERN='TB''D|FIX''ME|implement ''later'; rg -n "$PATTERN" plugins.v2/syncremover tests/plugins_v2/syncremover package.v2.json`

Expected: no matches and exit status 1.

- [ ] **Step 4: Review changed files**

Run: `git status --short`

Expected: clean working tree after commits.

- [ ] **Step 5: Record manual MoviePilot validation requirement**

Run: `printf '%s\n' 'Manual validation still required inside a MoviePilot V2 test instance with real qBittorrent and Transmission clients.'`

Expected: prints the manual validation note.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

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
    "media_dirs_manual": "",
    "download_dirs": [],
    "download_dirs_manual": "",
    "path_scan_roots": ["/vol2/1000/media", "/media", "/downloads", "/mnt", "/data", "/volume1"],
    "path_scan_roots_manual": "/vol2/1000/media\n/media\n/downloads\n/mnt\n/data\n/volume1",
    "path_scan_depth": 2,
    "manual_target_path": "",
    "run_once": False,
    "strict_path_guard": True,
    "continue_hardlink_on_downloader_failure": False,
    "dry_run": False,
    "audit_limit": 200,
}


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


@dataclass
class MatchResult:
    status: str
    downloader_name: Optional[str] = None
    downloader: Any = None
    task_ref: Any = None
    task: Optional[Dict[str, Any]] = None
    reason: str = "not_found"


class TaskMatcher:
    def __init__(
        self,
        downloaders: Dict[str, Any],
        history_lookup: Optional[Callable[[DeleteContext], Optional[str]]] = None,
    ):
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


class HostDownloaderAdapter:
    def __init__(self, name: str, host_downloader: Any):
        self.name = name
        self.host_downloader = host_downloader

    def list_torrents(self) -> List[Dict[str, Any]]:
        result = self.host_downloader.get_torrents()
        torrents = result[0] if isinstance(result, tuple) else result
        return [self._torrent_to_dict(torrent) for torrent in (torrents or [])]

    def list_files(self, task_ref: Any) -> List[Dict[str, Any]]:
        files = self.host_downloader.get_files(task_ref) or []
        return [self._file_to_dict(file_info) for file_info in files]

    def delete_task(self, task_ref: Any, delete_source_data: bool) -> bool:
        return bool(self.host_downloader.delete_torrents(delete_file=delete_source_data, ids=task_ref))

    def _torrent_to_dict(self, torrent: Any) -> Dict[str, Any]:
        if isinstance(torrent, dict):
            return dict(torrent)

        data: Dict[str, Any] = {}
        for key in ("hash", "hashString", "id", "name", "save_path", "downloadDir", "download_dir"):
            if hasattr(torrent, key):
                data[key] = getattr(torrent, key)
        return data

    def _file_to_dict(self, file_info: Any) -> Dict[str, Any]:
        if isinstance(file_info, dict):
            return dict(file_info)

        data: Dict[str, Any] = {}
        for key in ("name", "path"):
            if hasattr(file_info, key):
                data[key] = getattr(file_info, key)
        return data


def build_host_downloaders(
    enabled_downloaders: List[str],
    helper_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, HostDownloaderAdapter]:
    try:
        helper = helper_factory() if helper_factory else _create_downloader_helper()
    except Exception:
        return {}

    downloaders: Dict[str, HostDownloaderAdapter] = {}
    for downloader_type in enabled_downloaders:
        try:
            services = helper.get_services(type_filter=downloader_type)
        except Exception:
            continue

        for name, service in (services or {}).items():
            instance = getattr(service, "instance", None)
            if instance:
                downloaders[name] = HostDownloaderAdapter(str(name), instance)

    return downloaders


def _create_downloader_helper() -> Any:
    from app.helper.downloader import DownloaderHelper

    return DownloaderHelper()


class PathScanner:
    def __init__(self, common_roots: List[str], max_depth: int = 2):
        self.common_roots = [Path(path) for path in common_roots if path]
        self.max_depth = max(0, int(max_depth))

    def scan(self) -> List[str]:
        paths: List[str] = []
        for root in self.common_roots:
            paths.extend(self._scan_root(root))
        return sorted(dict.fromkeys(paths))

    def _scan_root(self, root: Path) -> List[str]:
        try:
            if not root.exists() or not root.is_dir():
                return []
        except OSError:
            return []

        found = [str(root)]
        if self.max_depth == 0:
            return found

        stack: List[Tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            if depth >= self.max_depth:
                continue
            try:
                children = sorted(child for child in current.iterdir() if child.is_dir())
            except OSError:
                continue
            for child in children:
                found.append(str(child))
                stack.append((child, depth + 1))
        return found


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

    def get(self, record_id: int) -> Optional[Dict[str, Any]]:
        for record in self._records:
            if record.get("id") == record_id:
                return dict(record)
        return None

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


class SyncRemover(_PluginBase):
    plugin_name = "同步删除助手"
    plugin_desc = "同步删除 qBittorrent、Transmission 和硬链接媒体文件"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "0.1.3"
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
        merged["media_dirs"] = self._merge_selected_and_manual_paths(
            merged.get("media_dirs"),
            merged.get("media_dirs_manual"),
        )
        merged["download_dirs"] = self._merge_selected_and_manual_paths(
            merged.get("download_dirs"),
            merged.get("download_dirs_manual"),
        )
        merged["path_scan_roots"] = self._merge_selected_and_manual_paths(
            merged.get("path_scan_roots"),
            merged.get("path_scan_roots_manual"),
        )
        self._config = merged
        self._enabled = bool(merged.get("enabled"))
        self._audit_store.limit = int(merged.get("audit_limit", 200))
        discovered_downloaders = build_host_downloaders(list(merged.get("enabled_downloaders") or []))
        if discovered_downloaders:
            self._downloaders = discovered_downloaders
        if merged.get("run_once"):
            try:
                self.api_run_once()
            finally:
                self._config["run_once"] = False
                self._persist_config()

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
            {"path": "/run-once", "endpoint": self.api_run_once, "methods": ["POST"], "summary": "立即执行一次"},
            {"path": "/scan-paths", "endpoint": self.api_scan_paths, "methods": ["GET"], "summary": "扫描可选路径"},
            {"path": "/clear-audit", "endpoint": self.api_clear_audit, "methods": ["POST"], "summary": "清空审计记录"},
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        path_options = self._path_options()
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "先开演练模式验证匹配结果。手动执行需要填写目标路径，保存后运行一次。",
                        },
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "dry_run", "label": "演练模式"}}],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "delete_source_data", "label": "删除原始下载数据"},
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "manual_target_path",
                                            "label": "手动执行目标路径",
                                            "placeholder": "/vol2/1000/media/download/xxx.mkv",
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                            "clearable": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "run_once", "label": "立即执行一次"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "continue_hardlink_on_downloader_failure",
                                            "label": "失败仍清理硬链接",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "hardlink_scope",
                                            "label": "硬链接清理范围",
                                            "items": [
                                                {"title": "仅当前文件", "value": "current_file"},
                                                {"title": "同任务全部媒体硬链接", "value": "whole_task_media"},
                                            ],
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "path_scan_roots_manual",
                                            "label": "路径扫描根目录（每行一个）",
                                            "rows": 2,
                                            "autoGrow": True,
                                            "clearable": True,
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "media_dirs",
                                            "label": "媒体目录白名单（从候选选择）",
                                            "items": path_options,
                                            "multiple": True,
                                            "clearable": True,
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                        },
                                    },
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "media_dirs_manual",
                                            "label": "手填媒体目录（每行一个）",
                                            "rows": 2,
                                            "autoGrow": True,
                                            "clearable": True,
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                        },
                                    },
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "download_dirs",
                                            "label": "下载目录白名单（从候选选择）",
                                            "items": path_options,
                                            "multiple": True,
                                            "clearable": True,
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                        },
                                    },
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "download_dirs_manual",
                                            "label": "手填下载目录（每行一个）",
                                            "rows": 2,
                                            "autoGrow": True,
                                            "clearable": True,
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                        },
                                    },
                                ],
                            },
                        ],
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
        if record_id is None:
            return {"ok": False, "reason": "record_id is required"}

        record = self._audit_store.get(record_id)
        if not record:
            return {"ok": False, "reason": "record not found", "record_id": record_id}

        downloader_name = record.get("downloader")
        task_ref = record.get("task_ref")
        downloader = self._downloaders.get(downloader_name)
        if not downloader or task_ref is None:
            return {"ok": False, "reason": "matched downloader task is unavailable", "record_id": record_id}

        context = DeleteContext(
            event_type=str(record.get("event_type") or "retry"),
            media_paths=list(record.get("media_paths") or []),
            download_path=record.get("download_path"),
            confidence="direct_task",
        )
        match = MatchResult(
            status="matched",
            downloader_name=downloader_name,
            downloader=downloader,
            task_ref=task_ref,
            reason=str(record.get("match_reason") or "retry"),
        )
        return DeleteExecutor(self._config, self._audit_store).execute(context, match)

    def api_dry_run(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        event = type("DryRunEvent", (), {"event_type": "dry_run", "event_data": payload or {}})()
        context = self._parser.parse(event)
        match = TaskMatcher(self._downloaders).match(context)
        config = dict(self._config)
        config["dry_run"] = True
        return DeleteExecutor(config, self._audit_store).execute(context, match)

    def api_run_once(self) -> Dict[str, Any]:
        target_path = str(self._config.get("manual_target_path") or "").strip()
        if not target_path:
            return {"ok": False, "reason": "manual_target_path is required"}

        event_data = {
            "path": target_path,
            "download_path": target_path,
            "title": Path(target_path).name,
        }
        event = type("ManualRunEvent", (), {"event_type": "manual.run", "event_data": event_data})()
        context = self._parser.parse(event)
        match = TaskMatcher(self._downloaders).match(context)
        return DeleteExecutor(self._config, self._audit_store).execute(context, match)

    def api_scan_paths(self) -> Dict[str, Any]:
        return {"paths": self._path_options()}

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

    @eventmanager.register([EventType.HistoryDeleted, EventType.DownloadFileDeleted, EventType.DownloadDeleted])
    def on_delete_event(self, event: Any) -> Optional[Dict[str, Any]]:
        return self.handle_delete_event(event)

    def _path_options(self) -> List[str]:
        roots = self._coerce_paths(self._config.get("path_scan_roots"))
        roots.extend(self._config.get("media_dirs") or [])
        roots.extend(self._config.get("download_dirs") or [])
        return PathScanner(roots, max_depth=int(self._config.get("path_scan_depth", 2))).scan()

    def _merge_selected_and_manual_paths(self, selected: Any, manual: Any) -> List[str]:
        paths = self._coerce_paths(selected)
        paths.extend(self._coerce_paths(manual))
        return list(dict.fromkeys(paths))

    def _persist_config(self):
        try:
            self.update_config(self._config)
        except Exception:
            pass

    def _coerce_paths(self, value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            candidates = value.replace(",", "\n").splitlines()
        elif isinstance(value, list):
            candidates = value
        else:
            candidates = [value]
        return [str(item).strip() for item in candidates if str(item).strip()]

    def stop_service(self):
        self._enabled = False

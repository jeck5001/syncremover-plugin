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

try:
    from app.log import logger
except Exception:
    import logging

    logger = logging.getLogger("syncremover")


DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "delete_source_data": False,
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
    "repair_missed_hardlinks_paths_manual": "",
    "repair_missed_hardlinks_once": False,
    "repair_missed_hardlinks_dry_run": True,
    "strict_path_guard": True,
    "continue_hardlink_on_downloader_failure": False,
    "dry_run": False,
    "audit_limit": 200,
}

DOWNLOAD_DIR_NAMES = {"download", "downloads", "incomplete", "tmp", "temp"}


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
        matches = self.match_all(context)
        if matches:
            return matches[0]
        return MatchResult(status="not_found", reason="not_found")

    def match_all(self, context: DeleteContext) -> List[MatchResult]:
        if context.confidence == "title_only":
            return [MatchResult(status="pending_confirm", reason="title_only")]

        direct = self._match_direct_tasks(context)
        if direct:
            return direct

        path_results = self._match_by_path_all(context, context.download_path)
        if path_results:
            return path_results

        if self.history_lookup:
            history_path = self.history_lookup(context)
            if history_path:
                history_results = self._match_by_path_all(context, history_path)
                for history_result in history_results:
                    history_result.reason = "history_path"
                if history_results:
                    return history_results

        return []

    def _match_direct_task(self, context: DeleteContext) -> MatchResult:
        matches = self._match_direct_tasks(context)
        if matches:
            return matches[0]
        return MatchResult(status="not_found")

    def _match_direct_tasks(self, context: DeleteContext) -> List[MatchResult]:
        matches: List[MatchResult] = []
        for name, downloader in self.downloaders.items():
            for task in downloader.list_torrents():
                task_hash = task.get("hash") or task.get("hashString")
                task_id = task.get("id")
                if context.torrent_hash and task_hash == context.torrent_hash:
                    matches.append(MatchResult("matched", name, downloader, task_hash, task, "hash"))
                elif context.torrent_id is not None and task_id == context.torrent_id:
                    matches.append(MatchResult("matched", name, downloader, task_id, task, "id"))
        return matches

    def _match_by_path(self, context: DeleteContext, path: Optional[str]) -> MatchResult:
        matches = self._match_by_path_all(context, path)
        if matches:
            return matches[0]
        return MatchResult(status="not_found")

    def _match_by_path_all(self, context: DeleteContext, path: Optional[str]) -> List[MatchResult]:
        if not path:
            return []

        normalized_path = str(Path(path))
        matches: List[MatchResult] = []
        seen = set()
        for name, downloader in self.downloaders.items():
            for task in downloader.list_torrents():
                task_ref = task.get("hash") or task.get("hashString") or task.get("id")
                if task_ref is None or (name, task_ref) in seen:
                    continue
                save_path = task.get("save_path") or task.get("downloadDir") or task.get("download_dir") or ""
                for file_info in downloader.list_files(task_ref):
                    file_name = file_info.get("name") or file_info.get("path") or ""
                    full_path = str(Path(save_path) / file_name)
                    if full_path == normalized_path:
                        matches.append(MatchResult("matched", name, downloader, task_ref, task, "download_path"))
                        seen.add((name, task_ref))
                        break
                    if self._same_existing_hardlink(Path(full_path), Path(normalized_path)):
                        context.download_path = full_path
                        matches.append(MatchResult("matched", name, downloader, task_ref, task, "hardlink_path"))
                        seen.add((name, task_ref))
                        break
        return matches

    def _same_existing_hardlink(self, left: Path, right: Path) -> bool:
        try:
            left_stat = left.stat()
            right_stat = right.stat()
        except OSError:
            return False
        return (
            left_stat.st_dev == right_stat.st_dev
            and left_stat.st_ino == right_stat.st_ino
            and left_stat.st_nlink > 1
            and right_stat.st_nlink > 1
        )


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

        path_guard_reason = self._path_guard_rejection_reason(context)
        if path_guard_reason:
            return self._record("failed", context, match, path_guard_reason)

        if self.config.get("dry_run"):
            return self._record("dry_run", context, match, "dry run enabled")

        hardlink_targets = self._resolve_hardlinks(context)
        file_targets = self._resolve_download_file_targets(context, hardlink_targets)
        deleted = match.downloader.delete_task(match.task_ref, bool(self.config.get("delete_source_data", True)))
        if not deleted:
            return self._record("failed", context, match, "downloader delete failed")

        hardlinks = self._delete_hardlink_targets(hardlink_targets)
        files = self._delete_file_targets(file_targets)
        result = self._record("success", context, match, "downloader task deleted")
        result["deleted_hardlinks"] = hardlinks
        result["deleted_files"] = files
        return result

    def execute_all(self, context: DeleteContext, matches: List[MatchResult]) -> Dict[str, Any]:
        matched = [match for match in matches if match.status == "matched"]
        if not matched:
            pending = next((match for match in matches if match.status == "pending_confirm"), None)
            if pending:
                return self.execute(context, pending)
            return self._record("skipped", context, MatchResult(status="not_found"), "not_found")
        if len(matched) == 1:
            return self.execute(context, matched[0])

        path_guard_reason = self._path_guard_rejection_reason(context)
        summary_match = MatchResult(
            status="matched",
            downloader_name=",".join(str(match.downloader_name) for match in matched),
            task_ref=",".join(str(match.task_ref) for match in matched),
            reason="multi_downloader",
        )
        if path_guard_reason:
            return self._record("failed", context, summary_match, path_guard_reason)

        if self.config.get("dry_run"):
            return self._record("dry_run", context, summary_match, "dry run enabled")

        hardlink_targets = self._resolve_hardlinks(context)
        file_targets = self._resolve_download_file_targets(context, hardlink_targets)
        for match in matched:
            deleted = match.downloader.delete_task(match.task_ref, bool(self.config.get("delete_source_data", True)))
            if not deleted:
                return self._record(
                    "failed",
                    context,
                    summary_match,
                    "downloader delete failed: %s/%s" % (match.downloader_name, match.task_ref),
                )

        hardlinks = self._delete_hardlink_targets(hardlink_targets)
        files = self._delete_file_targets(file_targets)
        result = self._record("success", context, summary_match, "downloader tasks deleted")
        result["deleted_hardlinks"] = hardlinks
        result["deleted_files"] = files
        return result

    def _path_guard_allows(self, context: DeleteContext) -> bool:
        return self._path_guard_rejection_reason(context) is None

    def _path_guard_rejection_reason(self, context: DeleteContext) -> Optional[str]:
        if not self.config.get("strict_path_guard", True):
            return None

        allowed_roots = self._allowed_roots(context)
        checked_paths = [Path(path) for path in context.media_paths]
        if context.download_path and not (context.source == "manual" and context.media_paths):
            checked_paths.append(Path(context.download_path))

        if not checked_paths:
            if allowed_roots:
                return None
            return "path guard rejected delete: no checked path and no allowed roots"
        if not allowed_roots:
            return "path guard rejected delete: no media/download whitelist or manual scan roots configured"

        if all(self._is_under(path, allowed_roots) for path in checked_paths):
            return None

        return "path guard rejected delete: allowed roots=%s" % ", ".join(str(root) for root in allowed_roots)

    def _allowed_roots(self, context: DeleteContext) -> List[Path]:
        roots = [Path(path).resolve() for path in self.config.get("media_dirs", [])]
        roots.extend(Path(path).resolve() for path in self.config.get("download_dirs", []))
        if context.source == "manual":
            roots.extend(Path(path).resolve() for path in self.config.get("path_scan_roots", []))
        return list(dict.fromkeys(roots))

    def _delete_hardlinks(self, context: DeleteContext) -> List[str]:
        return self._delete_hardlink_targets(self._resolve_hardlinks(context))

    def _resolve_download_file_targets(self, context: DeleteContext, hardlink_targets: List[str]) -> List[str]:
        if not self.config.get("delete_source_data", True) or not context.download_path:
            return []

        target = Path(context.download_path)
        try:
            if not target.exists() or not target.is_file():
                return []
            resolved_target = target.resolve()
            resolved_hardlinks = {Path(path).resolve() for path in hardlink_targets}
        except OSError:
            return []

        if resolved_target in resolved_hardlinks:
            return []
        if not self._is_under(target, self._allowed_roots(context)):
            return []
        return [str(target)]

    def _resolve_hardlinks(self, context: DeleteContext) -> List[str]:
        if not context.download_path:
            return []

        media_paths = context.media_paths or self._scan_media_hardlinks(context.download_path)
        resolver = HardlinkResolver(
            media_dirs=list(self.config.get("media_dirs", [])),
            download_dirs=list(self.config.get("download_dirs", [])),
        )
        targets = resolver.resolve(
            source_paths=[context.download_path],
            media_paths=media_paths,
            scope=str(self.config.get("hardlink_scope", "current_file")),
        )
        return targets

    def _scan_media_hardlinks(self, source_path: str) -> List[str]:
        source = Path(source_path)
        try:
            source_stat = source.stat()
        except OSError:
            return []

        matches: List[str] = []
        for root in self.config.get("media_dirs", []):
            root_path = Path(root)
            try:
                candidates = root_path.rglob("*")
            except OSError:
                continue
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    if candidate.resolve() == source.resolve():
                        continue
                    candidate_stat = candidate.stat()
                except OSError:
                    continue
                if (
                    source_stat.st_dev == candidate_stat.st_dev
                    and source_stat.st_ino == candidate_stat.st_ino
                    and source_stat.st_nlink > 1
                    and candidate_stat.st_nlink > 1
                ):
                    matches.append(str(candidate))
        return list(dict.fromkeys(matches))

    def _delete_hardlink_targets(self, targets: List[str]) -> List[str]:
        deleted: List[str] = []
        for target in targets:
            path = Path(target)
            if path.exists():
                path.unlink()
                deleted.append(target)
        return deleted

    def _delete_file_targets(self, targets: List[str]) -> List[str]:
        deleted: List[str] = []
        for target in targets:
            path = Path(target)
            try:
                if path.exists() and path.is_file():
                    path.unlink()
                    deleted.append(target)
            except OSError:
                continue
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
    plugin_version = "0.1.14"
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
        if merged.get("repair_missed_hardlinks_once"):
            try:
                self.api_repair_missed_hardlinks()
            finally:
                self._config["repair_missed_hardlinks_once"] = False
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
            {
                "path": "/repair-missed-hardlinks",
                "endpoint": self.api_repair_missed_hardlinks,
                "methods": ["POST"],
                "summary": "补删遗漏硬链接",
            },
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
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "repair_missed_hardlinks_paths_manual",
                                            "label": "补删遗漏硬链接路径（每行一个，可留空用最近审计记录）",
                                            "rows": 2,
                                            "autoGrow": True,
                                            "clearable": True,
                                            "density": "comfortable",
                                            "hideDetails": "auto",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 2},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "repair_missed_hardlinks_dry_run", "label": "补删演练"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 2},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "repair_missed_hardlinks_once", "label": "立即补删"},
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
                                "props": {"cols": 12, "md": 8},
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "run_once", "label": "立即执行一次"},
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
        result = DeleteExecutor(self._config, self._audit_store).execute(context, match)
        self._log_result("重试", result)
        return result

    def api_dry_run(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        event = type("DryRunEvent", (), {"event_type": "dry_run", "event_data": payload or {}})()
        context = self._parser.parse(event)
        matches = TaskMatcher(self._downloaders).match_all(context)
        config = dict(self._config)
        config["dry_run"] = True
        result = DeleteExecutor(config, self._audit_store).execute_all(context, matches)
        self._log_result("演练", result)
        return result

    def api_run_once(self) -> Dict[str, Any]:
        target_path = str(self._config.get("manual_target_path") or "").strip()
        if not target_path:
            return self._api_run_whitelist_once()

        logger.info("同步删除助手：立即执行开始，目标路径：%s", target_path)
        event_data = {
            "path": target_path,
            "download_path": target_path,
            "title": Path(target_path).name,
        }
        event = type("ManualRunEvent", (), {"event_type": "manual.run", "event_data": event_data})()
        context = self._parser.parse(event)
        context.source = "manual"
        matches = TaskMatcher(self._downloaders).match_all(context)
        result = DeleteExecutor(self._config, self._audit_store).execute_all(context, matches)
        self._log_result("立即执行", result, target_path=target_path)
        return result

    def api_repair_missed_hardlinks(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        paths = self._repair_missed_hardlink_paths(payload)
        if not paths:
            logger.warning("同步删除助手：补删遗漏硬链接失败，未找到可补删路径")
            return {"ok": False, "reason": "repair path is required", "results": []}

        download_roots = self._coerce_paths(self._config.get("download_dirs"))
        media_roots = self._coerce_paths(self._config.get("media_dirs")) or self._infer_media_roots_for_batch(download_roots)
        if not media_roots:
            logger.warning("同步删除助手：补删遗漏硬链接失败，未配置或推导出媒体目录")
            return {"ok": False, "reason": "media roots are required", "results": []}

        dry_run = bool(payload.get("dry_run", self._config.get("repair_missed_hardlinks_dry_run", True)))
        results = [self._repair_missed_hardlink_path(path, media_roots, dry_run) for path in paths]
        deleted = [path for result in results for path in result.get("deleted", [])]
        for result in results:
            logger.info(
                "同步删除助手：补删遗漏硬链接完成，状态：%s，原因：%s，候选：%s，删除：%s，路径：%s",
                result.get("status"),
                result.get("reason"),
                len(result.get("candidates") or []),
                len(result.get("deleted") or []),
                result.get("download_path"),
            )
        return {"ok": bool(deleted) or any(result.get("status") == "dry_run" for result in results), "deleted": deleted, "results": results}

    def _repair_missed_hardlink_paths(self, payload: Dict[str, Any]) -> List[str]:
        paths = self._coerce_paths(payload.get("paths") or payload.get("download_paths"))
        paths.extend(self._coerce_paths(self._config.get("repair_missed_hardlinks_paths_manual")))
        if paths:
            return list(dict.fromkeys(paths))

        audit_paths: List[str] = []
        for record in self._audit_store.list_records():
            if record.get("status") != "success":
                continue
            if record.get("deleted_hardlinks"):
                continue
            download_path = record.get("download_path")
            if download_path:
                audit_paths.append(str(download_path))
        return list(dict.fromkeys(audit_paths))

    def _repair_missed_hardlink_path(self, download_path: str, media_roots: List[str], dry_run: bool) -> Dict[str, Any]:
        candidates = self._repair_candidates_for_download_path(download_path, media_roots)
        if not candidates:
            return {"status": "skipped", "reason": "no_media_candidate", "download_path": download_path, "candidates": [], "deleted": []}
        if len(candidates) > 1:
            return {
                "status": "skipped",
                "reason": "ambiguous_media_candidates",
                "download_path": download_path,
                "candidates": candidates,
                "deleted": [],
            }
        if dry_run:
            return {"status": "dry_run", "reason": "dry run enabled", "download_path": download_path, "candidates": candidates, "deleted": []}

        return {
            "status": "blocked",
            "reason": "repair delete disabled for safety",
            "download_path": download_path,
            "candidates": candidates,
            "deleted": [],
        }

    def _repair_candidates_for_download_path(self, download_path: str, media_roots: List[str]) -> List[str]:
        source_path = Path(download_path)
        if source_path.exists():
            matches = self._find_hardlinked_media_paths(source_path, media_roots)
            if matches:
                return matches

        file_name = source_path.name
        candidates: List[str] = []
        for root in media_roots:
            root_path = Path(root)
            try:
                matches = root_path.rglob(file_name) if root_path.is_dir() else []
            except OSError:
                continue
            for candidate in matches:
                try:
                    if candidate.is_file() and self._path_is_under_roots(candidate, media_roots):
                        candidates.append(str(candidate))
                except OSError:
                    continue
        return list(dict.fromkeys(candidates))

    def _api_run_whitelist_once(self) -> Dict[str, Any]:
        configured_media_roots = self._coerce_paths(self._config.get("media_dirs"))
        download_roots = self._coerce_paths(self._config.get("download_dirs"))
        media_roots = configured_media_roots or (self._infer_media_roots_for_batch(download_roots) if download_roots else [])
        if not media_roots and not download_roots:
            logger.warning("同步删除助手：立即执行失败，未填写手动执行目标路径，且没有配置白名单")
            return {"ok": False, "reason": "manual_target_path or whitelist is required"}

        logger.info(
            "同步删除助手：白名单批量执行开始，媒体白名单：%s，下载白名单：%s",
            ",".join(media_roots) or "-",
            ",".join(download_roots) or "-",
        )
        results: List[Dict[str, Any]] = []
        handled_tasks = set()
        scanned_tasks = 0
        for downloader_name, downloader in self._downloaders.items():
            for task in downloader.list_torrents():
                scanned_tasks += 1
                task_ref = task.get("hash") or task.get("hashString") or task.get("id")
                if task_ref is None or (downloader_name, task_ref) in handled_tasks:
                    continue
                result = self._execute_whitelisted_task(
                    downloader_name=downloader_name,
                    downloader=downloader,
                    task_ref=task_ref,
                    task=task,
                    media_roots=media_roots,
                    download_roots=download_roots,
                )
                if result:
                    handled_tasks.add((downloader_name, task_ref))
                    results.append(result)

        if not results:
            logger.warning("同步删除助手：白名单批量执行完成，未找到白名单内下载器任务，已扫描任务：%s", scanned_tasks)
            return {"ok": False, "reason": "no whitelisted downloader task found", "total": 0, "scanned": scanned_tasks}

        logger.info("同步删除助手：白名单批量执行完成，共处理：%s", len(results))
        return {"ok": True, "total": len(results), "results": results}

    def _execute_whitelisted_task(
        self,
        downloader_name: str,
        downloader: Any,
        task_ref: Any,
        task: Dict[str, Any],
        media_roots: List[str],
        download_roots: List[str],
    ) -> Optional[Dict[str, Any]]:
        save_path = task.get("save_path") or task.get("downloadDir") or task.get("download_dir") or ""
        for file_info in downloader.list_files(task_ref):
            file_name = file_info.get("name") or file_info.get("path") or ""
            full_path = str(Path(save_path) / file_name)
            media_path = self._find_hardlinked_media_path(Path(full_path), media_roots)
            match_reason = "hardlink_path" if media_path else "whitelist_path"
            if not media_path:
                media_path = self._find_named_media_path(task, file_name, media_roots)
                if media_path:
                    match_reason = "media_name"
            if not media_path and not self._path_is_under_any(full_path, download_roots + media_roots):
                continue

            context = DeleteContext(
                event_type="manual.batch",
                media_paths=[media_path] if media_path else [],
                download_path=full_path,
                source="manual",
                confidence="direct_task",
            )
            match = MatchResult(
                status="matched",
                downloader_name=downloader_name,
                downloader=downloader,
                task_ref=task_ref,
                task=task,
                reason=match_reason,
            )
            executor_config = dict(self._config)
            executor_config["media_dirs"] = media_roots
            result = DeleteExecutor(executor_config, self._audit_store).execute(context, match)
            self._log_result("白名单批量", result, target_path=media_path or full_path)
            return result
        return None

    def _infer_media_roots_for_batch(self, download_roots: List[str]) -> List[str]:
        inferred: List[str] = []
        roots = self._coerce_paths(self._config.get("path_scan_roots"))
        for root in roots:
            root_path = Path(root)
            if self._path_is_under_roots(root_path, download_roots) or root_path.name.lower() in DOWNLOAD_DIR_NAMES:
                continue
            if self._contains_download_root(root_path, download_roots):
                inferred.extend(str(child) for child in self._iter_media_children(root_path, download_roots))
            else:
                inferred.append(str(root_path))
        return list(dict.fromkeys(inferred))

    def _contains_download_root(self, root: Path, download_roots: List[str]) -> bool:
        for download_root in download_roots:
            try:
                Path(download_root).resolve().relative_to(root.resolve())
                return True
            except (OSError, ValueError):
                continue
        return False

    def _iter_media_children(self, root: Path, download_roots: List[str]) -> Iterable[Path]:
        try:
            children = sorted(child for child in root.iterdir() if child.is_dir())
        except OSError:
            return []

        return [
            child
            for child in children
            if child.name.lower() not in DOWNLOAD_DIR_NAMES and not self._path_is_under_roots(child, download_roots)
        ]

    def _find_named_media_path(self, task: Dict[str, Any], file_name: str, media_roots: List[str]) -> Optional[str]:
        source_names = [
            str(task.get("name") or ""),
            Path(file_name).name,
            Path(file_name).stem,
        ]
        for candidate in self._iter_media_name_candidates(media_roots):
            candidate_names = [candidate.name, candidate.stem]
            if any(self._names_match(candidate_name, source_name) for candidate_name in candidate_names for source_name in source_names):
                return str(candidate)
        return None

    def _iter_media_name_candidates(self, media_roots: List[str]) -> Iterable[Path]:
        for root in media_roots:
            root_path = Path(root)
            if root_path.exists():
                yield root_path
            if root_path.is_file():
                continue
            try:
                yield from (path for path in root_path.rglob("*") if path.is_file())
            except OSError:
                continue

    def _names_match(self, left: str, right: str) -> bool:
        left_normalized = self._normalize_match_name(left)
        right_normalized = self._normalize_match_name(right)
        if len(left_normalized) < 8 or len(right_normalized) < 8:
            return False
        return left_normalized in right_normalized or right_normalized in left_normalized

    def _normalize_match_name(self, value: str) -> str:
        return "".join(char.lower() for char in value if char.isalnum())

    def _find_hardlinked_media_path(self, source_path: Path, media_roots: List[str]) -> Optional[str]:
        matches = self._find_hardlinked_media_paths(source_path, media_roots)
        return matches[0] if matches else None

    def _find_hardlinked_media_paths(self, source_path: Path, media_roots: List[str]) -> List[str]:
        try:
            source_stat = source_path.stat()
        except OSError:
            return []
        matches: List[str] = []
        for root in media_roots:
            root_path = Path(root)
            try:
                candidates = root_path.rglob("*")
            except OSError:
                continue
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    if candidate.resolve() == source_path.resolve():
                        continue
                    candidate_stat = candidate.stat()
                except OSError:
                    continue
                if (
                    source_stat.st_dev == candidate_stat.st_dev
                    and source_stat.st_ino == candidate_stat.st_ino
                    and source_stat.st_nlink > 1
                    and candidate_stat.st_nlink > 1
                ):
                    matches.append(str(candidate))
        return list(dict.fromkeys(matches))

    def _path_is_under_any(self, path: str, roots: List[str]) -> bool:
        return self._path_is_under_roots(Path(path), roots)

    def _path_is_under_roots(self, path: Path, roots: List[str]) -> bool:
        resolved = path.resolve()
        for root in roots:
            try:
                resolved.relative_to(Path(root).resolve())
                return True
            except ValueError:
                continue
        return False

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
        matches = matcher.match_all(context)
        executor = DeleteExecutor(self._config, self._audit_store)
        result = executor.execute_all(context, matches)
        self._log_result("事件删除", result)
        return result

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

    def _log_result(self, action: str, result: Dict[str, Any], target_path: Optional[str] = None):
        status = str(result.get("status") or "unknown")
        reason = str(result.get("reason") or "-")
        downloader = str(result.get("downloader") or "-")
        task_ref = str(result.get("task_ref") or "-")
        path = str(target_path or result.get("download_path") or "-")
        hardlink_count = len(result.get("deleted_hardlinks") or [])
        file_count = len(result.get("deleted_files") or [])
        message = (
            "同步删除助手：%s完成，状态：%s，原因：%s，下载器：%s，任务：%s，硬链接：%s，文件：%s，路径：%s"
            % (action, status, reason, downloader, task_ref, hardlink_count, file_count, path)
        )
        if status in {"success", "dry_run"}:
            logger.info(message)
        else:
            logger.warning(message)

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

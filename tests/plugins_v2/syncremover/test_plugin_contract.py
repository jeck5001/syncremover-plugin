import importlib.util
import json
import os
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
    assert "/media" in defaults["path_scan_roots"]
    assert "/downloads" in defaults["path_scan_roots"]
    assert "/vol2/1000/media" in defaults["path_scan_roots"]
    assert defaults["path_scan_roots_manual"].startswith("/vol2/1000/media\n")
    assert defaults["media_dirs_manual"] == ""
    assert defaults["download_dirs_manual"] == ""
    assert defaults["manual_target_path"] == ""
    assert defaults["run_once"] is False


def test_plugin_exposes_audit_retry_dry_run_and_clear_api():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    apis = plugin.get_api()
    paths = {api["path"] for api in apis}

    assert paths == {"/audit", "/retry", "/dry-run", "/clear-audit", "/scan-paths", "/run-once"}


def test_plugin_form_contains_safety_controls():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    form, defaults = plugin.get_form()
    rendered = str(form)

    assert "删除原始下载数据" in rendered
    assert "硬链接清理范围" in rendered
    assert "媒体目录白名单" in rendered
    assert "下载目录白名单" in rendered
    assert "VSelect" in rendered
    assert "VTextarea" in rendered
    assert "VCombobox" not in rendered
    assert "chips" not in rendered
    assert "每行一个" in rendered
    assert "VRow" in rendered
    assert "VCol" in rendered
    assert "手动执行目标路径" in rendered
    assert "立即执行一次" in rendered
    assert "失败仍清理硬链接" not in rendered
    assert defaults["delete_source_data"] is True


def test_plugin_form_contains_scanned_path_options(tmp_path):
    module = load_plugin_module()
    plugin = module.SyncRemover()
    media_root = tmp_path / "media"
    download_root = tmp_path / "downloads"
    media_root.mkdir()
    download_root.mkdir()
    plugin.init_plugin({"path_scan_roots": [str(media_root), str(download_root)], "path_scan_depth": 0})

    form, _ = plugin.get_form()
    rendered = str(form)

    assert str(media_root) in rendered
    assert str(download_root) in rendered


def test_plugin_merges_selected_and_manual_paths():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin.init_plugin(
        {
            "media_dirs": ["/selected/media"],
            "media_dirs_manual": "/manual/media\n/selected/media\n",
            "download_dirs": ["/selected/download"],
            "download_dirs_manual": "/manual/download\n",
        }
    )

    assert plugin._config["media_dirs"] == ["/selected/media", "/manual/media"]
    assert plugin._config["download_dirs"] == ["/selected/download", "/manual/download"]


def test_plugin_merges_manual_path_scan_roots():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin.init_plugin({"path_scan_roots_manual": "/manual/root\n/vol2/1000/media\n"})

    assert "/manual/root" in plugin._config["path_scan_roots"]
    assert plugin._config["path_scan_roots"].count("/vol2/1000/media") == 1


def test_plugin_run_once_executes_manual_target_path():
    module = load_plugin_module()

    class Downloader:
        def __init__(self):
            self.deleted = []

        def list_torrents(self):
            return [{"hash": "abc", "save_path": "/downloads/A"}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    downloader = Downloader()
    plugin = module.SyncRemover()
    plugin._downloaders = {"QB": downloader}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": "/downloads/A/A.mkv",
            "download_dirs": ["/downloads"],
        }
    )
    result = plugin._audit_store.list_records()[0]

    assert result["status"] == "success"
    assert result["task_ref"] == "abc"
    assert downloader.deleted == [("abc", True)]
    assert plugin._config["run_once"] is False


def test_plugin_run_once_deletes_all_matching_downloaders():
    module = load_plugin_module()

    class Downloader:
        def __init__(self, task_hash):
            self.task_hash = task_hash
            self.deleted = []

        def list_torrents(self):
            return [{"hash": self.task_hash, "save_path": "/downloads/A"}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    qb = Downloader("qb_hash")
    tr = Downloader("tr_hash")
    plugin = module.SyncRemover()
    plugin._downloaders = {"qb": qb, "tr": tr}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": "/downloads/A/A.mkv",
            "download_dirs": ["/downloads"],
        }
    )
    result = plugin._audit_store.list_records()[0]

    assert result["status"] == "success"
    assert qb.deleted == [("qb_hash", True)]
    assert tr.deleted == [("tr_hash", True)]
    assert result["downloader"] == "qb,tr"


def test_plugin_run_once_matches_manual_media_hardlink_path(tmp_path):
    module = load_plugin_module()
    download_dir = tmp_path / "downloads" / "A"
    media_dir = tmp_path / "media" / "cartoon"
    download_dir.mkdir(parents=True)
    media_dir.mkdir(parents=True)
    source_file = download_dir / "A.mkv"
    media_file = media_dir / "A.mkv"
    source_file.write_text("movie", encoding="utf-8")
    os.link(source_file, media_file)

    class Downloader:
        def __init__(self):
            self.deleted = []

        def list_torrents(self):
            return [{"hash": "abc", "save_path": str(download_dir)}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    downloader = Downloader()
    plugin = module.SyncRemover()
    plugin._downloaders = {"QB": downloader}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": str(media_file),
            "media_dirs": [str(tmp_path / "media")],
            "download_dirs": [str(tmp_path / "downloads")],
        }
    )
    result = plugin._audit_store.list_records()[0]

    assert result["status"] == "success"
    assert result["match_reason"] == "hardlink_path"
    assert result["download_path"] == str(source_file)
    assert downloader.deleted == [("abc", True)]
    assert not media_file.exists()


def test_plugin_run_once_allows_manual_path_under_scan_root(tmp_path):
    module = load_plugin_module()
    media_dir = tmp_path / "media" / "cartoon"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "A.mkv"
    media_file.write_text("movie", encoding="utf-8")

    class Downloader:
        def __init__(self):
            self.deleted = []

        def list_torrents(self):
            return [{"hash": "abc", "save_path": str(media_dir)}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    downloader = Downloader()
    plugin = module.SyncRemover()
    plugin._downloaders = {"tr": downloader}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": str(media_file),
            "media_dirs": [],
            "download_dirs": [],
            "path_scan_roots": [str(tmp_path / "media")],
            "path_scan_roots_manual": "",
        }
    )
    result = plugin._audit_store.list_records()[0]

    assert result["status"] == "success"
    assert downloader.deleted == [("abc", True)]


def test_plugin_path_guard_failure_reports_allowed_roots():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin.init_plugin(
        {
            "media_dirs": ["/allowed/media"],
            "download_dirs": ["/allowed/download"],
            "path_scan_roots": [],
            "path_scan_roots_manual": "",
        }
    )
    context = module.DeleteContext(
        event_type="manual.run",
        media_paths=["/blocked/media/A.mkv"],
        download_path="/blocked/media/A.mkv",
        confidence="path",
        source="manual",
    )
    match = module.MatchResult(status="matched", downloader_name="tr", downloader=object(), task_ref="abc")

    result = module.DeleteExecutor(plugin._config, plugin._audit_store).execute(context, match)

    assert result["status"] == "failed"
    assert "/allowed/media" in result["reason"]
    assert "/allowed/download" in result["reason"]


def test_plugin_run_once_resets_saved_flag_after_execution():
    module = load_plugin_module()

    class Downloader:
        def list_torrents(self):
            return [{"hash": "abc", "save_path": "/downloads/A"}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            return True

    plugin = module.SyncRemover()
    plugin._downloaders = {"QB": Downloader()}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": "/downloads/A/A.mkv",
            "download_dirs": ["/downloads"],
        }
    )

    assert plugin.get_config()["run_once"] is False


def test_plugin_run_once_writes_plugin_logs_for_success():
    module = load_plugin_module()

    class CaptureLogger:
        def __init__(self):
            self.infos = []
            self.warnings = []

        def info(self, message, *args):
            self.infos.append(message % args if args else message)

        def warning(self, message, *args):
            self.warnings.append(message % args if args else message)

    class Downloader:
        def list_torrents(self):
            return [{"hash": "abc", "save_path": "/downloads/A"}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            return True

    capture = CaptureLogger()
    module.logger = capture
    plugin = module.SyncRemover()
    plugin._downloaders = {"QB": Downloader()}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": "/downloads/A/A.mkv",
            "download_dirs": ["/downloads"],
        }
    )

    assert any("立即执行开始" in message for message in capture.infos)
    assert any("立即执行完成" in message and "success" in message for message in capture.infos)
    assert any("硬链接：0" in message for message in capture.infos)


def test_plugin_run_once_without_target_is_failed_record():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    result = plugin.api_run_once()

    assert result["ok"] is False
    assert result["reason"] == "manual_target_path or whitelist is required"


def test_plugin_run_once_without_target_and_whitelist_writes_warning_log():
    module = load_plugin_module()

    class CaptureLogger:
        def __init__(self):
            self.warnings = []

        def info(self, message, *args):
            pass

        def warning(self, message, *args):
            self.warnings.append(message % args if args else message)

    capture = CaptureLogger()
    module.logger = capture
    plugin = module.SyncRemover()

    plugin.api_run_once()

    assert any("未填写手动执行目标路径，且没有配置白名单" in message for message in capture.warnings)


def test_plugin_run_once_without_target_executes_download_whitelist(tmp_path):
    module = load_plugin_module()
    allowed_dir = tmp_path / "downloads" / "allowed"
    blocked_dir = tmp_path / "downloads" / "blocked"
    allowed_dir.mkdir(parents=True)
    blocked_dir.mkdir(parents=True)
    allowed_file = allowed_dir / "A.mkv"
    blocked_file = blocked_dir / "B.mkv"
    allowed_file.write_text("a", encoding="utf-8")
    blocked_file.write_text("b", encoding="utf-8")

    class Downloader:
        def __init__(self):
            self.deleted = []

        def list_torrents(self):
            return [
                {"hash": "allowed", "save_path": str(allowed_dir)},
                {"hash": "blocked", "save_path": str(blocked_dir)},
            ]

        def list_files(self, task_ref):
            return [{"name": "A.mkv" if task_ref == "allowed" else "B.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    downloader = Downloader()
    plugin = module.SyncRemover()
    plugin._downloaders = {"tr": downloader}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": "",
            "download_dirs": [str(allowed_dir)],
            "media_dirs": [],
            "path_scan_roots_manual": "",
        }
    )

    assert downloader.deleted == [("allowed", True)]
    assert plugin._audit_store.list_records()[0]["status"] == "success"


def test_plugin_run_once_without_target_executes_media_hardlink_whitelist(tmp_path):
    module = load_plugin_module()
    download_dir = tmp_path / "downloads" / "A"
    media_dir = tmp_path / "media" / "cartoon"
    download_dir.mkdir(parents=True)
    media_dir.mkdir(parents=True)
    source_file = download_dir / "A.mkv"
    media_file = media_dir / "A.mkv"
    source_file.write_text("movie", encoding="utf-8")
    os.link(source_file, media_file)

    class Downloader:
        def __init__(self):
            self.deleted = []

        def list_torrents(self):
            return [{"hash": "abc", "save_path": str(download_dir)}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    downloader = Downloader()
    plugin = module.SyncRemover()
    plugin._downloaders = {"tr": downloader}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": "",
            "download_dirs": [],
            "media_dirs": [str(media_dir)],
            "path_scan_roots_manual": "",
        }
    )

    assert downloader.deleted == [("abc", True)]
    assert plugin._audit_store.list_records()[0]["status"] == "success"
    assert not media_file.exists()


def test_plugin_run_once_without_target_executes_media_name_whitelist(tmp_path):
    module = load_plugin_module()
    media_dir = tmp_path / "media" / "cartoon" / "Walking.The.Way.All.Alone.S01.2026.2160p.WEB-DL"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "Walking.The.Way.All.Alone.S01E01.mkv"
    media_file.write_text("movie", encoding="utf-8")

    class Downloader:
        def __init__(self):
            self.deleted = []

        def list_torrents(self):
            return [
                {
                    "hash": "abc",
                    "name": "Walking.The.Way.All.Alone.S01.2026.2160p.WEB-DL",
                    "save_path": "/not-mounted/downloads",
                }
            ]

        def list_files(self, task_ref):
            return [{"name": "Walking.The.Way.All.Alone.S01E01.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    downloader = Downloader()
    plugin = module.SyncRemover()
    plugin._downloaders = {"tr": downloader}
    plugin.init_plugin(
        {
            "run_once": True,
            "manual_target_path": "",
            "download_dirs": [],
            "media_dirs": [str(media_dir)],
            "path_scan_roots_manual": "",
        }
    )
    result = plugin._audit_store.list_records()[0]

    assert downloader.deleted == [("abc", True)]
    assert result["status"] == "success"
    assert result["match_reason"] == "media_name"
    assert media_file.exists()


def test_plugin_scan_paths_api_returns_options(tmp_path):
    module = load_plugin_module()
    plugin = module.SyncRemover()
    media_root = tmp_path / "media"
    download_root = tmp_path / "downloads"
    media_root.mkdir()
    download_root.mkdir()
    plugin.init_plugin({"path_scan_roots": [str(media_root), str(download_root)], "path_scan_depth": 0})

    response = plugin.api_scan_paths()

    assert response["paths"] == [str(download_root), str(media_root)] or response["paths"] == [
        str(media_root),
        str(download_root),
    ]


def test_plugin_audit_api_lists_records():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin._audit_store.add({"status": "success", "reason": "ok"})

    response = plugin.api_audit()

    assert response["records"][0]["status"] == "success"


def test_plugin_dry_run_api_evaluates_payload_without_delete():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin.init_plugin({"enabled": True, "download_dirs": ["/downloads"]})
    downloader = type(
        "Downloader",
        (),
        {
            "list_torrents": lambda self: [{"hash": "abc", "save_path": "/downloads/A"}],
            "list_files": lambda self, task_ref: [{"name": "A.mkv"}],
            "delete_task": lambda self, task_ref, delete_source_data: False,
        },
    )()
    plugin._downloaders = {"QB": downloader}

    response = plugin.api_dry_run({"download_path": "/downloads/A/A.mkv"})

    assert response["status"] == "dry_run"
    assert response["task_ref"] == "abc"


def test_plugin_dry_run_api_reports_all_matching_downloaders():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin.init_plugin({"enabled": True, "download_dirs": ["/downloads"]})

    class Downloader:
        def __init__(self, task_hash):
            self.task_hash = task_hash
            self.deleted = []

        def list_torrents(self):
            return [{"hash": self.task_hash, "save_path": "/downloads/A"}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    qb = Downloader("qb_hash")
    tr = Downloader("tr_hash")
    plugin._downloaders = {"qb": qb, "tr": tr}

    response = plugin.api_dry_run({"download_path": "/downloads/A/A.mkv"})

    assert response["status"] == "dry_run"
    assert response["downloader"] == "qb,tr"
    assert response["task_ref"] == "qb_hash,tr_hash"
    assert qb.deleted == []
    assert tr.deleted == []


def test_plugin_enabled_event_deletes_all_matching_downloaders():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin.init_plugin({"enabled": True, "download_dirs": ["/downloads"]})

    class Downloader:
        def __init__(self, task_hash):
            self.task_hash = task_hash
            self.deleted = []

        def list_torrents(self):
            return [{"hash": self.task_hash, "save_path": "/downloads/A"}]

        def list_files(self, task_ref):
            return [{"name": "A.mkv"}]

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    qb = Downloader("qb_hash")
    tr = Downloader("tr_hash")
    plugin._downloaders = {"qb": qb, "tr": tr}
    event = type(
        "Event",
        (),
        {"event_type": "downloadfile.deleted", "event_data": {"download_path": "/downloads/A/A.mkv"}},
    )()

    response = plugin.handle_delete_event(event)

    assert response["status"] == "success"
    assert response["downloader"] == "qb,tr"
    assert qb.deleted == [("qb_hash", True)]
    assert tr.deleted == [("tr_hash", True)]


def test_plugin_retry_replays_matched_audit_record():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    plugin.init_plugin({"enabled": True, "download_dirs": ["/downloads"]})

    class Downloader:
        def __init__(self):
            self.deleted = []

        def delete_task(self, task_ref, delete_source_data):
            self.deleted.append((task_ref, delete_source_data))
            return True

    downloader = Downloader()
    plugin._downloaders = {"QB": downloader}
    record = plugin._audit_store.add(
        {
            "status": "failed",
            "event_type": "history.deleted",
            "downloader": "QB",
            "task_ref": "abc",
            "match_reason": "hash",
            "media_paths": [],
            "download_path": "/downloads/A/A.mkv",
        }
    )

    response = plugin.api_retry(record["id"])

    assert response["status"] == "success"
    assert downloader.deleted == [("abc", True)]


def test_plugin_handles_disabled_event_as_noop():
    module = load_plugin_module()
    plugin = module.SyncRemover()
    event = type("Event", (), {"event_type": "history.deleted", "event_data": {"title": "A"}})()

    assert plugin.on_delete_event(event) is None


def test_package_v2_contains_syncremover_metadata():
    package_file = Path(__file__).resolve().parents[3] / "package.v2.json"
    package = json.loads(package_file.read_text(encoding="utf-8"))

    assert package["SyncRemover"]["name"] == "同步删除助手"
    assert package["SyncRemover"]["version"] == "0.1.11"
    assert package["SyncRemover"]["level"] == 1

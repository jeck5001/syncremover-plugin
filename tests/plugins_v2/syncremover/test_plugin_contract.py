import importlib.util
import json
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


def test_plugin_exposes_audit_retry_dry_run_and_clear_api():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    apis = plugin.get_api()
    paths = {api["path"] for api in apis}

    assert paths == {"/audit", "/retry", "/dry-run", "/clear-audit", "/scan-paths"}


def test_plugin_form_contains_safety_controls():
    module = load_plugin_module()
    plugin = module.SyncRemover()

    form, defaults = plugin.get_form()
    rendered = str(form)

    assert "删除原始下载数据" in rendered
    assert "硬链接清理范围" in rendered
    assert "媒体目录白名单" in rendered
    assert "下载目录白名单" in rendered
    assert "VCombobox" in rendered
    assert "可选择或手填" in rendered
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
    assert package["SyncRemover"]["version"] == "0.1.1"
    assert package["SyncRemover"]["level"] == 1

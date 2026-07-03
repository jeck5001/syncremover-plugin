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


def test_executor_passes_default_delete_source_data_to_downloader(tmp_path):
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
    assert downloader.calls == [("abc", False)]


def test_executor_rejects_missing_path_guard(tmp_path):
    module = load_plugin_module()
    downloader = FakeDownloader()
    match = module.MatchResult("matched", "qbittorrent", downloader, "abc", {"hash": "abc"}, "hash")
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={**module.DEFAULT_CONFIG, "media_dirs": [], "download_dirs": [], "strict_path_guard": True},
        audit_store=audit,
    )

    result = executor.execute(
        module.DeleteContext("history.deleted", download_path=str(tmp_path / "A.mkv"), confidence="direct_task"),
        match,
    )

    assert result["status"] == "failed"
    assert result["reason"].startswith("path guard rejected delete")
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


def test_executor_deletes_scanned_media_hardlink_for_download_path(tmp_path):
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
            "manual.run",
            media_paths=[],
            download_path=str(download),
            source="manual",
            confidence="direct_task",
        ),
        match,
    )

    assert result["status"] == "success"
    assert result["deleted_hardlinks"] == [str(media)]
    assert media.exists() is False


def test_executor_deletes_remaining_download_file_after_all_downloaders_succeed(tmp_path):
    module = load_plugin_module()
    download = tmp_path / "downloads" / "A.mkv"
    download.parent.mkdir()
    download.write_text("video", encoding="utf-8")

    class RecordingDownloader(FakeDownloader):
        def __init__(self, name, calls):
            super().__init__()
            self.name = name
            self.shared_calls = calls

        def delete_task(self, task_ref, delete_source_data):
            self.shared_calls.append((self.name, task_ref, download.exists()))
            return super().delete_task(task_ref, delete_source_data)

    calls = []
    qb = RecordingDownloader("qb", calls)
    tr = RecordingDownloader("tr", calls)
    matches = [
        module.MatchResult("matched", "qb", qb, "qb_hash", {"hash": "qb_hash"}, "download_path"),
        module.MatchResult("matched", "tr", tr, "tr_hash", {"hash": "tr_hash"}, "download_path"),
    ]
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={
            **module.DEFAULT_CONFIG,
            "media_dirs": [],
            "download_dirs": [str(download.parent)],
            "delete_source_data": True,
        },
        audit_store=audit,
    )

    result = executor.execute_all(
        module.DeleteContext(
            "manual.run",
            media_paths=[],
            download_path=str(download),
            source="manual",
            confidence="path",
        ),
        matches,
    )

    assert result["status"] == "success"
    assert calls == [("qb", "qb_hash", True), ("tr", "tr_hash", True)]
    assert result["deleted_files"] == [str(download)]
    assert download.exists() is False


def test_executor_keeps_download_file_when_any_downloader_delete_fails(tmp_path):
    module = load_plugin_module()
    download = tmp_path / "downloads" / "A.mkv"
    download.parent.mkdir()
    download.write_text("video", encoding="utf-8")
    qb = FakeDownloader()
    tr = FakeDownloader(should_delete=False)
    matches = [
        module.MatchResult("matched", "qb", qb, "qb_hash", {"hash": "qb_hash"}, "download_path"),
        module.MatchResult("matched", "tr", tr, "tr_hash", {"hash": "tr_hash"}, "download_path"),
    ]
    audit = module.AuditStore(limit=10)
    executor = module.DeleteExecutor(
        config={
            **module.DEFAULT_CONFIG,
            "media_dirs": [],
            "download_dirs": [str(download.parent)],
            "delete_source_data": True,
        },
        audit_store=audit,
    )

    result = executor.execute_all(
        module.DeleteContext(
            "manual.run",
            media_paths=[],
            download_path=str(download),
            source="manual",
            confidence="path",
        ),
        matches,
    )

    assert result["status"] == "failed"
    assert download.exists()


def test_executor_resolves_hardlinks_before_downloader_deletes_source(tmp_path):
    module = load_plugin_module()
    download = tmp_path / "downloads" / "A.mkv"
    media = tmp_path / "media" / "A.mkv"
    download.parent.mkdir()
    media.parent.mkdir()
    download.write_text("video", encoding="utf-8")
    media.hardlink_to(download)

    class SourceDeletingDownloader(FakeDownloader):
        def delete_task(self, task_ref, delete_source_data):
            super().delete_task(task_ref, delete_source_data)
            download.unlink()
            return True

    downloader = SourceDeletingDownloader()
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
    assert result["deleted_hardlinks"] == [str(media)]
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

    result = executor.execute(
        module.DeleteContext("history.deleted", media_paths=[str(media)], confidence="direct_task"),
        match,
    )

    assert result["status"] == "failed"
    assert media.exists()

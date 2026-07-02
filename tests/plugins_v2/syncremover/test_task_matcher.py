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

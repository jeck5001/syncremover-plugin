from test_plugin_contract import load_plugin_module


class FakeTorrent:
    id = 7
    hashString = "trhash"
    name = "TR Movie"
    download_dir = "/downloads/tr"


class FakeFile:
    name = "TR Movie.mkv"


class FakeHostDownloader:
    def __init__(self):
        self.deleted = []

    def get_torrents(self):
        return ([{"hash": "qbhash", "name": "QB Movie", "save_path": "/downloads/qb"}, FakeTorrent()], False)

    def get_files(self, task_ref):
        if task_ref == "qbhash":
            return [{"name": "QB Movie.mkv"}]
        return [FakeFile()]

    def delete_torrents(self, delete_file, ids):
        self.deleted.append((delete_file, ids))
        return True


def test_host_downloader_adapter_normalizes_torrents_and_files():
    module = load_plugin_module()
    host = FakeHostDownloader()
    adapter = module.HostDownloaderAdapter("mixed", host)

    torrents = adapter.list_torrents()
    files = adapter.list_files("trhash")

    assert torrents[0]["hash"] == "qbhash"
    assert torrents[1]["id"] == 7
    assert torrents[1]["hashString"] == "trhash"
    assert torrents[1]["download_dir"] == "/downloads/tr"
    assert files == [{"name": "TR Movie.mkv"}]


def test_host_downloader_adapter_deletes_with_source_data_flag():
    module = load_plugin_module()
    host = FakeHostDownloader()
    adapter = module.HostDownloaderAdapter("qbittorrent", host)

    assert adapter.delete_task("qbhash", delete_source_data=True) is True
    assert host.deleted == [(True, "qbhash")]


def test_build_host_downloaders_uses_enabled_service_types():
    module = load_plugin_module()
    host = FakeHostDownloader()
    service = type("ServiceInfo", (), {"name": "QB", "type": "qbittorrent", "instance": host})()
    helper = type("Helper", (), {"get_services": lambda self, type_filter=None: {"QB": service}})()

    downloaders = module.build_host_downloaders(["qbittorrent"], helper_factory=lambda: helper)

    assert list(downloaders) == ["QB"]
    assert downloaders["QB"].delete_task("qbhash", True) is True

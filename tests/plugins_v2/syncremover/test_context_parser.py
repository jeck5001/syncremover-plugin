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

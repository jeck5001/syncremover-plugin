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

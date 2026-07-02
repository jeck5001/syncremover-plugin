from test_plugin_contract import load_plugin_module


def test_path_scanner_returns_existing_roots_and_children(tmp_path):
    module = load_plugin_module()
    media_root = tmp_path / "media"
    movies = media_root / "movies"
    downloads = tmp_path / "downloads"
    movies.mkdir(parents=True)
    downloads.mkdir()

    scanner = module.PathScanner(common_roots=[str(media_root), str(downloads), str(tmp_path / "missing")], max_depth=1)

    result = scanner.scan()

    assert str(media_root) in result
    assert str(movies) in result
    assert str(downloads) in result
    assert str(tmp_path / "missing") not in result


def test_path_scanner_limits_depth(tmp_path):
    module = load_plugin_module()
    root = tmp_path / "root"
    allowed = root / "allowed"
    too_deep = allowed / "too_deep"
    too_deep.mkdir(parents=True)

    scanner = module.PathScanner(common_roots=[str(root)], max_depth=1)

    result = scanner.scan()

    assert str(root) in result
    assert str(allowed) in result
    assert str(too_deep) not in result

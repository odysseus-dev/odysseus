from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "static/js/cookbookServe.js"


def test_cached_model_refresh_keeps_same_server_rows_visible():
    source = SOURCE.read_text(encoding="utf-8")

    preserve = source.index("preserveRows = hasModelRows")
    clear = source.index("if (!preserveRows) list.innerHTML = ''", preserve)
    assert preserve < clear
    assert "cookbook-cached-scan-loading" in source
    assert "Refreshing cached models…" in source
    assert "cookbook-cached-scan-error" in source
    assert "Refresh failed:" in source
    assert "list.dataset.cookbookScanSig = scanSig" in source


def test_cached_model_scan_only_latest_request_can_render():
    source = SOURCE.read_text(encoding="utf-8")

    assert "let _cachedModelsFetchId = 0;" in source
    assert "const fetchId = ++_cachedModelsFetchId;" in source
    assert source.count("fetchId !== _cachedModelsFetchId") >= 2
    assert "fetchId === _cachedModelsFetchId && list.querySelector('.serve-empty-auto-scan')" in source


def test_cached_model_titles_share_a_detected_provider_color():
    source = SOURCE.read_text(encoding="utf-8")

    assert "const _mc = modelColor(m._family || m.repo_id) || '';" in source


def test_cached_model_scan_plays_one_shot_domino_entrance():
    source = SOURCE.read_text(encoding="utf-8")

    assert "function _playCachedModelDomino(list)" in source
    assert "_playCachedModelDomino(list);" in source
    assert "cookbook-serve-models-just-loaded" in source
    assert "clearTimeout(list._serveModelDominoTimer);" in source

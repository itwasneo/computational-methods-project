"""Read-only catalog confinement and lifecycle checks, independent of web extras."""

import os

import pytest

from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.workspace import WorkspaceCatalog


@pytest.fixture
def catalog(tmp_path):
    (tmp_path / "b.jsonl").write_text("not yet validated\n")
    (tmp_path / "a.json").write_text("{}")
    with_catalog = WorkspaceCatalog(tmp_path)
    with_catalog.refresh()
    try:
        yield with_catalog
    finally:
        with_catalog.close()


def test_listing_candidates_pagination_and_no_writes(catalog, tmp_path):
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    first = catalog.page(limit=1)
    assert first["items"][0]["display_name"] == "a.json"
    assert first["items"][0]["kind"] == "json"
    assert first["items"][0]["validation"] == "unvalidated"
    second = catalog.page(limit=1, cursor=first["next_cursor"])
    assert second["items"][0]["kind"] == "result_shard"
    assert second["next_cursor"] is None
    assert str(tmp_path) not in str(first)
    with catalog.open_source(first["items"][0]["artifact_handle"], first["revision"]) as stream:
        assert stream.read() == b"{}"
    assert stream.closed
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("limit", [0, 501, True, 1.5, "2"])
def test_invalid_page_bounds(catalog, limit):
    with pytest.raises(InterfaceError, match="Page size"):
        catalog.page(limit=limit)


def test_invalid_stale_and_wrong_session_cursors(catalog, tmp_path):
    cursor = catalog.page(limit=1)["next_cursor"]
    with pytest.raises(InterfaceError) as error:
        catalog.page(limit=2, cursor=cursor)
    assert error.value.code == "cursor_invalid"
    with pytest.raises(InterfaceError):
        catalog.page(limit=1, cursor=cursor[:-3] + "xxx")
    other = WorkspaceCatalog(tmp_path)
    try:
        other.refresh()
        with pytest.raises(InterfaceError):
            other.page(limit=1, cursor=cursor)
    finally:
        other.close()
    catalog.refresh()
    with pytest.raises(InterfaceError):
        catalog.page(limit=1, cursor=cursor)


@pytest.mark.parametrize("change", ["modify", "delete", "replace", "add"])
def test_changes_invalidate_catalog_until_refresh(catalog, tmp_path, change):
    old = catalog.page()
    path = tmp_path / "a.json"
    if change == "modify":
        path.write_text('{"changed":true}')
    elif change == "delete":
        path.unlink()
    elif change == "replace":
        replacement = tmp_path / "new"
        replacement.write_text("{}")
        replacement.replace(path)
    else:
        (tmp_path / "c.json").write_text("{}")
    with pytest.raises(InterfaceError) as error:
        catalog.page()
    assert error.value.code == "stale_revision"
    catalog.refresh()
    assert catalog.page()["revision"] != old["revision"]
    with pytest.raises(InterfaceError):
        with catalog.open_source(old["items"][0]["artifact_handle"], old["revision"]):
            pytest.fail("old catalog handles must not open")


def test_hidden_symlink_special_and_unsupported_files_are_not_candidates(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.json").write_text("secret")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "file.json").symlink_to(outside / "private.json")
    (root / "directory").symlink_to(outside, target_is_directory=True)
    (root / ".hidden").mkdir()
    (root / ".hidden/private.json").write_text("secret")
    (root / ".secret.json").write_text("secret")
    (root / "notes.txt").write_text("unsupported")
    os.mkfifo(root / "pipe.json")
    (root / "normal.jsonl").write_text("candidate")
    catalog = WorkspaceCatalog(root)
    try:
        catalog.refresh()
        assert [item["display_name"] for item in catalog.page()["items"]] == ["normal.jsonl"]
        for handle in ["../outside/private.json", str(outside / "private.json"), "unknown"]:
            with pytest.raises(InterfaceError) as error:
                catalog.select_sources([handle], None, catalog.page()["revision"])
            assert error.value.code == "artifact_not_found"
    finally:
        catalog.close()


def test_scan_limits_fail_without_publishing_partial_catalog(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    catalog = WorkspaceCatalog(tmp_path, max_entries=1, max_depth=1)
    try:
        catalog.refresh()
        (tmp_path / "b.json").write_text("{}")
        with pytest.raises(InterfaceError) as error:
            catalog.refresh()
        assert error.value.code == "catalog_limit_exceeded"
        with pytest.raises(InterfaceError):
            catalog.page()
    finally:
        catalog.close()
    (tmp_path / "nested/deep").mkdir(parents=True)
    catalog = WorkspaceCatalog(tmp_path, max_depth=1)
    try:
        with pytest.raises(InterfaceError, match="depth"):
            catalog.refresh()
    finally:
        catalog.close()


def test_open_time_symlink_replacement_fails_closed(catalog, tmp_path, monkeypatch):
    page = catalog.page()
    item = page["items"][0]
    outside = tmp_path.parent / f"private-{tmp_path.name}.json"
    outside.write_text("must not read")
    original_open = os.open
    replaced = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == "a.json" and not replaced:
            replaced = True
            (tmp_path / "a.json").unlink()
            (tmp_path / "a.json").symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(InterfaceError) as error:
        with catalog.open_source(item["artifact_handle"], page["revision"]):
            pytest.fail("replacement symlink must not open")
    assert error.value.code == "stale_revision"
    assert str(outside) not in str(error.value)


def test_busy_operation_and_close(catalog):
    with catalog._operation():
        with pytest.raises(InterfaceError) as error:
            catalog.page()
        assert error.value.code == "catalog_busy"
    fd = catalog._fd
    catalog.close()
    catalog.close()
    with pytest.raises(OSError):
        os.fstat(fd)
    with pytest.raises(InterfaceError):
        catalog.page()


def test_root_replacement_and_invalid_startup_are_sanitized(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    catalog = WorkspaceCatalog(root)
    try:
        catalog.refresh()
        root.rename(tmp_path / "old")
        root.mkdir()
        with pytest.raises(InterfaceError):
            catalog.page()
    finally:
        catalog.close()
    for path in [tmp_path / "missing", __file__]:
        with pytest.raises(InterfaceError) as error:
            WorkspaceCatalog(path)
        assert str(path) not in str(error.value)

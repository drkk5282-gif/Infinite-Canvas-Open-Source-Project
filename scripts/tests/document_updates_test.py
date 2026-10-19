"""Incremental storage regression tests, using only a temporary data directory."""
import json
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
temporary_data = tempfile.TemporaryDirectory(prefix="canvas-save-tests-")
os.environ["DOUBAO_DATA_DIR"] = temporary_data.name

from backend import canvas_store, workbench_store, board_assets, workbench_assets
from backend.document_updates import save_changed
from backend.json_io import load_json
from backend.storage_epoch import epoch_path, storage_epoch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class DocumentUpdatesTest(unittest.TestCase):
    sequence = 0

    def setUp(self):
        type(self).sequence += 1
        self.user = f"test-user-{self.sequence}"

    def exercise(self, kind):
        store = canvas_store if kind == "canvas" else workbench_store
        field = "boards" if kind == "canvas" else "workbenches"
        row_path = board_assets.board_json_path if kind == "canvas" else workbench_assets.workbench_json_path
        a = {"id": "first", "name": "First", "nodes": [], "episodes": [{"id": "episode", "clips": [1, 2]}]}
        b = {"id": "second", "name": "Second", "nodes": [], "episodes": []}
        store.save_owned(self.user, [a, b])
        untouched = row_path(self.user, "second")
        before = (untouched.stat().st_mtime_ns, untouched.read_bytes())
        a["name"] = "Renamed"
        a["episodes"][0]["clips"] = [1]
        a["viewport"] = {"x": 120, "y": -12, "scale": 0.8}
        result = save_changed(self.user, [a], kind=kind)
        self.assertEqual(result["savedIds"], ["first"])
        self.assertNotIn(field, result)  # Small acknowledgement, no full list.
        self.assertEqual((untouched.stat().st_mtime_ns, untouched.read_bytes()), before)
        saved = load_json(row_path(self.user, "first"))
        self.assertEqual(saved["name"], "Renamed")
        self.assertEqual(saved["episodes"][0]["clips"], [1])
        self.assertEqual(saved["viewport"]["x"], 120)
        self.assertTrue(row_path(self.user, "first").with_suffix(".json.bak").is_file())
        self.assertEqual(len(store.load_owned(self.user)[field]), 2)

    def test_canvas_changes_preserve_other_documents_and_backups(self):
        self.exercise("canvas")

    def test_workbench_changes_preserve_other_documents_and_backups(self):
        self.exercise("workbench")

    def test_legacy_inline_index_keeps_untouched_content(self):
        canvas_store._ensure_canvas_file(self.user)
        path = canvas_store._canvas_path(self.user)
        path.write_text(json.dumps({"boards": [{"id": "old", "name": "Old", "nodes": [{"id": "node", "prompt": "keep"}]}]}), encoding="utf-8")
        save_changed(self.user, [{"id": "new", "name": "New", "nodes": []}], kind="canvas")
        data = canvas_store.load_owned(self.user)
        self.assertEqual({b["id"] for b in data["boards"]}, {"old", "new"})
        self.assertEqual(next(b for b in data["boards"] if b["id"] == "old")["nodes"][0]["prompt"], "keep")

    def test_shared_permission_is_checked_before_any_write(self):
        for kind, store, checker in (("canvas", canvas_store, "user_can_edit_shared_board"), ("workbench", workbench_store, "user_has_access")):
            with patch.object(store, checker, return_value=False), patch.object(store, "_user_save_lock") as lock:
                with self.assertRaises(PermissionError):
                    save_changed(self.user, [{"id": "own", "nodes": []}, {"id": "shared", "isOwned": False, "shareOwnerId": "other"}], kind=kind)
                lock.assert_not_called()

    def test_invalid_ids_and_duplicate_rows_are_rejected(self):
        for rows in ([{"id": "../outside"}], [{"id": "a"}, {"id": "a"}], [None]):
            with self.assertRaises(ValueError):
                save_changed(self.user, rows, kind="canvas")

    def test_storage_epoch_changes_when_server_data_marker_is_removed(self):
        first = storage_epoch(self.user)
        self.assertEqual(storage_epoch(self.user), first)
        marker = epoch_path(self.user)
        self.assertTrue(marker.is_relative_to(Path(temporary_data.name).resolve()))
        marker.unlink()
        second = storage_epoch(self.user)
        self.assertNotEqual(second, first)

    def test_actual_patch_routes_keep_unchanged_rows(self):
        # Compile the real two route definitions without starting application
        # lifecycle jobs or loading any real account data.
        main = Path(__file__).resolve().parents[2] / "backend/main.py"
        tree = ast.parse(main.read_text(encoding="utf-8"))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"api_save_canvas_changes", "api_save_workbench_changes"}]
        app = FastAPI()
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(main), "exec"), {"app": app, "HTTPException": HTTPException})
        with patch("backend.auth_deps.require_login_user", return_value={"id": self.user}):
            with TestClient(app) as client:
                for kind, field in (("canvas", "boards"), ("workbench", "workbenches")):
                    response = client.patch(f"/api/{kind}", json={field: [{"id": "one", "nodes": [], "episodes": []}]})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["savedIds"], ["one"])
                    self.assertRegex(response.json()["storageEpoch"], r"^[0-9a-f]{32}$")
                    self.assertNotIn(field, response.json())
                    self.assertEqual(client.patch(f"/api/{kind}", json={field: "bad"}).status_code, 400)
                    self.assertEqual(client.patch(f"/api/{kind}", json={field: [{"id": "../bad"}]}).status_code, 400)


if __name__ == "__main__":
    try:
        program = unittest.main(exit=False)
    finally:
        temporary_data.cleanup()
    sys.exit(not program.result.wasSuccessful())

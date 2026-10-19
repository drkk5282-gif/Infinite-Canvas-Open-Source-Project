"""Offline regressions. Run with a temporary DOUBAO_DATA_DIR, never live jobs."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# The test launcher supplies a disposable directory, inherited by child processes.
if not os.environ.get("DOUBAO_TEST_DATA_DIR"):
    raise RuntimeError("Set DOUBAO_TEST_DATA_DIR to a disposable test directory")
os.environ["DOUBAO_DATA_DIR"] = os.environ["DOUBAO_TEST_DATA_DIR"]

import httpx
from backend import task_manager
from backend.job_worker import jobs, poll_ledger, callback, client, reconcile, runner
from backend.models import TaskStatus
from backend.myapi import video_poll, client as myapi
from backend.myapi.common import MyApiUpstreamError


def _write_jobs(directory, number):
    jobs.JOBS_FILE = Path(directory) / "jobs.json"
    for i in range(12):
        jobs.upsert_job({"id": f"{number}-{i}", "status": "pending"})
        jobs.update_job("shared", **{f"field_{number}_{i}": i})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        for target, field, value in (
            (task_manager, "_tasks_path", lambda: path / "tasks.json"),
            (task_manager, "VIDEO_TASKS_FILE", path / "legacy.json"),
            (jobs, "JOBS_FILE", path / "jobs.json"),
            (poll_ledger, "LEDGER_DIR", path / "ledger"),
        ):
            patcher = mock.patch.object(target, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self, **kwargs):
        return task_manager.create_task("myapi", "test", "p", "1:1", kind="text",
                                        board_id="board", node_id=kwargs.pop("node_id", "node"), **kwargs)

    def test_concurrent_clicks_create_exactly_one_task(self):
        def click(_):
            try:
                return self.create().id
            except task_manager.ExistingNodeTask as exc:
                return exc.task.id
        with ThreadPoolExecutor(max_workers=12) as pool:
            ids = list(pool.map(click, range(30)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(task_manager.list_tasks()), 1)

    def test_recoverable_failure_blocks_new_submit_but_final_failure_allows_it(self):
        task = self.create()
        poll_ledger.write_record(job_id=task.id, kind="text", model="m", upstream_task_id="u")
        task_manager.update_task(task.id, status=TaskStatus.failed)
        with self.assertRaises(task_manager.ExistingNodeTask):
            self.create()
        poll_ledger.update_record(task.id, state="upstream_failed")
        self.assertNotEqual(self.create().id, task.id)

    def test_local_wait_timeout_allows_new_submit_and_heals_polling_ledger(self):
        task = self.create()
        poll_ledger.write_record(job_id=task.id, kind="video", model="m", upstream_task_id="up-1")
        task_manager.update_task(
            task.id,
            status=TaskStatus.failed,
            error="等待本机任务 up-1 超时（>3600s）",
        )
        replacement = self.create()
        self.assertNotEqual(replacement.id, task.id)
        self.assertEqual(poll_ledger.get_record(task.id)["state"], "query_failed")
        self.assertEqual(poll_ledger.get_record(task.id)["upstream_task_id"], "up-1")

    def test_definitive_missing_upstream_heals_stale_ledger_and_allows_new_submit(self):
        task = self.create()
        poll_ledger.write_record(job_id=task.id, kind="video", model="m", upstream_task_id="gone")
        task_manager.update_task(
            task.id,
            status=TaskStatus.failed,
            error='查询任务失败：{"detail":"任务不存在或不属于当前本地账户"}',
        )

        replacement = self.create()

        self.assertNotEqual(replacement.id, task.id)
        self.assertEqual(poll_ledger.get_record(task.id)["state"], "upstream_failed")

    def test_workbench_and_board_scopes_are_independent(self):
        first = self.create()
        second = self.create(workbench_id="workbench")
        third = self.create(node_id="other")
        self.assertEqual(len({first.id, second.id, third.id}), 3)

    def test_old_history_cannot_recheck_over_a_new_active_task(self):
        old = self.create()
        task_manager.update_task(old.id, status=TaskStatus.failed)
        self.create()
        with self.assertRaises(task_manager.ExistingNodeTask):
            task_manager.begin_recheck(old.id)

    def test_late_fail_cancel_progress_do_not_undo_success_or_refund(self):
        task = self.create()
        payload = {"task_id": task.id, "kind": "text", "result": {"text": "done"}}
        with mock.patch.object(callback, "_sync_gen_record") as records, \
             mock.patch("backend.points.commit") as commit, mock.patch("backend.points.refund") as refund:
            callback.apply_worker_callback({**payload, "status": "completed"})
            for state in ("failed", "cancelled", "processing", "completed"):
                result = callback.apply_worker_callback({**payload, "status": state})
                self.assertEqual(result["applied"], "ignored_terminal")
            self.assertEqual(records.call_count, 1)
            self.assertEqual(commit.call_count, 1)
            refund.assert_not_called()
        self.assertEqual(task_manager.get_task(task.id).status, TaskStatus.completed)

    def test_old_poll_attempt_cannot_fail_recheck(self):
        task = self.create()
        task_manager.update_task(task.id, status=TaskStatus.failed)
        current, claimed = task_manager.begin_recheck(task.id)
        self.assertTrue(claimed)
        self.assertEqual(current.poll_attempt, 1)
        self.assertFalse(task_manager.begin_recheck(task.id)[1])
        out = callback.apply_worker_callback({"task_id": task.id, "status": "failed", "poll_attempt": 0})
        self.assertEqual(out["applied"], "ignored_stale_attempt")
        self.assertEqual(task_manager.get_task(task.id).status, TaskStatus.generating)

    def test_cross_process_job_writes_preserve_all_rows_and_fields(self):
        jobs.upsert_job({"id": "shared", "status": "processing"})
        context = multiprocessing.get_context("spawn")
        children = [context.Process(target=_write_jobs, args=(self.temp.name, n)) for n in range(3)]
        for child in children:
            child.start()
        for child in children:
            child.join(25)
            if child.is_alive():
                child.terminate()
                child.join()
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(len(jobs.list_open_jobs()), 37)
        shared = jobs.get_job("shared")
        self.assertEqual(len([k for k in shared if k.startswith("field_")]), 36)

    def test_old_callback_ack_cannot_ack_new_attempt(self):
        jobs.upsert_job({"id": "job", "status": "processing", "poll_attempt": 1, "callback_delivered": False})
        jobs.update_job("job", callback_delivered=True, expected_poll_attempt=0)
        self.assertFalse(jobs.get_job("job")["callback_delivered"])


class Style:
    style_id = "test"
    def task_pending(self): return {"pending", "processing"}
    def task_done(self): return {"completed"}
    def task_fail(self): return {"failed"}
    def extract_status(self, data): return data.get("status", "")
    def extract_video_url(self, data): return data.get("video_url", "")
    def extract_image_url(self, data): return data.get("image_url", "")
    def extract_task_error(self, data): return data.get("error", "")
    def video_poll_url(self, base, task_id, url=""): return base + "/task/" + task_id
    image_poll_url = video_poll_url
    def video_poll_headers(self, headers): return headers


class PollTests(unittest.IsolatedAsyncioTestCase):
    async def test_recheck_api_preserves_id_and_passes_attempt_to_worker(self):
        from backend.main import api_recheck_task
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(task_manager, "_tasks_path", lambda: root / "tasks.json"), \
                 mock.patch.object(poll_ledger, "LEDGER_DIR", root / "ledger"), \
                 mock.patch("backend.auth_deps.require_login_user", return_value={"id": "u"}), \
                 mock.patch.object(client, "require_worker", mock.AsyncMock()), \
                 mock.patch.object(client, "recheck_job", mock.AsyncMock(return_value={"ok": True})) as recheck:
                task = task_manager.create_task("myapi", "m", "p", "auto", board_id="b", node_id="n")
                task_manager.update_task(task.id, status=TaskStatus.failed)
                poll_ledger.write_record(job_id=task.id, kind="video", model="m", upstream_task_id="original")
                out = await api_recheck_task(task.id)
                self.assertEqual(out["task_id"], task.id)
                recheck.assert_awaited_once_with(task.id, poll_attempt=1)
                self.assertEqual(task_manager.get_task(task.id).status, TaskStatus.generating)
                await api_recheck_task(task.id)
                self.assertEqual(recheck.await_count, 1)

    async def test_recheck_worker_preserves_upstream_identity_and_callback_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(jobs, "JOBS_FILE", root / "jobs.json"), \
                 mock.patch.object(poll_ledger, "LEDGER_DIR", root / "ledger"), \
                 mock.patch.object(runner, "enqueue_poll_resume", mock.AsyncMock()) as resume, \
                 mock.patch.object(runner, "_post_callback", mock.AsyncMock(return_value="ok")) as send:
                poll_ledger.write_record(job_id="job", kind="video", model="m", upstream_task_id="original")
                jobs.upsert_job({"id": "job", "task_id": "job", "status": "failed", "channel": "myapi"})
                self.assertTrue(await runner.enqueue_failed_recheck("job", poll_attempt=2))
                resume.assert_awaited_once_with("job")
                self.assertEqual(poll_ledger.get_record("job")["upstream_task_id"], "original")
                self.assertEqual(jobs.get_job("job")["poll_attempt"], 2)
                jobs.update_job("job", status="completed", result={"video_url": "https://media/video"})
                await runner.deliver_callback("job")
                self.assertEqual(send.call_args.args[0]["poll_attempt"], 2)

    async def test_browser_offline_keeps_binding_without_api_ledger(self):
        from backend.models import MediaTask
        task = MediaTask(id="job", kind="video", account_id="browser-account", account_name="b", prompt="p", ratio="auto",
                         status="generating", created_at=(datetime.now()-timedelta(minutes=15)).isoformat())
        with mock.patch.object(task_manager, "update_task") as update:
            self.assertFalse(await reconcile.reconcile_myapi_task(task, user_id="u", worker_available=False))
            update.assert_not_called()

    async def test_html_gateway_error_retries_same_id_then_succeeds(self):
        for kind in ("image", "video"):
            seen = []
            def transport(req):
                seen.append((req.method, req.url.path))
                if len(seen) == 1:
                    return httpx.Response(502, text="<html>gateway failed</html>")
                return httpx.Response(200, json={"status": "completed", f"{kind}_url": "https://media/result"})
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
                out = await getattr(video_poll, "poll_" + kind + "_task")(
                    http, style=Style(), base_url="https://test", headers={}, task_id="original", interval_s=0, timeout_s=1)
            self.assertTrue(out[kind + "_url"])
            self.assertEqual(seen, [("GET", "/task/original")] * 2)

    async def test_completed_without_url_waits_for_same_task_output(self):
        rows = [(200, {"status": "completed"}, ""),
                (200, {"status": "completed", "video_url": "https://media/v"}, "")]
        with mock.patch.object(video_poll, "get_video_task_once", mock.AsyncMock(side_effect=rows)) as get:
            out = await video_poll.poll_video_task(object(), style=Style(), base_url="x", headers={}, task_id="original", interval_s=0, timeout_s=1)
        self.assertEqual(get.await_count, 2)
        self.assertTrue(out["video_url"])

    async def test_request_and_backoff_respect_deadline(self):
        async def hung(*args, **kwargs):
            await asyncio.sleep(5)
        for response in (hung, httpx.ConnectError("offline")):
            with mock.patch.object(video_poll, "get_video_task_once", mock.AsyncMock(side_effect=response)):
                started = time.monotonic()
                with self.assertRaises(MyApiUpstreamError):
                    await video_poll.poll_video_task(object(), style=Style(), base_url="x", headers={}, task_id="t", interval_s=10, timeout_s=.04)
                self.assertLess(time.monotonic() - started, .5)

    async def test_failed_poll_keeps_identity_and_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(poll_ledger, "LEDGER_DIR", Path(directory)):
            for final in (False, True):
                async def fail(http, progress):
                    raise MyApiUpstreamError("query failed", raw_response={"code": "original"}, upstream_terminal=final)
                with self.assertRaises(MyApiUpstreamError):
                    await myapi._poll_with_ledger(kind="video", client_task_id="job", model_cfg={"id": "m"},
                        style=Style(), base_url="https://test", headers={}, upstream_task_id="original",
                        poll_url="", cancel_check=None, poll_coro_factory=fail)
                rec = poll_ledger.get_record("job")
                self.assertEqual(rec["upstream_task_id"], "original")
                self.assertEqual(rec["state"], "upstream_failed" if final else "query_failed")
                self.assertEqual(rec["raw_response"], {"code": "original"})
                self.assertTrue(runner._keep_failed_poll_ledger("job", "myapi"))



    async def test_worker_404_differs_from_transport_and_malformed_response(self):
        real = httpx.AsyncClient
        for response in (httpx.Response(404), httpx.Response(500), httpx.Response(200, text="oops"), httpx.Response(200, json={})):
            with mock.patch.object(client, "_headers", return_value={}), \
                 mock.patch.object(client.httpx, "AsyncClient", side_effect=lambda **kw: real(transport=httpx.MockTransport(lambda r: response), **kw)):
                if response.status_code == 404:
                    self.assertIsNone(await client.get_job("job"))
                else:
                    with self.assertRaises(client.WorkerQueryError):
                        await client.get_job("job")

    async def test_query_error_does_not_fail_old_running_task(self):
        from backend.models import MediaTask
        task = MediaTask(id="job", kind="video", account_id="myapi", account_name="m", prompt="p", ratio="auto",
                         status="generating", created_at=(datetime.now()-timedelta(minutes=15)).isoformat())
        with mock.patch.object(client, "get_job", mock.AsyncMock(side_effect=client.WorkerQueryError("offline"))), \
             mock.patch.object(task_manager, "update_task") as update:
            self.assertFalse(await reconcile.reconcile_myapi_task(task, user_id="u", worker_available=True))
            update.assert_not_called()

    async def test_processing_reply_is_authoritative_during_slow_image_post(self):
        from backend.models import MediaTask
        task = MediaTask(id="job", kind="image", account_id="myapi", account_name="m", prompt="p", ratio="auto",
                         status="generating", created_at=(datetime.now()-timedelta(minutes=5)).isoformat())
        job = {"id": "job", "kind": "image", "status": "processing", "created_at": task.created_at}
        with mock.patch.object(client, "get_job", mock.AsyncMock(return_value=job)), \
             mock.patch.object(task_manager, "update_task") as update:
            self.assertFalse(await reconcile.reconcile_myapi_task(task, user_id="u", worker_available=True))
            update.assert_not_called()

    async def test_missing_worker_shell_uses_recheck_of_original_upstream(self):
        from backend.models import MediaTask
        task = MediaTask(id="job", kind="video", account_id="myapi", account_name="m", prompt="p", ratio="auto",
                         status="generating", created_at=datetime.now().isoformat(), poll_attempt=2)
        with mock.patch.object(client, "get_job", mock.AsyncMock(return_value=None)), \
             mock.patch.object(poll_ledger, "get_record", return_value={"upstream_task_id": "original"}), \
             mock.patch.object(client, "recheck_job", mock.AsyncMock()) as recheck, \
             mock.patch.object(task_manager, "update_task") as update:
            await reconcile.reconcile_myapi_task(task, user_id="u", worker_available=True)
            recheck.assert_awaited_once_with("job", poll_attempt=2)
            update.assert_not_called()

    async def test_lost_recheck_request_is_redelivered_with_same_attempt(self):
        from backend.models import MediaTask
        task = MediaTask(id="job", kind="video", account_id="myapi", account_name="m", prompt="p", ratio="auto",
                         status="generating", created_at=datetime.now().isoformat(), poll_attempt=2)
        with mock.patch.object(client, "get_job", mock.AsyncMock(return_value={"id": "job", "status": "failed", "poll_attempt": 1})), \
             mock.patch.object(client, "recheck_job", mock.AsyncMock()) as recheck, \
             mock.patch.object(task_manager, "update_task") as update:
            await reconcile.reconcile_myapi_task(task, user_id="u", worker_available=True)
            recheck.assert_awaited_once_with("job", poll_attempt=2)
            update.assert_not_called()

    async def test_repeated_recheck_delivery_does_not_restart_running_query(self):
        with mock.patch.object(poll_ledger, "get_record", return_value={"upstream_task_id": "original"}), \
             mock.patch.object(jobs, "get_job", return_value={"id": "job", "status": "processing", "poll_attempt": 2}), \
             mock.patch.object(runner, "_running", {"job"}), \
             mock.patch.object(jobs, "update_job") as update, \
             mock.patch.object(runner, "enqueue_poll_resume", mock.AsyncMock()) as resume:
            self.assertTrue(await runner.enqueue_failed_recheck("job", poll_attempt=2))
            update.assert_not_called()
            resume.assert_not_awaited()

    async def test_duplicate_finished_worker_submit_never_enqueues(self):
        from backend.job_worker.app import api_submit_job
        from fastapi import BackgroundTasks
        pending = BackgroundTasks()
        with mock.patch("backend.job_worker.app._verify_token"), \
             mock.patch.object(jobs, "get_job", return_value={"id": "t", "status": "completed"}), \
             mock.patch.object(jobs, "upsert_job") as put:
            out = await api_submit_job(pending, {"task_id": "t", "user_id": "u", "kind": "video"})
        self.assertTrue(out["duplicate"])
        put.assert_not_called()
        self.assertEqual(pending.tasks, [])


if __name__ == "__main__":
    unittest.main()

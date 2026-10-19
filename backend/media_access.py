"""Per-user access control for /uploads/ and local media references."""



from __future__ import annotations



import logging

from pathlib import Path

from typing import Any



from fastapi import HTTPException, Request



from backend.board_assets import (

    _FLAT_UPLOAD_RE,

    _NESTED_UPLOAD_RE,

    _WORKBENCH_UPLOAD_RE,

    collect_board_refs,

    find_asset_by_filename,

    resolve_upload_path,

    upload_ref_from_url,

)

from backend.config import UPLOADS_DIR



logger = logging.getLogger(__name__)





def slug_to_user_id(slug: str) -> str | None:

    from backend import app_users



    key = (slug or "").strip()

    if not key:

        return None

    row = app_users.get_user_by_username(key)

    if row:

        return str(row["id"])

    row = app_users.get_user(key)

    if row:

        return str(row["id"])

    # Legacy folder name "default" before username lookup existed.

    if key == "default":

        return "default"

    return None





def path_owner_board(path: Path) -> tuple[str, str] | None:

    parts = path.parts

    try:

        users_idx = parts.index("users")

    except ValueError:

        return None

    if users_idx + 3 >= len(parts):

        return None

    slug = parts[users_idx + 1]

    third = parts[users_idx + 2]

    if third == "boards":

        if users_idx + 4 >= len(parts):

            return None

        board_id = parts[users_idx + 3]

    else:

        board_id = third

    owner_id = slug_to_user_id(slug)

    if not owner_id or not board_id:

        return None

    return owner_id, board_id





def user_has_board_access(user_id: str, owner_id: str, board_id: str) -> bool:

    from backend import canvas_store



    return canvas_store.user_has_board_access(user_id, owner_id, board_id)





def user_has_workbench_access(user_id: str, owner_id: str, workbench_id: str) -> bool:
    return False





def path_owner_workbench(path: Path) -> tuple[str, str] | None:

    parts = path.parts

    try:

        users_idx = parts.index("users")

        wb_idx = parts.index("workbenches")

    except ValueError:

        return None

    if wb_idx + 1 >= len(parts):

        return None

    slug = parts[users_idx + 1]

    workbench_id = parts[wb_idx + 1]

    owner_id = slug_to_user_id(slug)

    if not owner_id or not workbench_id:

        return None

    return owner_id, workbench_id





def _refs_match(url: str, item: str) -> bool:

    raw = (url or "").strip()

    other = (item or "").strip()

    if not raw or not other:

        return False

    if other == raw:

        return True

    ref = upload_ref_from_url(raw) or raw

    if upload_ref_from_url(other) == ref:

        return True

    filename = raw.rsplit("/", 1)[-1].split("?", 1)[0]

    return bool(filename and other.endswith(filename))





def _user_refs_workbench(user_id: str, url: str) -> bool:
    return False

    from backend import workbench_assets, workbench_store



    uid = (user_id or "").strip()

    if not uid:

        return False

    raw = (url or "").strip()

    view = workbench_store.load_for_user(uid)

    for wb in view.get("workbenches") or []:

        if not isinstance(wb, dict):

            continue

        for item in workbench_assets.collect_workbench_refs(wb):

            if _refs_match(raw, item):

                return True

    return False





def _user_refs_upload(user_id: str, ref: str) -> bool:

    from backend import canvas_store



    view = canvas_store.load_for_user(user_id)

    for board in view.get("boards") or []:

        if not isinstance(board, dict):

            continue

        refs = collect_board_refs(board)

        if ref in refs:

            return True

    return False





def _user_task_owns_upload(user_id: str, url: str) -> bool:

    """Allow tools on media this user already generated (task row), even before workbench save."""

    from backend.json_io import load_json

    from backend.user_scope import tasks_file



    uid = (user_id or "").strip()

    raw = (url or "").strip()

    if not uid or not raw:

        return False

    path = tasks_file(uid)

    if not path.is_file():

        return False

    data = load_json(path, default=[])

    tasks = data if isinstance(data, list) else (data.get("tasks") if isinstance(data, dict) else None)

    if not isinstance(tasks, list):

        return False

    media_keys = ("video_url", "image_url", "thumb_url", "cover_url", "audio_url", "url")

    for task in tasks:

        if not isinstance(task, dict):

            continue

        for key in media_keys:

            if _refs_match(raw, str(task.get(key) or "")):

                return True

        result = task.get("result")

        if isinstance(result, dict):

            for key in media_keys:

                if _refs_match(raw, str(result.get(key) or "")):

                    return True

    return False





def _flat_upload_allowed(user_id: str, filename: str) -> bool:

    from backend.media.upscale_queue import find_job_by_result_url



    url = f"/uploads/{filename}"

    job = find_job_by_result_url(url)

    if job and str(job.get("user_id") or "") == str(user_id):

        return True

    ref = upload_ref_from_url(url) or filename

    if _user_refs_upload(user_id, ref):

        return True

    if _user_refs_workbench(user_id, url):

        return True

    if _user_task_owns_upload(user_id, url):

        return True

    found = find_asset_by_filename(filename)

    if found:

        ow = path_owner_workbench(found)

        if ow:

            return user_has_workbench_access(user_id, ow[0], ow[1])

        ob = path_owner_board(found)

        if ob:

            return user_has_board_access(user_id, ob[0], ob[1])

    return False





def _workbench_context_allows(user_id: str, url: str, workbench_id: str) -> bool:
    return False

    from backend import workbench_assets, workbench_store



    uid = (user_id or "").strip()

    wid = (workbench_id or "").strip()

    raw = (url or "").strip()

    if not uid or not wid or not raw:

        return False



    wb = workbench_store.get_workbench(uid, wid)

    owner_id = uid

    if not wb:

        for share in workbench_store.shares_for_user(uid):

            if str(share.get("workbench_id") or "") != wid:

                continue

            owner_id = str(share.get("owner_user_id") or "")

            if owner_id:

                wb = workbench_store.get_workbench(owner_id, wid)

            break

    if not wb or not user_has_workbench_access(uid, owner_id, wid):

        return False



    for item in workbench_assets.collect_workbench_refs(wb):

        if _refs_match(raw, item):

            return True



    resolved = resolve_upload_path(raw)

    if not resolved or not resolved.is_file():

        return False



    ow = path_owner_workbench(resolved)

    if ow and ow[1] == wid and user_has_workbench_access(uid, ow[0], ow[1]):

        return True



    # Legacy flat pool: video plays via public GET, but tools previously 403'd when the

    # workbench JSON hadn't persisted the ref yet (debounce / stale save).

    try:

        if resolved.parent.resolve() == UPLOADS_DIR.resolve():

            return True

    except OSError:

        pass

    return False





def _board_context_allows(user_id: str, url: str, board_id: str) -> bool:

    from backend import canvas_store



    uid = (user_id or "").strip()

    bid = (board_id or "").strip()

    raw = (url or "").strip()

    if not uid or not bid or not raw:

        return False



    view = canvas_store.load_for_user(uid)

    board = None

    owner_id = uid

    for row in view.get("boards") or []:

        if isinstance(row, dict) and str(row.get("id") or "") == bid:

            board = row

            break

    if not board:

        return False

    if board.get("isOwned") is False or board.get("shareOwnerId"):

        owner_id = str(board.get("shareOwnerId") or "")

        if not owner_id or not user_has_board_access(uid, owner_id, bid):

            return False

    elif not user_has_board_access(uid, owner_id, bid):

        return False



    refs = collect_board_refs(board)

    ref = upload_ref_from_url(raw) or raw

    if ref in refs:

        return True

    filename = raw.rsplit("/", 1)[-1].split("?", 1)[0]

    if filename and any(str(item).endswith(filename) for item in refs):

        return True



    resolved = resolve_upload_path(raw)

    if not resolved or not resolved.is_file():

        return False

    ob = path_owner_board(resolved)

    if ob and ob[1] == bid and user_has_board_access(uid, ob[0], ob[1]):

        return True

    try:

        if resolved.parent.resolve() == UPLOADS_DIR.resolve():

            return True

    except OSError:

        pass

    return False





def user_can_access_upload(

    user_id: str,

    url: str,

    *,

    path: Path | None = None,

    workbench_id: str | None = None,

    board_id: str | None = None,

) -> bool:

    uid = (user_id or "").strip()

    if not uid:

        return False

    raw = (url or "").strip()

    if not raw:

        return False



    m = _WORKBENCH_UPLOAD_RE.search(raw)

    if m:

        slug, workbench_id_from_url, _filename = m.group(1), m.group(2), m.group(3)

        owner_id = slug_to_user_id(slug)

        if not owner_id:

            return False

        return user_has_workbench_access(uid, owner_id, workbench_id_from_url)



    m = _NESTED_UPLOAD_RE.search(raw)

    if m:

        slug, board_id_from_url, _filename = m.group(1), m.group(2), m.group(3)

        if board_id_from_url == "workbenches":

            return False

        owner_id = slug_to_user_id(slug)

        if not owner_id:

            return False

        return user_has_board_access(uid, owner_id, board_id_from_url)



    resolved = path or resolve_upload_path(raw)

    if resolved and resolved.is_file():

        ow = path_owner_workbench(resolved)

        if ow:

            return user_has_workbench_access(uid, ow[0], ow[1])

        ob = path_owner_board(resolved)

        if ob:

            return user_has_board_access(uid, ob[0], ob[1])

        try:

            if resolved.parent.resolve() == UPLOADS_DIR.resolve():

                if _flat_upload_allowed(uid, resolved.name):

                    return True

        except OSError:

            pass



    m = _FLAT_UPLOAD_RE.search(raw)

    if m:

        name = m.group(1)

        if name and "/" not in name and "\\" not in name:

            if _flat_upload_allowed(uid, name):

                return True



    wid = (workbench_id or "").strip()

    if wid and _workbench_context_allows(uid, raw, wid):

        return True

    bid = (board_id or "").strip()

    if bid and _board_context_allows(uid, raw, bid):

        return True

    return False





def assert_media_access(

    user_id: str,

    url: str,

    *,

    workbench_id: str | None = None,

    board_id: str | None = None,

) -> None:

    raw = (url or "").strip()

    if not raw:

        return

    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("data:"):

        return

    if not raw.startswith("/uploads/") and not raw.startswith("uploads/"):

        return

    if not user_can_access_upload(

        user_id,

        raw,

        workbench_id=workbench_id,

        board_id=board_id,

    ):

        logger.warning("media access denied user=%s url=%s", user_id, raw[:120])

        raise HTTPException(403, "无权访问该媒体文件")





def assert_media_refs(

    user_id: str,

    references: list[dict[str, Any]] | None = None,

    reference_images: list[str] | None = None,

    *,

    workbench_id: str | None = None,

    board_id: str | None = None,

) -> None:

    for item in references or []:

        if not isinstance(item, dict):

            continue

        url = str(item.get("url") or item.get("src") or "").strip()

        assert_media_access(user_id, url, workbench_id=workbench_id, board_id=board_id)

    for url in reference_images or []:

        assert_media_access(

            user_id,

            str(url or ""),

            workbench_id=workbench_id,

            board_id=board_id,

        )





def assert_upload_serve_allowed(

    request: Request,

    url: str,

    path: Path,

    media_token: str | None = None,

) -> None:

    """Public read for board media — no login or ?mt= required (MyApi / Youkou fetch refs)."""

    return



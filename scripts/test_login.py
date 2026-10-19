import os

import httpx

base = os.environ.get("CANVAS_TEST_URL", "http://127.0.0.1:10027").rstrip("/")

with httpx.Client(timeout=20.0) as c:
    r = c.post(f"{base}/api/auth/login", json={"username": os.environ["CANVAS_TEST_USERNAME"], "password": os.environ["CANVAS_TEST_PASSWORD"]})
    print("canvas_login", r.status_code, r.json().get("user", {}).get("username"))
    tok = r.json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    r2 = c.get(f"{base}/api/accounts", headers=h)
    print("accounts", r2.status_code, len(r2.json().get("accounts", [])))
    r3 = c.get(f"{base}/api/canvas", headers=h)
    print("canvas_boards", r3.status_code, len(r3.json().get("boards", [])))
    r4 = c.get(f"{base}/api/auth/me", headers=h)
    print("me", r4.status_code, r4.json().get("user", {}).get("username"))

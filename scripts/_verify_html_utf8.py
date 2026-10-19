# -*- coding: utf-8 -*-
from pathlib import Path

front = Path(__file__).resolve().parent.parent / "frontend"
for name in ["index.html", "canvas.html"]:
    raw = (front / name).read_bytes()
    try:
        text = raw.decode("utf-8")
        ok = True
    except UnicodeDecodeError as e:
        text = raw.decode("utf-8", errors="replace")
        ok = False
        print(name, "STRICT_FAIL", e)
    bad = text.count("\ufffd")
    print(
        f"{name}: utf8_ok={ok} bad={bad} size={len(raw)} "
        f"edit资料={'编辑资料' in text} adopt={'采纳到节点' in text} "
        f"amount={'充值金额' in text} dur={'总时长' in text}"
    )

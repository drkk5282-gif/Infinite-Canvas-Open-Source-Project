# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(r"e:\Desktop\AI\doubao-canvas\frontend\canvas.html.corrupt-b209-encoding-bak")
raw = p.read_bytes()
print("BOM", raw[:3])
print("ufffd count in utf-8 decode", raw.decode("utf-8", errors="replace").count("\ufffd"))
# try decode as utf-8 strict
try:
    t = raw.decode("utf-8")
    print("strict utf-8 OK, len", len(t))
except UnicodeDecodeError as e:
    print("strict fail", e)

for s in ["编辑资料", "重命名画布", "采纳到节点", "theme-adapt.css", "modal-edit-profile"]:
    print(repr(s), s.encode("utf-8") in raw)

# sample around edit-profile button
idx = raw.find(b"btn-workspace-edit-profile")
print("snippet", raw[idx:idx+120])

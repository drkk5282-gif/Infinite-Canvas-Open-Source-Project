# -*- coding: utf-8 -*-
from pathlib import Path
import re

FRONT = Path(__file__).resolve().parent.parent / "frontend"
TAG = "b217-random-dark"
for name in ("index.html", "canvas.html"):
    path = FRONT / name
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(theme\.js\?v=)[^\"\s>]+", rf"\1{TAG}", text)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(name, "ok")

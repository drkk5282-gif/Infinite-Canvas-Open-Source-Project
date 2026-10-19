import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.user_scope import set_current_user_id
from backend import account_manager

set_current_user_id("984ceb9a6c18")
t0 = time.time()
print("reconcile...", flush=True)
account_manager.reconcile_gpt_online_status()
print("reconcile", time.time() - t0, flush=True)
t1 = time.time()
rows = account_manager.list_accounts()
print("list", len(rows), time.time() - t1, flush=True)

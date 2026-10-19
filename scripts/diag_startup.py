import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("start", flush=True)
from backend import app_users

print("users", flush=True)
app_users.ensure_bootstrap()
print("cleanup", flush=True)
from backend.media.uploads_cleanup import cleanup_uploads

cleanup_uploads()
print("accounts", flush=True)
from backend import account_manager

print("count", len(account_manager.list_accounts()), flush=True)
print("done", flush=True)

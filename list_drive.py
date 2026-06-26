import sys
import io
import os
from pathlib import Path
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

# ── .env 로드 ────────────────────────────────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

SERVER_URL    = os.environ["SERVER_URL"]
CLIENT_NAME   = os.environ["CLIENT_NAME"]
ACCESS_TOKEN  = os.environ["ACCESS_TOKEN"]
REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]
# ────────────────────────────────────────────────────────────────────────────

from siemens.scx import SCX, Credentials
from dotenv import set_key


def save_tokens(scx):
    session = scx._session
    set_key(str(ENV_FILE), "ACCESS_TOKEN", session.bearer_jwt.replace("Bearer ", ""))
    set_key(str(ENV_FILE), "REFRESH_TOKEN", session.auth_headers.get("x-refresh-token", ""))


scx = SCX.Get_from_existing_token(
    server_url=SERVER_URL,
    credentials=Credentials(access_token=ACCESS_TOKEN, refresh_token=REFRESH_TOKEN),
    client_name=CLIENT_NAME,
)
save_tokens(scx)

print("\n--- Altairone Drive Home ---", flush=True)
contents, _ = scx.drive.ls("~")
for item in contents:
    print(f"  {item.name:40s}  {item.type}", flush=True)
print(f"\nTotal: {len(contents)} items", flush=True)

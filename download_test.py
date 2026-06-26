# -*- coding: utf-8 -*-
"""
단일 파일 다운로드 테스트
- adcopy.exe(504 오류) 대신 FsGetDownloadUrlAction → presigned URL → requests 방식 사용
- DRIVE_PATH 에 Drive 경로를 지정하고 실행
"""
import sys, io, os, requests
from pathlib import Path
from dotenv import load_dotenv, set_key

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

# ── .env 로드 (스크립트와 같은 폴더) ──────────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

SERVER_URL    = os.environ["SERVER_URL"]
CLIENT_NAME   = os.environ["CLIENT_NAME"]
ACCESS_TOKEN  = os.environ["ACCESS_TOKEN"]
REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]
# ─────────────────────────────────────────────────────────────────────────────

# ★ 다운로드할 파일 경로 (Drive 상의 경로)
DRIVE_PATH = "~/virtualwindtunnel.zip"

# ★ 저장할 로컬 폴더
LOCAL_DIR = Path(__file__).parent   # test/ 폴더에 저장


def save_tokens(scx):
    session = scx._session
    set_key(str(ENV_FILE), "ACCESS_TOKEN", session.bearer_jwt.replace("Bearer ", ""))
    set_key(str(ENV_FILE), "REFRESH_TOKEN", session.auth_headers.get("x-refresh-token", ""))


def get_file_by_path(drive, drive_path: str):
    """Drive 경로로 파일 객체를 찾아 반환."""
    from siemens.scx.drive.models.file import File

    path = drive_path.lstrip("~").lstrip("/")
    parts = [p for p in path.split("/") if p]
    parent = "~/" + "/".join(parts[:-1]) if len(parts) > 1 else "~"
    name   = parts[-1]

    contents, _ = drive.ls(parent)
    for item in contents:
        if item.name == name and isinstance(item, File):
            return item
    return None


def download_file(session, file_obj, dest_dir: Path):
    from siemens.scx.core.service.action_executor import execute_action

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file_obj.name

    if dest.exists():
        print(f"이미 존재함 (건너뜀): {dest}", flush=True)
        return

    print(f"presigned URL 발급 중...", flush=True)
    res = execute_action(
        session=session,
        action_inputs={"fileId": file_obj.id},
        action_content_type="altair.system.FsGetDownloadUrlAction",
    )
    url = res.get("url") if res else None
    if not url:
        print("URL 발급 실패", flush=True)
        return

    size_mb = (file_obj.size or 0) / 1024 / 1024
    print(f"다운로드 시작: {file_obj.name}  ({size_mb:.1f} MB)", flush=True)

    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            downloaded = 0
            for chunk in r.iter_content(chunk_size=512 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                print(f"\r  {downloaded / 1024 / 1024:.1f} MB", end="", flush=True)
    print(f"\n완료: {dest}", flush=True)


# ── 메인 ─────────────────────────────────────────────────────────────────────
print(f"Altairone 연결 중...", flush=True)
from siemens.scx import SCX, Credentials

scx = SCX.Get_from_existing_token(
    server_url=SERVER_URL,
    credentials=Credentials(access_token=ACCESS_TOKEN, refresh_token=REFRESH_TOKEN),
    client_name=CLIENT_NAME,
)
save_tokens(scx)
print("연결 성공!\n", flush=True)

print(f"파일 조회: {DRIVE_PATH}", flush=True)
file_obj = get_file_by_path(scx.drive, DRIVE_PATH)
if not file_obj:
    print(f"파일을 찾을 수 없습니다: {DRIVE_PATH}", flush=True)
    sys.exit(1)

print(f"파일 ID  : {file_obj.id}", flush=True)
download_file(scx._session, file_obj, LOCAL_DIR)

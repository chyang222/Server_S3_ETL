import sys
import io
import os
import requests
from pathlib import Path
from dotenv import load_dotenv, set_key

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

# ── .env 로드 ────────────────────────────────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

SERVER_URL    = os.environ["SERVER_URL"]
CLIENT_NAME   = os.environ["CLIENT_NAME"]
ACCESS_TOKEN  = os.environ["ACCESS_TOKEN"]
REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]

# DEST_DIR: 절대경로면 그대로, 상대경로면 스크립트 위치 기준으로 해석
# Linux 예: /data/altairone/drive_data
# 상대 예: ./drive_data  →  스크립트 폴더/drive_data
_dest_raw = os.environ.get("DEST_DIR", "./drive_data")
DEST_DIR = (
    Path(_dest_raw) if Path(_dest_raw).is_absolute()
    else (Path(__file__).parent / _dest_raw)
).resolve()

SKIP_NAMES = {"SharedWithMe", "trash"}
# ────────────────────────────────────────────────────────────────────────────


def save_tokens(scx):
    """토큰 갱신 후 .env 파일에 새 값 덮어씀."""
    session = scx._session
    new_access  = session.bearer_jwt.replace("Bearer ", "")
    new_refresh = session.auth_headers.get("x-refresh-token", "")
    set_key(str(ENV_FILE), "ACCESS_TOKEN", new_access)
    set_key(str(ENV_FILE), "REFRESH_TOKEN", new_refresh)
    print("  [.env 토큰 갱신 완료]", flush=True)


def connect():
    from siemens.scx import SCX, Credentials
    scx = SCX.Get_from_existing_token(
        server_url=SERVER_URL,
        credentials=Credentials(access_token=ACCESS_TOKEN, refresh_token=REFRESH_TOKEN),
        client_name=CLIENT_NAME,
    )
    save_tokens(scx)
    return scx


def get_download_url(session, file_id: str) -> str:
    from siemens.scx.core.service.action_executor import execute_action
    try:
        res = execute_action(
            session=session,
            action_inputs={"fileId": file_id},
            action_content_type="altair.system.FsGetDownloadUrlAction",
        )
        return res.get("url") if res else None
    except Exception as e:
        print(f"    WARN (URL 발급 실패, 재시도 필요): {e}", flush=True)
        return None


def download_file(session, file_obj, local_dir: Path, stats: dict):
    local_dir.mkdir(parents=True, exist_ok=True)
    dest = local_dir / file_obj.name

    if dest.exists():
        print(f"    SKIP (exists): {file_obj.name}", flush=True)
        stats["skip"] += 1
        return

    url = get_download_url(session, file_obj.id)
    if not url:
        print(f"    FAIL (no URL): {file_obj.name}", flush=True)
        stats["fail"] += 1
        return

    size_kb = (file_obj.size or 0) / 1024
    print(f"    ↓ {file_obj.name}  ({size_kb:.1f} KB)", flush=True)

    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    f.write(chunk)
        stats["ok"] += 1
    except Exception as e:
        print(f"    FAIL: {e}", flush=True)
        if dest.exists():
            dest.unlink()
        stats["fail"] += 1


def download_recursive(drive, session, drive_path: str, local_dir: Path, stats: dict, depth: int = 0):
    from siemens.scx.drive.models.file import File
    from siemens.scx.drive.models.directory import Directory

    indent = "  " * depth
    try:
        contents, _ = drive.ls(drive_path)
    except Exception as e:
        print(f"{indent}  ls 실패 ({drive_path}): {e}", flush=True)
        return

    for item in contents:
        if item.name in SKIP_NAMES:
            print(f"{indent}  SKIP: {item.name}", flush=True)
            continue

        if isinstance(item, File):
            download_file(session, item, local_dir, stats)
        elif isinstance(item, Directory):
            sub_drive = f"{drive_path}/{item.name}"
            sub_local = local_dir / item.name
            print(f"{indent}  [DIR] {item.name}/", flush=True)
            download_recursive(drive, session, sub_drive, sub_local, stats, depth + 1)


def main():
    print("=" * 60, flush=True)
    print("Altairone Drive → 로컬 다운로드 (폴더 구조 유지)", flush=True)
    print(f"저장 위치: {DEST_DIR}", flush=True)
    print("=" * 60 + "\n", flush=True)

    scx = connect()
    print("연결 성공!\n", flush=True)

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    stats = {"ok": 0, "skip": 0, "fail": 0}

    download_recursive(scx.drive, scx._session, "~", DEST_DIR, stats)

    print("\n" + "=" * 60, flush=True)
    print(f"완료:  다운로드 {stats['ok']}개  /  건너뜀 {stats['skip']}개  /  실패 {stats['fail']}개", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()

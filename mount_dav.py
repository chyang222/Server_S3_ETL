"""
Altairone Drive → WebDAV 서버

mount_keep.sh 에서 자동 실행되거나 직접 실행 가능.
실행 후 mount_keep.sh 가 DEST_DIR 에 rclone/davfs2 로 마운트함.
"""
import sys
import io
import os
import time
import threading
import requests
from pathlib import Path
from dotenv import load_dotenv, set_key

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

SERVER_URL    = os.environ["SERVER_URL"]
CLIENT_NAME   = os.environ["CLIENT_NAME"]
ACCESS_TOKEN  = os.environ["ACCESS_TOKEN"]
REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]

PORT       = int(os.environ.get("DAV_PORT", "8888"))
CACHE_TTL  = 30   # 디렉토리 목록 캐시 유효시간(초)
PID_FILE   = Path(__file__).parent / ".dav_server.pid"


# ── Altairone 연결 ────────────────────────────────────────────────────────────
_scx_lock = threading.Lock()
_scx_ref  = [None]   # 뮤터블 컨테이너 (스레드 간 공유)


def _save_tokens(session):
    set_key(str(ENV_FILE), "ACCESS_TOKEN", session.bearer_jwt.replace("Bearer ", ""))
    set_key(str(ENV_FILE), "REFRESH_TOKEN", session.auth_headers.get("x-refresh-token", ""))


def _make_scx():
    from siemens.scx import SCX, Credentials
    load_dotenv(ENV_FILE, override=True)
    scx = SCX.Get_from_existing_token(
        server_url=os.environ["SERVER_URL"],
        credentials=Credentials(
            access_token=os.environ["ACCESS_TOKEN"],
            refresh_token=os.environ["REFRESH_TOKEN"],
        ),
        client_name=os.environ["CLIENT_NAME"],
    )
    _save_tokens(scx._session)
    return scx


def get_scx():
    with _scx_lock:
        return _scx_ref[0]


def token_refresh_loop(interval_sec=20 * 3600):
    """Access Token 만료 전 자동 갱신 (기본 20시간 주기)."""
    while True:
        time.sleep(interval_sec)
        print(f"  [토큰 갱신 중...]", flush=True)
        try:
            new_scx = _make_scx()
            with _scx_lock:
                _scx_ref[0] = new_scx
            print(f"  [토큰 갱신 완료]", flush=True)
        except Exception as e:
            print(f"  [토큰 갱신 실패] {e}", flush=True)


def get_download_url(session, file_id: str):
    from siemens.scx.core.service.action_executor import execute_action
    try:
        res = execute_action(
            session=session,
            action_inputs={"fileId": file_id},
            action_content_type="altair.system.FsGetDownloadUrlAction",
        )
        return res.get("url") if res else None
    except Exception as e:
        print(f"  [URL 발급 실패] {e}", flush=True)
        return None


# ── 디렉토리 목록 캐시 ────────────────────────────────────────────────────────
_dir_cache: dict = {}
_cache_lock = threading.Lock()


def ls_cached(drive, drive_path: str):
    now = time.time()
    with _cache_lock:
        if drive_path in _dir_cache:
            ts, contents = _dir_cache[drive_path]
            if now - ts < CACHE_TTL:
                return contents
    try:
        contents, _ = drive.ls(drive_path)
    except Exception as e:
        print(f"  [ls 실패] {drive_path}: {e}", flush=True)
        contents = []
    with _cache_lock:
        _dir_cache[drive_path] = (now, contents)
    return contents


# ── WebDAV 리소스 구현 ────────────────────────────────────────────────────────
from wsgidav.dav_provider import DAVProvider, DAVCollection, DAVNonCollection


class AltaironeFile(DAVNonCollection):
    def __init__(self, path, environ, file_obj, session):
        super().__init__(path, environ)
        self._file_obj = file_obj
        self._session  = session

    def get_content_length(self):  return self._file_obj.size or 0
    def get_content_type(self):    return "application/octet-stream"
    def get_etag(self):            return f'"{self._file_obj.id}"'
    def support_etag(self):        return True
    def get_last_modified(self):   return None
    def support_modified(self):    return False
    def support_ranges(self):      return False

    def get_content(self):
        url = get_download_url(self._session, self._file_obj.id)
        if not url:
            return io.BytesIO(b"")
        r = requests.get(url, stream=True, timeout=300)
        r.raise_for_status()
        return r.raw


class AltaironeDir(DAVCollection):
    def __init__(self, path, environ, drive, drive_path, session):
        super().__init__(path, environ)
        self._drive      = drive
        self._drive_path = drive_path
        self._session    = session

    def get_member_names(self):
        contents = ls_cached(self._drive, self._drive_path)
        return [item.name for item in contents if item.name not in ("SharedWithMe", "trash")]

    def get_member(self, name):
        from siemens.scx.drive.models.file import File
        from siemens.scx.drive.models.directory import Directory
        for item in ls_cached(self._drive, self._drive_path):
            if item.name == name:
                child_path = self.path.rstrip("/") + "/" + name
                if isinstance(item, File):
                    return AltaironeFile(child_path, self.environ, item, self._session)
                elif isinstance(item, Directory):
                    return AltaironeDir(child_path, self.environ, self._drive,
                                        self._drive_path + "/" + name, self._session)
        return None

    def get_last_modified(self):  return None
    def support_modified(self):   return False


class AltaironeProvider(DAVProvider):
    def get_resource_inst(self, path, environ):
        from siemens.scx.drive.models.file import File
        from siemens.scx.drive.models.directory import Directory

        scx = get_scx()
        if scx is None:
            return None

        if path in ("", "/"):
            return AltaironeDir("/", environ, scx.drive, "~", scx._session)

        parts = [p for p in path.strip("/").split("/") if p]
        parent_drive = "~/" + "/".join(parts[:-1]) if len(parts) > 1 else "~"
        target_name  = parts[-1]

        for item in ls_cached(scx.drive, parent_drive):
            if item.name == target_name:
                if isinstance(item, File):
                    return AltaironeFile(path, environ, item, scx._session)
                elif isinstance(item, Directory):
                    return AltaironeDir(path, environ, scx.drive,
                                        parent_drive + "/" + target_name, scx._session)
        return None


# ── 서버 시작 ─────────────────────────────────────────────────────────────────
def main():
    # PID 파일 저장
    PID_FILE.write_text(str(os.getpid()))

    print("=" * 55, flush=True)
    print(f"Altairone Drive → WebDAV 서버  (포트 {PORT})", flush=True)
    print("=" * 55, flush=True)

    print("\nAltairone 연결 중...", flush=True)
    scx = _make_scx()
    with _scx_lock:
        _scx_ref[0] = scx
    print("  [연결 성공]", flush=True)

    # 토큰 자동 갱신 스레드 (20시간마다)
    t = threading.Thread(target=token_refresh_loop, daemon=True)
    t.start()
    print("  [토큰 자동 갱신 스레드 시작 (20h 주기)]", flush=True)

    from wsgidav.wsgidav_app import WsgiDAVApp
    from cheroot import wsgi

    config = {
        "host": "0.0.0.0",
        "port": PORT,
        "provider_mapping": {"/": AltaironeProvider()},
        "simple_dc": {"user_mapping": {"*": True}},
        "verbose": 0,
        "logging": {"enable_loggers": []},
    }

    app    = WsgiDAVApp(config)
    server = wsgi.Server(("0.0.0.0", PORT), app)

    print(f"\n서버 준비: http://localhost:{PORT}/", flush=True)
    print("Ctrl+C 로 종료\n", flush=True)

    try:
        server.start()
    except KeyboardInterrupt:
        print("\n서버 종료", flush=True)
        server.stop()
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

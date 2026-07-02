#!/usr/bin/env bash
# =============================================================================
# Altairone Drive 마운트 유지 스크립트 (Linux)
#
# 동작 흐름:
#   1. mount_dav.py (WebDAV 서버) 기동
#   2. DEST_DIR 에 rclone 또는 davfs2 로 마운트
#   3. 30초마다 서버/마운트 상태 감시 → 자동 재시작
#   4. 20시간마다 서버 재시작으로 토큰 갱신
#   5. Ctrl+C / kill 수신 시 깔끔하게 마운트 해제 후 종료
#
# 사전 요구사항:
#   pip install wsgidav cheroot python-dotenv requests
#   sudo apt install rclone  (또는 davfs2)
#
# 실행:
#   chmod +x mount_keep.sh
#   ./mount_keep.sh              # 포그라운드
#   nohup ./mount_keep.sh &      # 백그라운드 (nohup.out 에 로그)
#   screen -S altairone ./mount_keep.sh   # screen 세션
# =============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
PID_FILE="$SCRIPT_DIR/.dav_server.pid"
SERVER_LOG="$SCRIPT_DIR/mount_dav.log"
PORT="${DAV_PORT:-8888}"
CHECK_INTERVAL=30     # 상태 확인 주기 (초)
TOKEN_REFRESH=72000   # 20시간 (초) - 이 주기로 서버 재시작하여 토큰 갱신

# .env 에서 DEST_DIR 읽기 (python-dotenv 사용)
MOUNT_POINT="$(python3 - <<'PYEOF'
import os, sys
from pathlib import Path
from dotenv import dotenv_values

script_dir = sys.argv[1] if len(sys.argv) > 1 else "."
env_file = Path(script_dir) / ".env"
v = dotenv_values(env_file)
dest = v.get("DEST_DIR", "./drive_data")
p = Path(dest)
if not p.is_absolute():
    p = Path(script_dir) / dest
print(p.resolve())
PYEOF
 "$SCRIPT_DIR")"

# =============================================================================
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# 서버 시작
start_server() {
    log "WebDAV 서버 시작 (port $PORT)..."
    python3 "$SCRIPT_DIR/mount_dav.py" \
        >> "$SERVER_LOG" 2>&1 &
    echo $! > "$PID_FILE"
    log "서버 PID: $(cat "$PID_FILE")"

    # 준비 대기 (최대 20초)
    for i in $(seq 1 20); do
        if curl -sf "http://localhost:$PORT/" > /dev/null 2>&1; then
            log "서버 준비 완료"
            return 0
        fi
        sleep 1
    done
    log "경고: 서버 20초 내 응답 없음 (계속 진행)"
}

# 서버 종료
stop_server() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log "서버 종료 (PID: $pid)"
            kill "$pid" 2>/dev/null || true
            sleep 2
        fi
        rm -f "$PID_FILE"
    fi
}

# 서버 생존 확인
is_server_alive() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

# 마운트
do_mount() {
    mkdir -p "$MOUNT_POINT"

    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        log "이미 마운트됨: $MOUNT_POINT"
        return 0
    fi

    # rclone 우선
    if command -v rclone &>/dev/null; then
        log "rclone 마운트 시도: $MOUNT_POINT"
        rclone mount :webdav: "$MOUNT_POINT" \
            --webdav-url "http://localhost:$PORT/" \
            --daemon \
            --vfs-cache-mode reads \
            --dir-cache-time "${CHECK_INTERVAL}s" \
            --poll-interval "${CHECK_INTERVAL}s" \
            --log-level WARNING \
            --log-file "$SCRIPT_DIR/rclone.log" 2>/dev/null
        sleep 2
        if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
            log "rclone 마운트 완료: $MOUNT_POINT"
            return 0
        fi
        log "rclone 마운트 실패"
    fi

    # davfs2 fallback
    if command -v mount.davfs &>/dev/null; then
        log "davfs2 마운트 시도: $MOUNT_POINT"
        echo "http://localhost:$PORT/ user password" | sudo tee -a /etc/davfs2/secrets > /dev/null 2>&1 || true
        sudo mount.davfs "http://localhost:$PORT/" "$MOUNT_POINT" 2>/dev/null
        if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
            log "davfs2 마운트 완료: $MOUNT_POINT"
            return 0
        fi
        log "davfs2 마운트 실패"
    fi

    log "오류: rclone 또는 davfs2 를 설치해 주세요"
    log "  sudo apt install rclone  또는  https://rclone.org/install/"
    log "  sudo apt install davfs2"
    return 1
}

# 마운트 해제
do_umount() {
    if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        log "마운트 해제: $MOUNT_POINT"
        fusermount -u "$MOUNT_POINT" 2>/dev/null \
            || fusermount3 -u "$MOUNT_POINT" 2>/dev/null \
            || sudo umount "$MOUNT_POINT" 2>/dev/null \
            || true
        sleep 1
    fi
}

# 종료 처리 (Ctrl+C / kill)
cleanup() {
    log "종료 신호 수신..."
    do_umount
    stop_server
    log "===== 종료 완료 ====="
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# =============================================================================
# 메인
# =============================================================================
log "===== Altairone Drive 마운트 시작 ====="
log "마운트 위치 : $MOUNT_POINT"
log "WebDAV 포트 : $PORT"
log "토큰 갱신   : ${TOKEN_REFRESH}초 (20h) 마다 서버 재시작"

# 기존 잔여 정리
do_umount
stop_server

# 최초 기동
start_server
do_mount

SERVER_START_TIME=$(date +%s)
log "감시 루프 시작 (${CHECK_INTERVAL}초 간격)"

while true; do
    sleep "$CHECK_INTERVAL"

    now=$(date +%s)
    uptime_sec=$((now - SERVER_START_TIME))

    # 20시간 경과 → 서버 재시작으로 토큰 갱신
    if [ "$uptime_sec" -ge "$TOKEN_REFRESH" ]; then
        log "20시간 경과: 토큰 갱신을 위해 서버 재시작..."
        do_umount
        stop_server
        sleep 2
        start_server
        do_mount
        SERVER_START_TIME=$(date +%s)
        continue
    fi

    # 서버 다운 → 재시작
    if ! is_server_alive; then
        log "서버 다운 감지, 재시작..."
        stop_server
        start_server
        sleep 2
        SERVER_START_TIME=$(date +%s)
    fi

    # 마운트 해제 → 재마운트
    if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        log "마운트 해제 감지, 재마운트..."
        do_mount
    fi
done

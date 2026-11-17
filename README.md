# Server S3 ETL — 시뮬레이션 데이터 S3 연동 · 마운트 도구

해석(시뮬레이션) 데이터를 S3 기반 클라우드 드라이브로 이관한 뒤, 벤더 Python SDK를 통해
**서버에 S3 스토리지를 마운트·동기화**하여 모니터링하는 경량 SPDM(Simulation Process & Data Management) 도구의 연동 모듈입니다.

- 드라이브 → 로컬 서버 **증분 동기화 ETL** (폴더 구조 유지)
- 드라이브를 로컬 폴더처럼 쓰는 **WebDAV 서버 + rclone 마운트**
- **OAuth2 토큰 자동 순환**으로 수동 개입 없는 장기 운영
- 30초 주기 **감시 데몬**으로 서버·마운트 장애 자동 복구

---

## 아키텍처

```
시뮬레이션 데이터 ──(이관)──▶ S3 기반 클라우드 드라이브
                                   │
                    벤더 Python SDK + OAuth2 (토큰 자동 순환)
                                   │
          ┌────────────────────────┼─────────────────────────┐
          ▼                        ▼                         ▼
 download_all.py            mount_dav.py               mount_keep.sh
 Presigned URL 다운로드      WebDAV 서버 (wsgidav)       감시 데몬
 (일 단위 증분 동기화)        └─▶ rclone / davfs2 마운트   (30초 감시, 20시간 재시작)
```

## 파일 구성

```
.
├── .env.example      # 연결 설정 템플릿 (.env 는 gitignore)
├── list_drive.py     # 드라이브 파일 목록 조회
├── download_all.py   # 드라이브 → 로컬 전체 동기화 (폴더 구조 유지, 기존 파일 건너뜀)
├── download_test.py  # 단일 파일 다운로드 테스트
├── mount_dav.py      # 드라이브를 WebDAV로 노출하는 서버
└── mount_keep.sh     # 마운트 유지 데몬 (Linux)
```

---

## 핵심 설계

### 1. OAuth2 토큰 자동 순환

플랫폼은 OAuth2/OIDC 서버를 사용하며 비밀번호 방식(Password Grant)을 지원하지 않습니다.
최초 1회만 브라우저 세션에서 토큰을 받아 두면, 이후에는 실행할 때마다 자동으로 갱신됩니다.

```
[최초 1회] 브라우저에서 토큰 확보 → .env 저장
    ↓
[실행 시마다 자동]
  1. .env 에서 토큰 로드
  2. Refresh Token → 새 Access Token + 새 Refresh Token 발급
  3. 갱신된 토큰을 .env 에 덮어씀
  4. API 작업 수행
```

| 토큰 | 역할 | 수명 |
|------|------|------|
| Access Token | API 호출 인증 | 약 24시간 |
| Refresh Token | Access Token 재발급 | 수일~수주 |

주기 실행이 유지되는 한 Refresh Token이 계속 순환되므로 무기한 동작합니다.

### 2. SDK 다운로드 타임아웃 우회

SDK 기본 복사 도구는 대용량 파일에서 서버 측 504 타임아웃이 발생했습니다.
대신 다운로드 URL 발급 API로 **Presigned URL**을 직접 받아 `requests`로 스트리밍(512KB 청크) 다운로드합니다.

```
드라이브 파일 → 다운로드 URL 발급 API → Presigned URL (S3) → requests 스트리밍 다운로드
```

- 서버 측 일시적 500 오류는 경고만 남기고 다음 파일로 진행
- 이미 받은 파일은 건너뛰므로 재실행해도 안전

### 3. WebDAV 마운트

SDK 위에 WebDAV 프로바이더(wsgidav)를 직접 구현하고, rclone(우선) 또는 davfs2로 로컬에 마운트합니다.

```
mount_dav.py   → localhost:8888 WebDAV 서버
     ↕ HTTP WebDAV
rclone/davfs2  → DEST_DIR (로컬 마운트 포인트)
```

- 디렉터리 목록 30초 캐시로 API 호출 절감
- 백그라운드 스레드가 20시간마다 토큰 갱신

### 4. 마운트 지속성

| 상황 | 대응 | 자동화 |
|------|------|--------|
| Access Token 만료 (~24h) | 20시간마다 서버 재시작 | ✅ |
| WebDAV 서버 크래시 | 30초 감시 후 재시작 | ✅ |
| 마운트 해제 | 30초 감시 후 재마운트 | ✅ |
| 서버 재부팅 | systemd 서비스로 자동 시작 | ✅ (등록 필요) |
| Refresh Token 만료 (장기 중단 시) | 토큰 재발급 | ⚠️ 수동 |

---

## 설치

```bash
pip install python-dotenv requests wsgidav cheroot
# + 플랫폼 벤더가 제공하는 Python SDK

# 마운트용 (Linux)
sudo apt install rclone fuse3   # 또는 davfs2
```

## 설정

```bash
cp .env.example .env
```

```env
SERVER_URL=https://your-server.example.com
CLIENT_NAME=<OAuth2 client id>
ACCESS_TOKEN=<access token>
REFRESH_TOKEN=<refresh token>
DEST_DIR=./drive_data
```

> `.env` 는 `.gitignore` 에 포함되어 커밋되지 않습니다.

## 실행

```bash
python list_drive.py      # 파일 목록 확인
python download_all.py    # 전체 동기화 (SharedWithMe, trash 폴더 제외)

chmod +x mount_keep.sh
./mount_keep.sh           # 마운트 + 감시 데몬 실행
```

### cron (매일 02:00 동기화)

```cron
0 2 * * * cd /path/to/Server_S3_ETL && python download_all.py >> /var/log/s3_etl.log 2>&1
```

### systemd (재부팅 후 자동 마운트)

```ini
[Unit]
Description=S3 Drive Mount
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<your-user>
WorkingDirectory=/path/to/Server_S3_ETL
ExecStart=/path/to/Server_S3_ETL/mount_keep.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## 기술 스택

Python · OAuth2/OIDC · S3 Presigned URL · WebDAV (wsgidav, cheroot) · rclone / davfs2 (FUSE) · Bash · systemd · cron

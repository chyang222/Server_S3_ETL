# Samsung A1 — Altairone Drive ETL

Altairone Drive(S3 기반)에서 로컬 서버로 파일을 자동 복사하는 ETL 스크립트.

---

## 구조

```
04_개발/
├── .env              # 연결 설정 + 토큰 (gitignore 처리, 직접 생성 필요)
├── .env.example      # .env 템플릿
├── list_drive.py     # Drive 파일 목록 조회
├── download_all.py   # Drive → 로컬 전체 다운로드 (폴더 구조 유지)
├── mount_dav.py      # Drive → WebDAV 서버 (마운트용)
└── mount_keep.sh     # Linux 마운트 유지 데몬 스크립트
```

---

## 동작 방식

### 인증 (OAuth2 / OIDC)

Altairone은 **Ory Hydra** 기반 OAuth2 서버를 사용한다.  
지원 인증 방식: `authorization_code`, `implicit`, `client_credentials`, `refresh_token`, Device Authorization Grant  
**Password Grant(ID/PW 직접 입력)는 지원하지 않는다.**

토큰은 두 종류로 구성된다:

| 토큰 | 역할 | 수명 |
|------|------|------|
| Access Token | API 호출 인증 | ~24시간 |
| Refresh Token | Access Token 재발급 | 수일~수주 |

### 토큰 자동 갱신 흐름

```
[최초 1회] 브라우저 쿠키에서 토큰 복사 → .env 에 저장
    ↓
[스크립트 실행 시 자동]
  1. .env 에서 토큰 로드
  2. Refresh Token → 새 Access Token + 새 Refresh Token 발급
  3. 갱신된 토큰을 .env 에 덮어씀  ← 핵심: 매 실행마다 자동 갱신
  4. API 작업 수행 (파일 목록 조회 / 다운로드)
```

ETL이 주기적으로 실행되는 한 Refresh Token이 자동 갱신되므로 **추가 수동 개입 불필요**.  
ETL을 오래 중단했다가 재시작할 경우 Refresh Token이 만료될 수 있으며, 이때는 아래 [초기 설정](#초기-설정--토큰-발급)을 다시 수행한다.

### 파일 다운로드 방식

`siemens_scx` SDK의 내장 `adcopy.exe`는 서버 측 504 타임아웃 문제가 있어 사용하지 않는다.  
대신 `FsGetDownloadUrlAction` API로 **Oracle OCI S3 presigned URL**을 직접 발급받아 `requests`로 다운로드한다.

```
Drive 파일
  → FsGetDownloadUrlAction (SDK API)
  → Presigned URL (Oracle OCI S3, 유효시간 ~20시간)
  → requests.get() 직접 다운로드
```

---

## 설치

### 요구사항

- Python 3.8 이상
- `siemens_scx` SDK (사내 배포 패키지)
- `requests`, `python-dotenv`, `wsgidav`, `cheroot`

```bash
pip install python-dotenv requests wsgidav cheroot
pip install siemens_scx  # 사내 패키지 설치 방법에 따름
```

---

## 초기 설정 — 토큰 발급

**최초 1회만 수행**. 이후에는 자동 갱신된다.

### 1. 브라우저에서 토큰 추출

1. Altairone 웹 UI에 로그인
2. **F12** (개발자 도구) 열기
3. **Network** 탭 클릭
4. 브라우저에서 페이지 **새로고침 (F5)**
5. Network 탭에 요청 목록이 나타나면 아무 항목이나 클릭
6. 오른쪽 **Headers** 패널 → **Request Headers** → `Cookie` 항목 확인
7. Cookie 문자열 안에서 아래 세 값을 복사:

```
Cookie: ... access-token=eyJhbGci...; refresh-token=ory_rt_...; x-client-id=f4be8e42-...; ...
```

| 쿠키 이름 | 용도 | 변경 주기 |
|-----------|------|-----------|
| `x-client-id` | OAuth2 클라이언트 식별자 (UUID) | 고정 (변경 불필요) |
| `access-token` | API 인증 토큰 | ~24시간 |
| `refresh-token` | Access Token 재발급용 | 수일~수주 |

### 2. .env 파일 생성

```bash
cp .env.example .env
```

`.env`를 열고 복사한 값을 입력:

```env
SERVER_URL=https://your-altairone-server.example.com
CLIENT_NAME=<x-client-id 값>
ACCESS_TOKEN=<access-token 값>
REFRESH_TOKEN=<refresh-token 값>
DEST_DIR=./drive_data          # 또는 Linux 절대경로: /data/altairone/drive_data
```

> **주의**: `.env`는 `.gitignore`에 포함되어 있어 Git에 커밋되지 않는다.  
> 토큰이 외부에 노출되지 않도록 주의한다.

---

## 실행

### Drive 파일 목록 확인

```bash
python list_drive.py
```

### 전체 다운로드 (폴더 구조 유지)

```bash
python download_all.py
```

- 이미 존재하는 파일은 건너뜀 (재실행 안전)
- `SharedWithMe`, `trash` 폴더는 자동 제외
- 실행 후 `.env`의 토큰이 자동 갱신됨

---

## Drive 마운트 (WebDAV)

Drive를 로컬 폴더에 마운트하면 **파일 탐색기처럼** Drive 파일을 직접 탐색할 수 있다.

### 마운트 구조

```
mount_dav.py        → localhost:8888 WebDAV 서버 (Python)
    ↕ HTTP WebDAV
rclone / davfs2     → DEST_DIR (로컬 마운트 포인트)
```

### 사전 요구사항 (Linux)

```bash
# rclone 설치 (권장 — FUSE 기반, sudo 불필요)
curl https://rclone.org/install.sh | sudo bash
# 또는
sudo apt install rclone

# FUSE 지원 확인
sudo apt install fuse3   # Ubuntu 22.04+
```

### 마운트 유지 스크립트 실행

```bash
chmod +x mount_keep.sh

# 포그라운드 실행 (테스트용)
./mount_keep.sh

# 백그라운드 실행
nohup ./mount_keep.sh > mount_keep.log 2>&1 &
echo $! > .mount_keep.pid

# screen 세션으로 실행 (권장)
screen -S altairone-mount ./mount_keep.sh
# 분리: Ctrl+A, D  /  재접속: screen -r altairone-mount
```

`.env`의 `DEST_DIR`이 마운트 포인트로 사용된다.

### 마운트 해제

```bash
# 스크립트 종료 (Ctrl+C) 시 자동 해제
# 또는 수동 해제
fusermount -u <DEST_DIR>         # rclone 사용 시
sudo umount <DEST_DIR>           # davfs2 사용 시
```

### 마운트 지속성 — 얼마나 오래 유지되나?

| 항목 | 설명 | 자동 처리 |
|------|------|-----------|
| Access Token 만료 (~24h) | 서버 재시작으로 갱신 | ✅ `mount_keep.sh` 20h마다 재시작 |
| Refresh Token 만료 (수주) | .env 수동 갱신 필요 | ⚠️ ETL 중단 시 수동 갱신 |
| WebDAV 서버 크래시 | 자동 재시작 | ✅ `mount_keep.sh` 30초 감시 |
| 마운트 해제 감지 | 자동 재마운트 | ✅ `mount_keep.sh` 30초 감시 |
| 서버 재부팅 | systemd 서비스 등록 필요 | ⚠️ 아래 참고 |

**결론**: ETL이 정기적으로 돌아가는 한 Refresh Token이 순환되므로 **무기한 지속 가능**.  
장기간(수주) 중단 후 재시작 시에는 브라우저에서 토큰을 재발급해야 한다.

### systemd 서비스 등록 (재부팅 후 자동 시작)

```ini
# /etc/systemd/system/altairone-mount.service
[Unit]
Description=Altairone Drive Mount
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<your-user>
WorkingDirectory=/path/to/04_개발
ExecStart=/path/to/04_개발/mount_keep.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable altairone-mount
sudo systemctl start altairone-mount
sudo systemctl status altairone-mount
```

---

## ETL 자동화

Linux cron 예시 — 매일 새벽 2시 실행:

```cron
0 2 * * * cd /path/to/04_개발 && python download_all.py >> /var/log/altairone_etl.log 2>&1
```

### 토큰 만료 시 대응

Refresh Token이 만료되면 스크립트가 `HTTP 401 Unauthorized` 오류를 반환한다.  
이 경우 [초기 설정](#초기-설정--토큰-발급)을 다시 수행하여 `.env`의 `ACCESS_TOKEN`과 `REFRESH_TOKEN`을 갱신한다.

---

## 주요 API

| Action Type | 설명 |
|-------------|------|
| `altair.system.UserInfoAction` | 세션 유효성 확인 |
| `altair.system.FsLsAction` | 디렉토리 목록 조회 |
| `altair.system.FsInfoAction` | 파일/폴더 상세 정보 |
| `altair.system.FsGetDownloadUrlAction` | Presigned 다운로드 URL 발급 |

모든 API는 `POST /api/objectstore/actions/execute` 엔드포인트를 통해 호출된다.

# autocoin

빗썸 연동 자동매매 서버 (Python).

## 빠른 시작

```bash
cd /path/to/autocoin
./.venv/bin/pip install -e .
./.venv/bin/autocoin
# 또는 동일 진입점: ./.venv/bin/python -m autocoin
```

`pip install -e .` 후에는 `autocoin` 명령은 `pyproject.toml`의 `[project.scripts]`가 `.venv/bin/autocoin` 실행 파일을 만들어 줍니다.

Private API(잔고) 확인: `.env.example`을 참고해 `.env`에 키를 넣은 뒤:

```bash
./.venv/bin/autocoin --accounts
```

## FastAPI + PostgreSQL

1. DB 기동 (Docker):

   ```bash
   docker compose up -d
   ```

2. `.env`에 `DATABASE_URL` 설정 (`.env.example` 참고). 예:

   `postgresql+asyncpg://autocoin:***REMOVED***@127.0.0.1:5432/autocoin`

3. API 서버 실행:

   ```bash
   ./.venv/bin/autocoin-api
   ```

   - [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) — Swagger UI
   - `GET /health`, `GET /health/db` — 헬스체크
   - `POST /signals`, `GET /signals` — OpenCrawl 등에서 넘길 **신호 적재**(선택: `API_AUTH_TOKEN` 설정 시 `Authorization: Bearer …` 필요)

기동 시 SQLAlchemy가 **개발 편의를 위해** 테이블을 자동 생성합니다. 운영 환경에서는 마이그레이션(Alembic 등)으로 전환하는 것이 좋습니다.

## 시스템 패키지 (Ubuntu / Debian)

터미널에서 한 번 실행:

```bash
sudo apt-get update
sudo apt-get install -y direnv python3.12-venv pre-commit
```

설치 후 **가상환경을 표준 방식으로 다시 만들려면** (이미 `.venv`가 있으면 교체):

```bash
cd /path/to/autocoin
rm -rf .venv
python3 -m venv .venv
./.venv/bin/pip install -U pip
```

`pre-commit`은 apt로 깔면 시스템 `PATH`의 `pre-commit`을 쓰면 됩니다. 저장소에서 훅만 연결:

```bash
cd /path/to/autocoin
pre-commit install
```

## 셸 설정

- **direnv**: `~/.bashrc`(또는 zsh/fish)에 훅 추가 — `eval "$(direnv hook bash)"`
- 프로젝트 디렉터리 진입 시 `.envrc` 로드, 최초에는 `direnv allow` 필요할 수 있음.

## 구성 요약

- **가상환경**: 루트의 `.venv` — direnv가 `PATH`에 넣음 (`.envrc`).
- **pre-commit**: 커밋 시 `.pre-commit-config.yaml` 훅 실행.

## 참고 (uv)

`python3-venv`를 쓸 수 없을 때만, [uv](https://github.com/astral-sh/uv)로 `.venv`를 만들 수 있습니다.

# autocoin

빗썸 연동 자동매매 서버 (Python).

## 빠른 시작

```bash
cd /path/to/autocoin
./.venv/bin/pip install -e .
./.venv/bin/autocoin
# 또는: ./.venv/bin/python -m trader
```

`pip install -e .` 후 콘솔 스크립트 `autocoin` / `autocoin-api`는 내부적으로 **`trader`** 패키지를 실행합니다. 코드에서는 `import trader...` 를 씁니다.

Private API(잔고) 확인: `.env.example`을 참고해 `.env`에 키를 넣은 뒤:

```bash
./.venv/bin/autocoin --accounts
```

## 환경 변수 (`.env`)

예민한 값은 **전부 `.env`** 에 두고, 저장소에는 **`.env.example`만** 둡니다.

```bash
cp .env.example .env
# 비밀번호·API 키 등 수정
```

`trader.config.Settings`는 프로젝트 루트의 `.env`를 읽습니다. Docker의 `ct-api`는 같은 파일을 `env_file`로 넣되, **`DATABASE_URL`만** compose의 `DATABASE_URL_DOCKER`로 덮어써서 컨테이너 안에서는 호스트명 `ct-db`를 쓰게 합니다.

Docker Compose는 **같은 디렉터리의 `.env`** 를 읽어 `${POSTGRES_USER}` 같은 치환에 씁니다. `POSTGRES_*`와 `DATABASE_URL_DOCKER`의 계정·비밀번호는 **서로 맞출 것**.

비밀번호에 `@`, `:`, `#` 등 URL에 쓰이는 문자가 있으면 `DATABASE_URL` / `DATABASE_URL_DOCKER` 에서 **퍼센트 인코딩**이 필요합니다.

## FastAPI + PostgreSQL

### Docker Compose (DB + API 한 번에)

`ct-db`와 `ct-api`(Coin Trader)가 **`internal` 브리지 네트워크**에서만 서로 붙습니다.

```bash
docker compose up -d --build
```

- API 이미지: 루트 `Dockerfile` — `src/`, `pyproject.toml`, `alembic` 복사 후 `pip install .`
- 컨테이너 기동 시 `docker/entrypoint.sh`가 **`alembic upgrade head`** 후 **`uvicorn trader.api.app:app`** 실행
- 브라우저: `http://127.0.0.1:8000/docs` (`.env`의 `API_PUBLISH_PORT`로 바꿀 수 있음)
- **호스트 포트**: `POSTGRES_PUBLISH_PORT`(기본 5432), `API_PUBLISH_PORT` + 컨테이너 내부 `API_PORT`(기본 8000) — `.env.example` 참고
- `.env`에 `BITHUMB_*`, `API_AUTH_TOKEN`, `DB_ECHO_SQL` 등을 넣으면 컨테이너 앱에도 그대로 전달됩니다

Postgres를 호스트에서 직접 쓰려면 `POSTGRES_PUBLISH_PORT`로 노출한 포트를 씁니다. **DB를 밖에 안 열려면** `docker-compose.yml`의 `ct-db` `ports` 블록을 제거하면 됩니다.

### 로컬(venv)에서만 개발할 때

1. DB만 Docker: `docker compose up -d ct-db` (또는 전체 compose 후 API는 끔)
2. `.env`의 `DATABASE_URL`: `127.0.0.1` + `POSTGRES_*`와 동일 계정
3. `./.venv/bin/alembic upgrade head`
4. `./.venv/bin/autocoin-api`

이전에 `create_all`만 쓰다 옮긴 DB는 한 번 `alembic stamp 0001` 로 맞출 수 있습니다.

스키마 변경 시: 모델 수정 후 `alembic revision --autogenerate -m "설명"` → 검토 후 `alembic upgrade head`.

엔드포인트: `GET /health`, `GET /health/db`, `POST/GET /signals` (선택: `API_AUTH_TOKEN` + `Authorization: Bearer …`).

## 코드 레이아웃

설치 가능한 패키지는 **`src/trader/`** 한 곳에만 둡니다 (저장소 폴더 이름 `autocoin`과 겹치지 않음).

| 경로 | 역할 |
|------|------|
| `src/trader/models/` | SQLAlchemy ORM |
| `src/trader/schemas/` | Pydantic 요청/응답 스키마 |
| `src/trader/services/` | 비즈니스 로직 |
| `src/trader/routers/` | FastAPI 라우터 |
| `src/trader/api/` | 앱 조립(`app.py`), DI(`deps.py`) |
| `alembic/` | PostgreSQL 마이그레이션 |

### DB 테이블이 세 가지인 이유

| 테이블 | 용도 |
|--------|------|
| **`signal_events`** | 외부(예: OpenCrawl)가 API로 보낸 **매매 의도**만 기록합니다. `buy` / `sell` / `hold`, 근거 텍스트, 원본 JSON 등. **아직 거래소에 주문이 나간 것과는 별개**입니다. |
| **`trades`** | 빗썸 등에서 **실제로 체결·주문된 사실** (수량, 가격, 주문 ID, 수수료 등). 손익·감사용입니다. |
| **`research_items`** | 뉴스·리포트·크롤 결과 등 **시장 판단에 쓰는 자료**를 모읍니다. 원문·URL·요약·`structured_payload`(정리된 필드)를 둡니다. |

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

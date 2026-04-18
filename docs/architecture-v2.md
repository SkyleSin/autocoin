# autocoin v2 아키텍처 설계서

## 1. 개요

싱글 프로세스 모놀리스 → 멀티 컨테이너 구조로 전환.
멀티유저(100+) 지원, Redis 기반 상태/큐/락 관리.

---

## 2. 컨테이너 구성

```
┌─────────────────────────────────────────────────────┐
│                    인프라 레이어                       │
│  ct-db (PostgreSQL 16)    ct-redis (Redis 7)         │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                    서비스 레이어                       │
│  ct-scheduler   수집 + 분석 + Safety 모니터           │
│  ct-strategy    유저별 전략 평가 + 시그널 판단          │
│  ct-worker      거래소 API 실행 (인스턴스 2~3개)       │
│  ct-telegram    텔레그램 봇 수신/발송 전담              │
│  ct-api         헬스체크, 웹훅                        │
└─────────────────────────────────────────────────────┘
```

### 2.1 컨테이너별 역할

| 컨테이너 | 역할 | 현재 코드 매핑 |
|----------|------|---------------|
| **ct-db** | PostgreSQL 16 | 기존 유지 |
| **ct-redis** | 상태/큐/락/pub-sub/레이트리밋/캐시 | 신규 |
| **ct-scheduler** | 캔들 수집, 지표 계산, 패턴/S/R/추세 분석, Safety 모니터 | `collect_scheduler`, `handlers/candle.py`, `handlers/indicators.py`, `monitor.py`, `shock_monitor.py` |
| **ct-strategy** | 유저별 전략 평가, 시그널 판단, 섀도우 기록 | `strategy_scheduler`, `strategy_*.py`, `topdown_*.py`, `signal_notifier.py` |
| **ct-worker** | 거래소 주문/체결/잔고 조회 (2~3 인스턴스) | `worker.py`, `poller.py`, `handlers/trade.py` |
| **ct-telegram** | 텔레그램 폴링 수신, 명령어 파싱, 메시지 발송 | `telegram/poller.py`, `telegram/handler.py`, `telegram/bot.py` |
| **ct-api** | 헬스체크, 외부 웹훅 | `app.py` (라우터만) |

---

## 3. 공통 vs 유저별 처리 경계

```
[공통 — 1회 실행]                    ct-scheduler
  캔들 수집 → 지표 계산 → 패턴 인식
  → S/R → 추세 판단
  → indicator_snapshots DB 저장

[유저별 — N회 실행]                  ct-strategy
  유저 설정 로드 (전략, 파라미터, 코인 목록, 임계값)
  → 전략 평가 (유저 파라미터 적용)
  → 시그널 판단 → Signal DB 저장
  → 알림 요청 → 매매 요청
```

분석 결과(지표, 패턴, S/R, 추세)는 객관적 데이터 → 1회 계산.
전략 평가부터 유저별 판단 기준 적용.

---

## 4. 데이터 흐름

```
ct-scheduler (공통, 1회 실행)
  │  캔들 수집 → 지표 계산 → 패턴/S/R/추세
  │  → indicator_snapshots DB 저장
  │  → Redis pub "analysis_ready:{market}:{tf}"
  │
  │  Safety 모니터 (1분 주기)
  │  → 지지선 이탈 / 충격 감지
  │  → 위험 시 Redis queue "trade_jobs" (emergency_sell)
  │  → Redis pub "send_message" (경고 알림)
  │
ct-strategy (유저별, N회 실행)
  │  ← Redis sub "analysis_ready"
  │  → DB에서 분석 결과 읽기 (공통 데이터)
  │  → 유저별 전략/파라미터로 판단
  │  → Signal DB 저장
  │  → Redis pub "send_message" (시그널 알림 + 차트 + 버튼)
  │
ct-telegram (수신/발송 전담)
  │  [수신] 텔레그램 폴링 → 명령어/버튼 콜백 파싱
  │    → 매매 승인 → Redis queue "trade_jobs"
  │    → 일반 명령어 → 직접 처리 or Redis 전달
  │  [발송] ← Redis sub "send_message"
  │    → 유저별 텔레그램 발송 (throttling)
  │
ct-worker (인스턴스 2~3개, 공유 큐)
  │  ← Redis BRPOP "trade_jobs"
  │
  │  Job: execute_trade
  │    → Redis lock:trade:{user_id} 획득
  │    → Redis rate_limit:{user_id} 확인 (초당 15회)
  │    → check_balance → place_order
  │    → lock 해제
  │    → Redis 지연 Job 등록: confirm_trade (30초 후)
  │
  │  Job: confirm_trade (30초 후 pop)
  │    → check_order
  │    → 미체결 시 cancel or 재확인
  │    → DB 결과 저장
  │    → Redis pub "send_message" (체결 결과 알림)
  │
ct-api
     헬스체크, 외부 웹훅 수신
```

---

## 5. Redis 설계

### 5.1 역할별 키 패턴

| 용도 | 키 패턴 | 타입 | 사용처 |
|------|---------|------|-------|
| **매매 큐** | `trade_jobs` | List (BRPOP) | ct-telegram → ct-worker |
| **지연 큐** | `delayed_jobs` | Sorted Set (score=실행시각) | confirm_trade 30초 후 실행 |
| **이벤트** | `analysis_ready` | Pub/Sub (payload에 market/tf 포함) | ct-scheduler → ct-strategy |
| **알림** | `send_message` | Pub/Sub | 모든 컨테이너 → ct-telegram |
| **분산 락** | `lock:trade:{user_id}` | String (SET NX EX) | ct-worker 유저별 매매 직렬화 |
| **레이트리밋** | `rate_limit:{user_id}` | Sorted Set (슬라이딩 윈도우) | ct-worker 유저별 초당 15회 |
| **방어 모드** | `defense:{market}` | Hash | 방어 모드 상태 (진입/해제/손절선) |
| **이탈 카운터** | `breach:{market}` | String (INCR) | 지지선 이탈 연속 카운트 |
| **충격 쿨다운** | `shock_cooldown:{market}` | String (SET EX) | 충격 감지 중복 방지 |
| **안전 쿨다운** | `safety_triggered:{market}` | String (SET EX) | 지지선 이탈 LLM 트리거 쿨다운 |
| **가중치 캐시** | `weights:{timeframe}` | Hash | 지표 가중치 (TTL 관리) |
| **포지션 캐시** | `position:allocation`, `position:sell_rec` | Hash | 포지션 사이징 캐시 |

### 5.2 기존 인메모리 → Redis 이관 대상

| 현재 (인메모리) | 파일 | Redis 키 |
|----------------|------|----------|
| `_defense_states` | `defense_mode.py` | `defense:{market}` |
| `_breach_counts`, `_last_triggered` | `monitor.py` | `breach:{market}` |
| `_last_shock` | `shock_monitor.py` | `shock:{market}` |
| `_db_weights` | `signal_weights.py` | `weights:{timeframe}` |
| `_db_allocation`, `_db_sell_rec` | `position_sizing.py` | `position:config` |
| `_cache`, `_cache_ts` | `pattern_service.py` | `pattern:config` |
| `_strategy_queue` | `handlers/indicators.py` | Redis pub/sub로 대체 |

---

## 6. ct-worker 상세 설계

### 6.1 공유 큐 + 유저별 제어

```
Redis 공유 큐: trade_jobs
  {user_id: 42, type: "execute_trade", payload: {...}}
  {user_id: 73, type: "execute_trade", payload: {...}}

ct-worker (인스턴스 2~3개)
  → BRPOP으로 Job pop (원자적, 중복 없음)
  → user_id로 해당 유저 API 키 조회 (DB, 암호화)
  → user_id별 rate limiter 적용 (Redis 슬라이딩 윈도우)
  → 실행
```

### 6.2 매매 Job 분리

| Job | 동작 | 소요시간 |
|-----|------|---------|
| `execute_trade` | lock 획득 → check_balance → place_order → lock 해제 → 지연 Job 등록 | ~1~2초 |
| `confirm_trade` | check_order → 미체결 처리 → DB 저장 → 알림 | ~0.5초 |

30초 대기를 Redis 지연 큐(Sorted Set)로 처리하여 워커가 묶이지 않음.

### 6.3 레이트 리밋

```
키: rate_limit:{user_id} (Sorted Set)
방식: 슬라이딩 윈도우 (현재 rate_limit.py 로직 재사용)
제한: 유저별 초당 15회 (빗썸 API 제한)
유저 간 독립: 유저A가 15회 소진해도 유저B는 영향 없음
```

### 6.4 분산 락

```
키: lock:trade:{user_id}
방식: SET NX EX (Redis 원자적 락)
TTL: 30초 (execute_trade 최대 소요시간)
목적: 한 유저의 매매가 동시에 2개 실행되지 않도록 직렬화
```

### 6.5 스케일링

```
유저 100명 기준:
  스파이크 시: ~300 Jobs (100명 × 3 API 호출)
  워커 2~3개 × asyncio 동시 20~30개 = 동시 60~90 Job
  처리 시간: ~2.5초 (300 ÷ 60 × 0.5s)
  평소: 거의 유휴 상태
```

---

## 7. 유저 데이터 모델 (신규)

### 7.1 users 테이블

```sql
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    -- 거래소 인증 (AES-256 암호화)
    bithumb_api_key     TEXT,
    bithumb_secret_key  TEXT,
    -- 전략 설정
    strategy_type   VARCHAR(32) DEFAULT 'topdown_mtf',
    strategy_params JSONB DEFAULT '{}',
    coin_watchlist  JSONB DEFAULT '[]',
    -- 리스크 설정
    risk_max_per_trade_pct  FLOAT DEFAULT 5.0,
    risk_max_daily_trades   INT DEFAULT 10,
    risk_min_krw_reserve    FLOAT DEFAULT 100000,
    -- 상태
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### 7.2 user_signals 테이블

```sql
CREATE TABLE user_signals (
    id          SERIAL PRIMARY KEY,
    user_id     INT REFERENCES users(id),
    signal_id   INT REFERENCES signals(id),
    action      VARCHAR(16),       -- 승인 / 패스
    traded_at   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

### 7.3 기존 테이블 변경

```sql
-- signals: user_id 추가 (유저별 시그널)
ALTER TABLE signals ADD COLUMN user_id INT REFERENCES users(id);

-- trades: user_id 추가 (유저별 거래 기록)
ALTER TABLE trades ADD COLUMN user_id INT REFERENCES users(id);

-- active_alerts: user_id 추가 (유저별 알림 쿨다운)
ALTER TABLE active_alerts ADD COLUMN user_id INT REFERENCES users(id);
```

---

## 8. 빗썸 API 키 관리

```
master 계정: 기존 .env의 BITHUMB_API_KEY / SECRET
  → ct-scheduler에서 공통 데이터 수집용 (공개 API)
  → 관리자 직접 매매용

유저 계정: DB users.bithumb_api_key / secret_key
  → AES-256 암호화 저장 (암호화 키는 환경변수)
  → ct-worker가 런타임에 복호화하여 사용
  → 유저별 독립 레이트 리밋
```

---

## 9. docker-compose.yml (v2)

```yaml
services:
  # ── 인프라 ──
  ct-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-autocoin}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-autocoin}
      POSTGRES_DB: ${POSTGRES_DB:-autocoin}
    volumes:
      - /cache/autocoin/pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - internal

  ct-redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - /cache/autocoin/redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - internal

  # ── 서비스 ──
  ct-api:
    build: &build
      context: .
      dockerfile: Dockerfile
    command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
    env_file: &envfile
      - path: .env
        required: false
    environment: &common_env
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-autocoin}:${POSTGRES_PASSWORD:-autocoin}@ct-db:5432/${POSTGRES_DB:-autocoin}
      REDIS_URL: redis://ct-redis:6379/0
    ports:
      - "${API_PUBLISH_PORT:-8000}:8000"
    depends_on: &depends
      ct-db:
        condition: service_healthy
      ct-redis:
        condition: service_healthy
    networks:
      - internal

  ct-scheduler:
    build: *build
    command: ["python", "-m", "entrypoints.scheduler"]
    env_file: *envfile
    environment:
      <<: *common_env
      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
      # master 계정 (공개 API 수집용)
      BITHUMB_API_KEY: ${BITHUMB_API_KEY}
      BITHUMB_SECRET_KEY: ${BITHUMB_SECRET_KEY}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./log:/app/log
    depends_on: *depends
    networks:
      - internal

  ct-strategy:
    build: *build
    command: ["python", "-m", "entrypoints.strategy"]
    env_file: *envfile
    environment:
      <<: *common_env
    volumes:
      - ./log:/app/log
    depends_on: *depends
    networks:
      - internal

  ct-worker:
    build: *build
    command: ["python", "-m", "entrypoints.worker"]
    env_file: *envfile
    environment:
      <<: *common_env
      ENCRYPT_KEY: ${ENCRYPT_KEY}
    volumes:
      - ./log:/app/log
    depends_on: *depends
    deploy:
      replicas: ${WORKER_REPLICAS:-2}
    networks:
      - internal

  ct-telegram:
    build: *build
    command: ["python", "-m", "entrypoints.telegram"]
    env_file: *envfile
    environment:
      <<: *common_env
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    volumes:
      - ./log:/app/log
    depends_on: *depends
    networks:
      - internal

networks:
  internal:
    driver: bridge
```

---

## 10. 엔트리포인트 구조 (신규)

```
src/entrypoints/
  ├── scheduler.py    # collect_scheduler + safety_monitor + shock_monitor
  ├── strategy.py     # Redis sub → 유저별 전략 평가 루프
  ├── worker.py       # Redis BRPOP → execute_trade / confirm_trade
  └── telegram.py     # 텔레그램 폴링 + Redis sub send_message → 발송
```

각 엔트리포인트는 독립 프로세스로 실행되며, DB 세션과 Redis 연결만 초기화.
현재 `app.py`의 lifespan에서 하던 6개 태스크가 자연스럽게 분산됨.

---

## 11. 마이그레이션 단계

### Phase 1: Redis 도입 + 인메모리 상태 이관
- ct-redis 컨테이너 추가
- 7개 인메모리 상태 → Redis 이관
- 기존 모놀리스는 유지 (Redis 클라이언트만 추가)

### Phase 2: ct-worker 분리
- Job 큐를 DB 폴링 → Redis 큐로 전환
- execute_trade / confirm_trade Job 분리
- 유저별 rate limiter + 분산 락 구현
- entrypoints/worker.py 생성

### Phase 3: ct-telegram 분리
- 텔레그램 폴링/발송을 독립 프로세스로
- Redis pub/sub "send_message" 채널 구현
- 다른 컨테이너에서 텔레그램 직접 호출 제거

### Phase 4: ct-scheduler + ct-strategy 분리
- collect_scheduler + safety를 독립 프로세스로
- strategy_scheduler를 Redis sub 기반으로 전환
- 유저별 전략 평가 로직 구현

### Phase 5: 유저 시스템
- users 테이블 + API 키 암호화
- 기존 테이블에 user_id 추가
- 유저 등록/설정 텔레그램 명령어

---

## 12. 환경변수 (신규/변경)

```bash
# Redis
REDIS_URL=redis://ct-redis:6379/0

# API 키 암호화
ENCRYPT_KEY=               # AES-256 암호화 키 (유저 거래소 API 키 보호)

# Worker
WORKER_REPLICAS=2          # ct-worker 인스턴스 수
WORKER_CONCURRENCY=20      # 인스턴스당 asyncio 동시 처리 수

# 기존 (master 계정 — ct-scheduler 전용)
BITHUMB_API_KEY=           # master 계정
BITHUMB_SECRET_KEY=        # master 계정
BITHUMB_MAX_REQUESTS_PER_SEC=15

# 텔레그램 (서비스 봇 1개)
TELEGRAM_BOT_TOKEN=        # 서비스 봇
```

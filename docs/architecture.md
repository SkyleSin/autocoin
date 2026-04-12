# autocoin 아키텍처

AI 기반 암호화폐 자동매매 시스템 (빗썸 거래소 대상)

## 전체 구조 개요

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        외부 서비스                                       │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ 빗썸 REST │  │ DeepSeek R1  │  │ Ollama      │  │ 텔레그램 Bot   │  │
│  │ API      │  │ API (우선)   │  │ gemma3:4b   │  │ API           │  │
│  └────┬─────┘  └──────┬───────┘  └──────┬──────┘  └───────┬────────┘  │
└───────┼───────────────┼────────────────┼──────────────────┼────────────┘
        │               │                │                  │
┌───────┼───────────────┼────────────────┼──────────────────┼────────────┐
│       ▼               ▼                ▼                  ▼            │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                FastAPI 앱 (ct-api Docker)                       │  │
│  │                                                                 │  │
│  │  ┌───────────────────────────────────────────────────────────┐  │  │
│  │  │ Unified Scheduler (단일 루프)                              │  │  │
│  │  │  • 캔들 수집 Job 생성 (15분 주기, 분석TF + 참조TF)         │  │  │
│  │  │  • 일봉 수집 + 패턴 판정 + Job 정리 (KST 00:05)           │  │  │
│  │  │  • 수익률 리포트 전송 (KST 09:00 / 21:00)                 │  │  │
│  │  └───────────────────────────────────────────────────────────┘  │  │
│  │                                                                 │  │
│  │  ┌──────────┐  ┌──────────────────────────────────────┐        │  │
│  │  │Job Poller│─▶│ Job Workers (N개, PriorityQueue)     │        │  │
│  │  │(DB→큐)   │  │ 레이트 리밋 + 재시도 + Job 체이닝      │        │  │
│  │  └──────────┘  └──────────────────────────────────────┘        │  │
│  │                                                                 │  │
│  │  ┌────────────────┐  ┌────────────────────────────────┐        │  │
│  │  │Telegram Poller │  │ Safety Monitor (1분 주기)       │        │  │
│  │  │(명령어/버튼)    │  │ 지지선 이탈 감시 → 긴급 매도     │        │  │
│  │  └────────────────┘  └────────────────────────────────┘        │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │              PostgreSQL 16 (ct-db, 256GB NVMe)                  │  │
│  │  candles │ signals │ trades │ jobs │ active_alerts │ ...        │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
```

## 백그라운드 태스크 (lifespan)

| 태스크 | 설명 |
|--------|------|
| **Job Poller** | DB에서 Ready/Failed Job을 SKIP LOCKED로 클레임 → PriorityQueue |
| **Job Workers** (N개) | 큐에서 Job 디스패치, 빗썸 API 레이트 리밋 공유, 실패 시 재시도 |
| **Unified Scheduler** | 캔들 수집(15분) + 일봉/판정(00:05) + 리포트(09/21시) 단일 루프 |
| **Telegram Poller** | getUpdates long polling으로 명령어/인라인 버튼 수신 |
| **Safety Monitor** | 보유 코인 1분마다 현재가 조회, 지지선 이탈 시 LLM 긴급 판단 → 자동 매도 |

## Job 체이닝 흐름

```
Unified Scheduler
    │
    ▼
collect_candles ──▶ analyze_technical ──BUY/SELL──▶ analyze_local_llm
  (빗썸 캔들 수집)    (4단계 시그널 판단)              (DeepSeek R1 → Ollama 폴백)
                                                        │
                                          ┌─────────────┼───────────────┐
                                          ▼             ▼               ▼
                                   텔레그램 알림    섀도우 트레이딩   LLM HOLD 알림
                                   (차트+버튼)     (가상 매매 기록)   (간략 상태)
                                          │
                              ┌───────────┼───────────┐
                              ▼                       ▼
                        [매매실행]                  [패스]
                              │                   passed_at 마킹
                              ▼
                        check_balance ──▶ place_order ──30초──▶ check_order
                        (잔고+리스크)      (시장가 주문)          (체결 확인)
                              │                                      │
                     잔고 부족 시                              미체결 시
                              ▼                                      ▼
                        리밸런싱 제안                          사용자 알림
                     (약한 코인 매도 추천)                [취소후 재주문][유지][취소]
```

## 시그널 판단 4단계

```
┌─────────────────────────────────────────────────────────────────┐
│ 1단계: 트리거 (S/R — 필수)                                       │
│   지지선 반등 → BUY +0.40  │  저항선 돌파(거래량) → BUY +0.40   │
│   지지선 이탈(거래량) → SELL +0.45  │  저항선 저항 → SELL +0.35  │
│   S/R 이벤트 없음 → 즉시 HOLD                                    │
├─────────────────────────────────────────────────────────────────┤
│ 2단계: 확인 (기술 지표 — 1개 이상 필수)                           │
│   MACD 히스토그램 부호 전환 (+0.30)                               │
│   RSI 과매수>70 / 과매도<30 (+0.35)                               │
│   볼린저밴드 상단/하단 이탈 (+0.25)                                │
│   거래량 1.5x 확인 (돌파/이탈 시 필수)                             │
│   지표 확인 0개 → HOLD                                            │
├─────────────────────────────────────────────────────────────────┤
│ 3단계: 보조 (차트 패턴 — BTC/ETH만)                               │
│   활성 패턴 1개만 반영, 신뢰도 +10~20% 부스트 (최대 +0.20)        │
│   패턴 없어도 시그널 가능 (참조용)                                 │
├─────────────────────────────────────────────────────────────────┤
│ 4단계: SMA 게이트 + MTF                                          │
│   SMA 20/50 방향 게이트 (BUY=정배열, SELL=역배열 필수)            │
│   상위 추세(1h/4h): BUY + 하락추세 → HOLD                        │
│   threshold ≥ 0.4                                                │
└─────────────────────────────────────────────────────────────────┘
```

## 프로젝트 구조

```
src/
├── app.py                    # FastAPI 앱, lifespan
├── unified_scheduler.py      # 통합 스케줄러 (캔들/일봉/리포트 단일 루프)
├── ai_engine/                # AI 판단 엔진
│   ├── deepseek_llm.py       #   DeepSeek R1 API (우선)
│   ├── local_llm.py          #   Ollama gemma3:4b (폴백)
│   ├── prompts.py            #   LLM 프롬프트 (지표+패턴+S/R+추세)
│   └── emergency_prompt.py   #   긴급 지지선 이탈 판단용
├── analysis/                 # 기술 분석
│   ├── indicators.py         #   RSI, MACD, BB, SMA, EMA, 거래량
│   ├── signal_generator.py   #   4단계 시그널: 트리거→확인→보조→게이트
│   ├── support_resistance.py #   지지/저항선 자동 탐지
│   ├── patterns.py           #   패턴 인식 메인 (BTC/ETH만)
│   ├── trend.py              #   상위 TF 추세 판단 (MTF)
│   └── chart.py              #   캔들차트 이미지 생성
├── bithumb/                  # 빗썸 API 클라이언트
│   ├── account.py            #   잔고 조회
│   ├── candle.py             #   캔들 데이터
│   ├── ticker.py             #   현재가
│   └── ...
├── collector/                # 데이터 수집 유틸
│   ├── scheduler.py          #   캔들 수집 Job 생성 유틸
│   └── daily_scheduler.py    #   일봉 백필/수집, 패턴 캐시, Job 정리 유틸
├── executor/                 # 매매 실행
│   ├── position_sizing.py    #   패턴 기반 포지션 사이징
│   └── risk_manager.py       #   일일 횟수, 최소 잔고 검증
├── job_runner/               # 비동기 작업 큐
│   ├── poller.py             #   DB → PriorityQueue 클레임
│   ├── worker.py             #   Job 디스패치 + 핸들러
│   └── rate_limit.py         #   슬라이딩 윈도우 레이트 리미터
├── models/                   # SQLAlchemy ORM
│   ├── candle.py             #   OHLCV (market+timeframe+open_at unique)
│   ├── signal.py             #   시그널 이력
│   ├── job.py                #   비동기 Job
│   ├── trade.py              #   매매 기록
│   ├── active_alert.py       #   알림 중복 방지
│   ├── pattern_config.py     #   패턴 활성/적중률/사이징
│   ├── shadow_trade.py       #   섀도우 트레이딩
│   └── ...
├── safety/                   # 긴급 매도 안전장치
│   └── monitor.py            #   지지선 이탈 감시 → 자동 매도
├── services/                 # 비즈니스 로직
│   ├── alert_service.py      #   알림 관리 (4시간 쿨다운)
│   ├── pattern_service.py    #   패턴 DB 관리 (적중률 판정/갱신)
│   ├── shadow_service.py     #   섀도우 트레이딩
│   ├── report_service.py     #   리포트 텍스트 생성
│   └── report_scheduler.py   #   리포트 잔고/현재가 조회 유틸
├── telegram/                 # 텔레그램 봇
│   ├── bot.py                #   메시지/이미지/버튼 전송
│   ├── handler.py            #   명령어 라우팅
│   ├── messages.py           #   시그널 알림 포맷
│   └── poller.py             #   long polling 루프
└── utils/                    # 유틸
    ├── config.py             #   Settings (pydantic-settings)
    └── logging_setup.py      #   KST 타임스탬프 로깅
```

## Job Types

| Job Type | 설명 | 체이닝 |
|----------|------|--------|
| `collect_candles` | 빗썸 캔들 수집 → DB upsert | → `analyze_technical` (ref_only 시 스킵) |
| `analyze_technical` | 4단계 시그널 판단 + MTF 추세 | → `analyze_local_llm` (BUY/SELL 시) |
| `analyze_local_llm` | DeepSeek R1 → Ollama 폴백 → 알림 | → `check_balance` (매매실행 시) |
| `check_balance` | 잔고 조회 + 리스크 검증 | → `place_order` 또는 리밸런싱 제안 |
| `place_order` | 시장가 주문 실행 | → `check_order` (30초 후) |
| `check_order` | 체결 확인, 미체결 시 재확인 (최대 2회) | → Trade 기록 또는 사용자 알림 |
| `cancel_order` | 주문 취소 | → 선택적 시장가 재주문 |

## 텔레그램 명령어

| 명령어 | 단축 | 기능 |
|--------|------|------|
| `/balance` | `/b` | 빗썸 잔고 조회 |
| `/assets` | `/a` | 추적 자산 목록 |
| `/add KRW-BTC` | | 추적 자산 추가 |
| `/remove KRW-BTC` | `/rm` | 추적 자산 제거 |
| `/risk` | | 리스크 설정 보기/변경 |
| `/history` | `/h` | 최근 매매 이력 |
| `/status` | `/s` | 시스템 상태 |
| `/report` | `/r` | 수익률 + 적중률 + 섀도우 리포트 |
| `/report_detail` | `/rd` | LLM 판단 카드형 상세 리포트 |

## 안전장치

### 리스크 관리
- **1회 매매 상한**: 잔고의 15% (DB 설정, `/risk` 명령으로 변경)
- **일일 매매 횟수**: 30회 (안전장치 자동매도는 우회)
- **최소 유지 잔고**: 5,000원
- **매도 비율**: 확신도 기반 (80%+→80%, 60%+→60%, 40%+→40%, 그 외→20%)

### 긴급 매도 (Safety Monitor)
- 1분마다 보유 코인 현재가 조회
- 지지선 5%+ 이탈 → 즉시 LLM 긴급 판단
- 지지선 1.5~5% 이탈 × 5분 연속 → LLM 긴급 판단
- SELL 판단 시 사용자 확인 없이 시장가 전량 매도 + 사후 알림

### 알림 관리
- 4시간 쿨다운 (NEW/CHANGED/REMIND 공용)
- HOLD 시 기존 알림 유지 (NEW 스팸 방지)
- 패스 → 동일 패턴이면 재알림 안 함, 패턴 변경 시 해제
- 전량 매도 후 acted_at 마킹 → 동일 시그널 SKIP

## 인프라

```
Docker Compose
├── ct-db   — PostgreSQL 16 (/cache/autocoin/pgdata, 256GB NVMe)
└── ct-api  — FastAPI + Uvicorn (Python 3.12)

서버: AMD Ryzen 5 5600X, 32GB RAM, GTX 750 (Ollama CPU 전용)
```

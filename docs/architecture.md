# autocoin 자동매매 아키텍처

## 전체 구조 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                        autocoin 시스템                           │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ Data Collector│    │  AI Engine   │    │  Trade Executor  │  │
│  │  (시세 수집)  │───▶│  (판단 엔진)  │───▶│  (매매 실행)     │  │
│  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘  │
│         │                   │                      │            │
│         ▼                   ▼                      ▼            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   PostgreSQL (ct-db)                     │   │
│  │  candle_1m │ candle_5m │ signal │ trade │ job │ ...     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │            기존 Job Runner (Poller + Worker)              │  │
│  │     모든 빗썸 API 호출은 Job으로 실행 (레이트 리밋 준수)     │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 1단계: 데이터 수집 파이프라인 (`src/collector/`)

```
src/collector/
├── scheduler.py        # 주기별 수집 스케줄링 (1분봉, 5분봉, 일봉)
├── candle_collector.py # 빗썸 캔들 데이터 수집 → DB 적재
└── snapshot.py         # 오더북/체결 스냅샷 (선택적)
```

**동작 방식:**
- `lifespan`에서 스케줄러 태스크 시작
- 1분 주기로 추적 자산(`TrackedAsset`)의 캔들 데이터를 Job으로 생성
- Job Worker가 빗썸 API 호출 → 응답을 캔들 테이블에 적재
- 기존 레이트 리미터를 그대로 활용

**새 모델:**
```python
# models/candle.py
class Candle(Base):
    market: str          # "KRW-BTC"
    timeframe: str       # "1m", "5m", "1d"
    open_at: datetime    # 봉 시작 시각
    open, high, low, close: Decimal
    volume: Decimal
    # unique constraint: (market, timeframe, open_at)
```

## 2단계: 기술 지표 분석 (`src/analysis/`)

```
src/analysis/
├── indicators.py       # RSI, MACD, 볼린저밴드, 이동평균 계산
├── signal_generator.py # 지표 조합 → 시그널 생성 (BUY/SELL/HOLD)
└── models.py           # 시그널 데이터 모델
```

**동작 방식:**
- 새 캔들 적재 완료 시 트리거
- pandas로 최근 N개 캔들 로드 → 지표 계산 (CPU만, 비용 0)
- 규칙 기반 1차 필터링:
  - RSI < 30 + MACD 골든크로스 → `BUY_SIGNAL`
  - RSI > 70 + MACD 데드크로스 → `SELL_SIGNAL`
  - 그 외 → `HOLD` (여기서 끝, LLM 호출 안 함)
- **핵심:** `HOLD`가 아닌 경우에만 다음 단계(LLM)로 진행 → API 비용 절약

**새 모델:**
```python
# models/signal.py
class Signal(Base):
    market: str
    timeframe: str
    signal_type: str     # "BUY" | "SELL" | "HOLD"
    confidence: float    # 0.0 ~ 1.0
    indicators: dict     # {"rsi": 28.5, "macd": ..., ...}
    source: str          # "technical" | "local_llm" | "external_llm"
    acted_on: bool       # 매매 실행 여부
```

## 3단계: AI 판단 엔진 (`src/ai_engine/`)

```
src/ai_engine/
├── engine.py           # 3단계 판단 오케스트레이터
├── local_llm.py        # Ollama 7B 연동 (httpx → localhost:11434)
├── external_llm.py     # Claude/OpenAI API 연동
├── prompts/
│   ├── analysis.py     # 시장 분석 프롬프트 템플릿
│   └── decision.py     # 매매 판단 프롬프트 템플릿
└── risk.py             # 리스크 관리 (포지션 크기, 손절가)
```

**3단계 판단 흐름:**

```
캔들 데이터 + 지표
       │
       ▼
[1] 기술 지표 필터 ──HOLD──▶ 종료 (비용 0)
       │
    BUY/SELL 시그널
       │
       ▼
[2] 로컬 LLM (Ollama 7B) ──REJECT──▶ 종료 (비용 0)
       │
    CONFIRM + 분석 근거
       │
       ▼
[3] 외부 LLM (Claude API) ──REJECT──▶ 종료
       │
    CONFIRM + 최종 판단
       │
       ▼
   매매 실행 (Trade Executor)
```

**로컬 LLM 연동:**
- Ollama REST API (`http://localhost:11434/api/generate`)
- 입력: 기술 지표 요약 + 최근 가격 추세 (텍스트)
- 출력: JSON `{"decision": "BUY|SELL|HOLD", "confidence": 0.8, "reason": "..."}`
- 7B 모델이라 빠른 응답 (2~5초), 메모리 ~5GB

**외부 LLM 호출 조건 (비용 최소화):**
- 로컬 LLM confidence > 0.7 일 때만 호출
- 일일 호출 상한 설정 (예: 20회/일)
- 큰 금액 매매일수록 외부 확인 필수

## 4단계: 매매 실행 (`src/executor/`)

```
src/executor/
├── trade_executor.py   # 최종 매매 주문 생성/실행
├── position.py         # 포지션 관리 (보유량, 평단가)
└── risk_manager.py     # 손절/익절 라인, 최대 투자금 제한
```

**안전장치:**
- 1회 매매 금액 상한 (예: 총 자산의 5%)
- 일일 최대 매매 횟수 제한
- 손절선 자동 설정 (-3% 등)
- 매매 전 잔고 확인 (빗썸 Private API)

## 새로 필요한 빗썸 API 엔드포인트

| 기능 | 엔드포인트 | 용도 |
|------|-----------|------|
| 캔들 조회 | `GET /v1/candles/minutes/{unit}` | 시세 수집 |
| 잔고 조회 | `GET /v1/accounts` | 매매 전 확인 |
| 매수 주문 | `POST /v1/orders` | 매수 실행 |
| 매도 주문 | `POST /v1/orders` | 매도 실행 |
| 주문 조회 | `GET /v1/order` | 체결 확인 |

## Job Type 확장

기존 Job Runner를 그대로 활용하되, 새 Job Type 추가:

```python
# 데이터 수집
"collect_candles"     # 캔들 수집 → DB 적재
"collect_orderbook"   # 오더북 스냅샷 (선택)

# AI 판단
"analyze_technical"   # 기술 지표 계산 → 시그널 생성
"analyze_local_llm"   # 로컬 LLM 판단
"analyze_external_llm"# 외부 LLM 판단

# 매매 실행
"place_order"         # 주문 실행
"check_order"         # 주문 체결 확인
"check_balance"       # 잔고 확인
```

## 구현 우선순위

| 순서 | 작업 | 이유 |
|------|------|------|
| **1** | 캔들 모델 + 수집 | 데이터가 있어야 분석 가능 |
| **2** | 기술 지표 분석 | 비용 0, 바로 테스트 가능 |
| **3** | 로컬 LLM 연동 | Ollama 설치 후 비용 0 테스트 |
| **4** | 잔고 조회 + 주문 API | 매매 인프라 |
| **5** | 외부 LLM 연동 | 마지막에 연결 |
| **6** | 리스크 관리 + 자동화 | 실전 투입 전 필수 |

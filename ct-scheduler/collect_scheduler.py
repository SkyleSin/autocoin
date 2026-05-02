"""수집 스케줄러 — 시간 기반 트리거 전용.

봉 수집 트리거 + 포트폴리오 동기화 + 일별 태스크 + 정기 리포트.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

from infra.api.job import cleanup_jobs
from infra.api.portfolio import get_user_portfolio
from infra.api.report import generate_report, get_signal_summary
from infra.api.signal import cleanup_hold_signals
from infra.api.tracked_asset import get_active_markets
from infra.api.trade import get_trades
from infra.api.user import get_active_users
from infra.redis_pubsub import publish_master, publish_telegram
from infra.redis_queue import push_worker_job
from utils.config import TF_PERIODS, Settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
REPORT_HOURS_KST = [9, 21]


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def _tf_bucket(now: datetime, period_min: int) -> int:
    """현재 시각이 속한 TF 버킷 인덱스 — 버킷이 바뀌면 경계 통과."""
    epoch_min = int(now.timestamp()) // 60
    return epoch_min // period_min


def _next_kst_target(hour: int, minute: int) -> datetime:
    now_kst = datetime.now(KST)
    target = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_kst >= target:
        target += timedelta(days=1)
    return target.astimezone(UTC)


async def _build_user_report(
    user_id: int, price_map: dict[str, float],
) -> str | None:
    """유저 손익 리포트 텍스트 생성."""
    portfolio = await get_user_portfolio(user_id)
    if not portfolio:
        return None

    total_invested = 0.0
    total_current = 0.0
    coin_lines = []

    for item in portfolio:
        currency = item["currency"]
        if currency == "KRW":
            continue
        balance = float(item["balance"])
        avg_price = float(item.get("avg_buy_price", 0))
        if balance <= 0 or avg_price <= 0:
            continue
        current_price = price_map.get(currency)
        if not current_price:
            continue

        invested = avg_price * balance
        current_val = current_price * balance
        pnl = current_val - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0
        total_invested += invested
        total_current += current_val

        emoji = "🟢" if pnl >= 0 else "🔴"
        coin_lines.append(
            f"{emoji} `{currency}`: {pnl:+,.0f}원 ({pnl_pct:+.1f}%)"
            f" | 평균가 {avg_price:,.0f}"
        )

    total_pnl = total_current - total_invested
    total_pnl_pct = (
        (total_pnl / total_invested * 100) if total_invested > 0 else 0
    )

    lines = ["📊 *손익 리포트*\n"]
    lines.append(f"💰 총 평가액: {total_current:,.0f}원")
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    lines.append(
        f"{pnl_emoji} 총 손익: {total_pnl:+,.0f}원 ({total_pnl_pct:+.1f}%)\n"
    )
    if coin_lines:
        lines.append("*코인별 손익:*")
        lines.extend(coin_lines)

    trades = await get_trades(limit=5, user_id=user_id)
    if trades:
        lines.append("\n*최근 거래:*")
        for t in trades:
            side_emoji = "🟢" if t.get("side") == "buy" else "🔴"
            created = t.get("created_at", "")[:16].replace("T", " ")
            lines.append(
                f"{side_emoji} `{t['market']}` {t['side']}"
                f" p={t['price_krw']} [{t['status']}] {created}"
            )

    return "\n".join(lines)


def _next_report_target() -> datetime:
    now_kst = datetime.now(KST)
    today = now_kst.replace(minute=0, second=0, microsecond=0)
    candidates = [today.replace(hour=h) for h in REPORT_HOURS_KST]
    tomorrow = today + timedelta(days=1)
    candidates += [tomorrow.replace(hour=h) for h in REPORT_HOURS_KST]
    future = [c for c in candidates if c > now_kst]
    return min(future).astimezone(UTC)


# ---------------------------------------------------------------------------
# 봉 수집 트리거
# ---------------------------------------------------------------------------

async def _trigger_collect(markets: list[str], timeframe: str) -> None:
    """ct-worker에 캔들 수집 Job 전달."""
    for market in markets:
        await push_worker_job({
            "type": "collect_candles",
            "payload": {"market": market, "timeframe": timeframe},
        })
    logger.info("candle trigger: %s × %d markets → Redis", timeframe, len(markets))


async def _trigger_cache_1m(markets: list[str]) -> None:
    """ct-worker에 1분봉 Redis 캐시 Job 전달."""
    for market in markets:
        await push_worker_job({
            "type": "cache_candles_1m",
            "payload": {"market": market},
        })
    logger.debug("1m cache trigger: %d markets → Redis", len(markets))


async def _trigger_portfolio_sync() -> None:
    """활성 유저별 포트폴리오 동기화 Job → ct-worker."""
    try:
        users = await get_active_users()
    except Exception:
        logger.exception("portfolio sync: 유저 목록 조회 실패")
        return

    for user in users:
        user_id = user.get("id")
        if user_id is None:
            continue
        if user.get("is_shadow"):
            continue
        await push_worker_job({
            "type": "sync_portfolio",
            "user_id": str(user_id),
            "payload": {"user_id": user_id},
        })
    logger.info("portfolio sync trigger: %d users → Redis", len(users))


# ---------------------------------------------------------------------------
# 정기 리포트 발송
# ---------------------------------------------------------------------------


async def _send_scheduled_reports(settings: Settings) -> None:
    """9시/21시 정기 리포트 — master + 유저별 발송."""
    signal_text = ""
    try:
        resp = await get_signal_summary(hours=16)
        signal_text = resp.get("text", "")
    except Exception:
        logger.exception("report: 시그널 요약 조회 실패")

    master_chat = settings.master_telegram_id
    if master_chat:
        try:
            resp = await generate_report()
            master_text = resp.get("text", "")
            if signal_text:
                master_text += f"\n\n{signal_text}"
            await publish_master(master_chat, master_text)
            logger.info("report: master 리포트 발송")
        except Exception:
            logger.exception("report: master 리포트 실패")

    try:
        users = await get_active_users()
    except Exception:
        logger.exception("report: 유저 조회 실패")
        return

    try:
        assets = await get_active_markets()
        price_map = {
            a["market"].replace("KRW-", ""): a.get("current_price")
            for a in assets
        }
    except Exception:
        logger.exception("report: 마켓 조회 실패")
        price_map = {}

    for user in users:
        chat_id = str(user.get("telegram_id", ""))
        user_id = user.get("id")
        if not chat_id or not user_id:
            continue
        try:
            user_text = await _build_user_report(user_id, price_map)
            if not user_text:
                user_text = "📊 보유 자산이 없습니다."
            if signal_text:
                user_text += f"\n\n{signal_text}"
            await publish_telegram(chat_id, user_text)
            logger.info("report: 유저 %s 리포트 발송", chat_id)
        except Exception:
            logger.exception("report: 유저 %s 리포트 실패", chat_id)


# ---------------------------------------------------------------------------
# 방어 실행 헬퍼
# ---------------------------------------------------------------------------

async def _safe(coro, label: str) -> bool:
    """코루틴 실행 + 로깅. 성공 True, 실패 False (루프 중단 방지)."""
    try:
        await coro
        logger.info("%s 완료", label)
        return True
    except Exception:
        logger.exception("%s 실패", label)
        return False


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

async def collect_scheduler(settings: Settings) -> None:
    """수집 스케줄러 메인 루프 — 트리거 전용 (10초 주기).

    흐름:
      1. TF 경계 감지 → ct-worker에 collect_candles Job 전달
      2. 일별 태스크 (Job 정리, HOLD 시그널 정리, 마켓 동기화)
      3. 정기 리포트 발송
    """
    logger.info("collect_scheduler: 시작")

    last_bucket: dict[str, int] = {}
    last_1m_bucket: int = -1
    next_daily = _next_kst_target(0, 5)
    next_report = _next_report_target()

    try:
        while True:
            tick_start = datetime.now(UTC)

            # 활성 마켓 조회
            try:
                assets = await get_active_markets()
                markets = [a["market"] for a in assets] if assets else []
            except Exception:
                logger.exception("collect: 마켓 조회 실패")
                markets = []

            # 1분봉 캐시 트리거 (1분마다, 지연 없이 즉시)
            cur_1m = _tf_bucket(tick_start, 1)
            if last_1m_bucket != cur_1m:
                last_1m_bucket = cur_1m
                if markets:
                    await _safe(_trigger_cache_1m(markets), "1m cache trigger")

            # TF 경계 확인 → 봉 수집 트리거 (1분 지연: 봉 마감 확정 대기)
            delayed = tick_start - timedelta(minutes=1)
            for tf, period_min in TF_PERIODS.items():
                bucket = _tf_bucket(delayed, period_min)
                if last_bucket.get(tf) != bucket:
                    last_bucket[tf] = bucket
                    if markets:
                        await _safe(
                            _trigger_collect(markets, tf), f"candle trigger: {tf}"
                        )
                    # 15m 경계 → 포트폴리오 동기화 (전략 평가 전 잔고 갱신)
                    if tf == "15m":
                        await _safe(
                            _trigger_portfolio_sync(), "portfolio sync trigger"
                        )

            # 일별 태스크
            now = datetime.now(UTC)
            if now >= next_daily:
                await _safe(
                    cleanup_jobs(complete_days=3, error_days=7),
                    "collect/daily: job cleanup",
                )
                await _safe(
                    cleanup_hold_signals(days=7),
                    "collect/daily: HOLD 시그널 정리",
                )
                await _safe(
                    push_worker_job({"type": "sync_markets", "payload": {}}),
                    "collect/daily: 마켓 동기화 트리거",
                )
                next_daily = _next_kst_target(0, 5)

            # 리포트 발송
            if now >= next_report:
                await _send_scheduled_reports(settings)
                next_report = _next_report_target()

            # 다음 틱까지 대기 (10초 주기)
            elapsed = (datetime.now(UTC) - tick_start).total_seconds()
            await asyncio.sleep(max(0.0, 10.0 - elapsed))

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("collect_scheduler: 종료")

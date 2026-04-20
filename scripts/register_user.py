"""유저 등록 스크립트 — 서버에서 직접 실행.

사용법:
    python scripts/register_user.py \
        --telegram-id 123456789 \
        --username "홍길동" \
        --api-key "빗썸_API_KEY" \
        --secret-key "빗썸_SECRET_KEY" \
        --watchlist "KRW-BTC,KRW-ETH"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 프로젝트 루트의 .env 로드
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))

from utils.crypto import encrypt  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="autocoin 유저 등록")
    parser.add_argument("--telegram-id", type=int, required=True)
    parser.add_argument("--username", type=str, required=True)
    parser.add_argument("--api-key", type=str, required=True)
    parser.add_argument("--secret-key", type=str, required=True)
    parser.add_argument("--watchlist", type=str, default="KRW-BTC")
    args = parser.parse_args()

    # .env에서 ENCRYPT_KEY 읽기
    env_file = ROOT / ".env"
    encrypt_key = ""
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ENCRYPT_KEY="):
                encrypt_key = line.split("=", 1)[1].strip().strip("'\"")
                break

    if not encrypt_key:
        print("ERROR: .env에서 ENCRYPT_KEY를 찾을 수 없습니다.")
        sys.exit(1)

    # 암호화
    enc_api = encrypt(args.api_key, encrypt_key)
    enc_secret = encrypt(args.secret_key, encrypt_key)

    # watchlist
    watchlist = [w.strip() for w in args.watchlist.split(",") if w.strip()]
    watchlist_json = json.dumps(watchlist)

    risk_settings = json.dumps({
        "max_per_trade_pct": 15.0,
        "max_daily_trades": 30,
        "min_krw_reserve": 100_000,
    })

    cols = (
        "telegram_id, username, bithumb_api_key,"
        " bithumb_secret_key, coin_watchlist, risk_settings, is_active"
    )
    vals = (
        f"{args.telegram_id}, '{args.username}',"
        f" '{enc_api}', '{enc_secret}',"
        f" '{watchlist_json}'::jsonb, '{risk_settings}'::jsonb, true"
    )
    sql = (
        f"INSERT INTO users ({cols}) VALUES ({vals})"
        " ON CONFLICT (telegram_id) DO UPDATE SET"
        " username = EXCLUDED.username,"
        " bithumb_api_key = EXCLUDED.bithumb_api_key,"
        " bithumb_secret_key = EXCLUDED.bithumb_secret_key,"
        " coin_watchlist = EXCLUDED.coin_watchlist,"
        " is_active = true;"
    )

    print("=" * 60)
    print("  유저 등록 SQL 생성 완료")
    print("=" * 60)
    print(f"  Telegram ID: {args.telegram_id}")
    print(f"  Username:    {args.username}")
    print(f"  Watchlist:   {watchlist}")
    print(f"  API Key:     {enc_api[:20]}...")
    print(f"  Secret Key:  {enc_secret[:20]}...")
    print()
    print("다음 명령어로 실행하세요:")
    print()
    print(f'docker compose exec ct-db psql -U autocoin -c "{sql}"')
    print()


if __name__ == "__main__":
    main()

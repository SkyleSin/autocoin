"""로컬 스모크: 공개 시세 조회 (키 불필요)."""

from __future__ import annotations

import argparse

from trader.bithumb.client import BithumbAPIError, BithumbClient


def main() -> None:
    p = argparse.ArgumentParser(description="autocoin — 빗썸 연동")
    p.add_argument(
        "--accounts",
        action="store_true",
        help="Private: /v1/accounts (`.env`에 API 키 필요)",
    )
    args = p.parse_args()

    with BithumbClient() as client:
        if args.accounts:
            try:
                rows = client.get_accounts()
            except BithumbAPIError as e:
                print(e)
                raise SystemExit(1) from e
            for row in rows[:20]:
                print(row)
            if len(rows) > 20:
                print(f"... 외 {len(rows) - 20}건")
            return

        markets = client.get_markets()
        tickers = client.get_ticker("KRW-BTC")
        print(f"마켓 수: {len(markets)}")
        if tickers:
            t = tickers[0]
            print(
                f"샘플 시세 {t.get('market')}: "
                f"현재가 {t.get('trade_price')} / 변동 {t.get('change')}",
            )


if __name__ == "__main__":
    main()

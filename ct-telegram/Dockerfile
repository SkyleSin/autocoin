# ct-telegram: 텔레그램 봇 수신/발송
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-telegram/src/telegram/ ./telegram/
COPY ct-telegram/src/entrypoints/ ./entrypoints/

CMD ["python", "-m", "entrypoints.telegram"]

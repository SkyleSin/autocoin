# ct-worker: 유저별 거래소 API 실행
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-worker/src/entrypoints/ ./entrypoints/

CMD ["python", "-m", "entrypoints.worker"]

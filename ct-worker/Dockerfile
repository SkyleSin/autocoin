# ct-worker: 빗썸 API 전담 (잔고조회/주문/체결확인)
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-worker/ ./

CMD ["python", "worker.py"]

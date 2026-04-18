# ct-scheduler: 수집 + 분석 + Safety + 비매매 Job
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-scheduler/src/entrypoints/ ./entrypoints/

CMD ["python", "-m", "entrypoints.scheduler"]

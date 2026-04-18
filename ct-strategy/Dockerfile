# ct-strategy: 유저별 전략 평가 + 시그널
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-strategy/src/entrypoints/ ./entrypoints/

CMD ["python", "-m", "entrypoints.strategy"]

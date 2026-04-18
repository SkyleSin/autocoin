# ct-api: FastAPI (DDL + 헬스체크)
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-api/src/ ./

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]

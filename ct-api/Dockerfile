# ct-api: FastAPI (DDL + 헬스체크)
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-api/src/app.py ./app.py
COPY ct-api/src/routers/ ./routers/
COPY ct-api/src/schemas/ ./schemas/

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]

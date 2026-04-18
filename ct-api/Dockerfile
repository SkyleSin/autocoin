# ct-api: FastAPI DB 게이트웨이
FROM autocoin-base:latest

COPY shared/ ./
COPY ct-api/ ./

COPY ct-api/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]

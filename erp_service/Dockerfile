# Standalone image for the real ERP + scheduling service (erp_service/). Build from the repo
# root so the build context includes this package:
#   docker build -f erp_service/Dockerfile -t erp-service .
FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn "sqlalchemy>=2.0"

COPY erp_service ./erp_service

# File-backed SQLite so state survives container restarts; mount /data as a volume to persist
# it across `docker compose up`/`down` cycles.
ENV ERP_DATABASE_URL=sqlite:////data/erp.sqlite

EXPOSE 8123

CMD ["uvicorn", "erp_service.api:app", "--host", "0.0.0.0", "--port", "8123"]

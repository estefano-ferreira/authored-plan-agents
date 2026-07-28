FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts

RUN pip install --no-cache-dir .

ENV AI_PROVIDER=gemini

# This is the "aici" (platform) image only. It intentionally does NOT contain erp_service/ --
# the ERP/scheduling business system is a separate container (see erp_service/Dockerfile) and the
# platform's only link to it is HTTP, via ERP_BASE_URL (RestConnector). The platform must not be
# able to read or write the ERP's storage directly.
ENTRYPOINT ["python", "scripts/run_plans.py"]
CMD ["--repeat", "1"]

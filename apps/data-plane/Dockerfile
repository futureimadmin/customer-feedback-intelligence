FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt google-cloud-pubsub>=2.21.0

COPY common.py ingest.py normalize.py features.py classify.py analytics.py agentic_rag.py pipeline.py stage_worker.py ./

ENV STAGE_WORKER_MODE=pubsub
CMD ["python", "stage_worker.py"]

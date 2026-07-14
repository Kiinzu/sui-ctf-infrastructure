# syntax=docker/dockerfile:1
#
# Orchestrator image: FastAPI RPC proxy + nc menu + reaper in one asyncio
# process. Build context is the REPO ROOT. It talks to the host Docker socket
# (mounted at runtime) to spawn sibling instance containers.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator/app ./app

# nc menu + rpc proxy
EXPOSE 1337 8080
CMD ["python", "-m", "app.main"]

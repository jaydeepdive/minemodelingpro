# MineModelingPro — the site, Studio's API and the modelling worker in one image.
# The same image runs two App Platform components: `web` (uvicorn) and `modeler`
# (studio/worker.py). See studio/DEPLOY-DO.md.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app \
    MMP_ENV=production

# mdbtools: Microsoft Access (.mdb/.accdb) drill databases
RUN apt-get update \
 && apt-get install -y --no-install-recommends mdbtools ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY studio/requirements.txt studio/requirements.txt
RUN pip install -r studio/requirements.txt

COPY . .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

CMD ["uvicorn", "studio.server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]

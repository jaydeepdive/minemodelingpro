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

# ONE worker, on purpose. Two of them cost 2.7x the memory for no concurrency a
# 1 vCPU box can actually deliver — measured: 57.7 MB resident with one worker,
# 153.6 MB with two, before a single request. That is the floor; the ceiling is
# what matters more. A request over a client's own drill data peaks around
# 510 MB (measured, test_data_api), and with two workers two such requests can
# land in different processes and exceed the container's 1 GB, which the kernel
# answers by killing one — indistinguishable from the outside from the app being
# down. Each worker also runs its own sitesync pull and poll, so the mirrored
# site was being downloaded and watched twice.
#
# FastAPI runs synchronous endpoints in a threadpool, so one worker still serves
# concurrent requests; what two buy is parallel CPU, which one vCPU does not have.
CMD ["uvicorn", "studio.server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

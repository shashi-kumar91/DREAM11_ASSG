# Two stages: build the browser UI, then run FastAPI + the built UI on one origin.
# Works on a free Docker host (Hugging Face Spaces / Render style): the port comes from
# $PORT, nothing needs a volume, and all configuration arrives as environment variables.

# ---- stage 1: the frontend --------------------------------------------- #
FROM node:24-alpine AS ui
WORKDIR /src
COPY . /src
RUN set -eux; \
    npm install -g npm@11 >/dev/null 2>&1 || true; \
    if [ -f package.json ]; then \
        (npm ci --no-audit --no-fund || npm install --no-audit --no-fund) && npm run build; \
    elif [ -f frontend/package.json ]; then \
        cd frontend && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund) \
            && npm run build; \
    else \
        echo "no package.json yet - the API will serve its placeholder page"; \
    fi; \
    mkdir -p frontend/dist; touch frontend/dist/.keep


# ---- stage 2: the agent server ------------------------------------------ #
FROM python:3.13-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    RUN_MODE=cached-demo \
    LLM_BACKEND=openrouter \
    ASK_TIMEOUT_S=90 \
    MAX_SESSIONS=12

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY backend/kernel      /app/backend/kernel
COPY backend/packs       /app/backend/packs
COPY backend/adapters    /app/backend/adapters
COPY backend/metering    /app/backend/metering
COPY backend/app         /app/backend/app
COPY backend/scripts     /app/backend/scripts
COPY backend/fixtures    /app/backend/fixtures
COPY backend/__init__.py /app/backend/__init__.py
COPY tests               /app/tests
COPY docs                /app/docs
COPY --from=ui /src/frontend/dist /app/frontend/dist

# Never baked into the image: keys arrive from the host environment (.env is dockerignored).
EXPOSE 7860

CMD uvicorn backend.app.server:app --host 0.0.0.0 --port ${PORT:-7860}

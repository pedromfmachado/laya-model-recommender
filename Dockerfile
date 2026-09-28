FROM ghcr.io/astral-sh/uv:0.10.9 AS uv

FROM ghcr.io/berriai/litellm:v1.101.2

WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
RUN uv pip install --python /app/.venv/bin/python --no-cache laya==0.3.20

COPY config/litellm.yaml /app/config/litellm.yaml
COPY laya_router /app/laya_router

FROM ghcr.io/astral-sh/uv:0.10.9 AS uv

FROM ghcr.io/berriai/litellm:v1.101.2

WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
RUN uv pip install --python /app/.venv/bin/python --no-cache laya==0.3.20

COPY config.yaml /app/config.yaml
COPY custom_classifier.py /app/custom_classifier.py

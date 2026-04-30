FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY hf_space.py ./
COPY api ./api
COPY src ./src
COPY sql ./sql

RUN pip install --upgrade pip && pip install .[mcp]

EXPOSE 7860

CMD ["uvicorn", "hf_space:app", "--host", "0.0.0.0", "--port", "7860"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev gcc && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY solomon ./solomon
RUN pip install .

EXPOSE 8080
CMD ["uvicorn", "solomon.api:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.11-slim AS common
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    DATA_DIR=/data \
    DB_PATH=/data/finevpn.db
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data && useradd --create-home --uid 10001 finevpn && chown -R finevpn:finevpn /app /data
USER finevpn

FROM common AS api
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

FROM common AS bot
CMD ["python", "bot.py"]

FROM common AS main-panel
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

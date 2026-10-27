FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    DATA_DIR=/app/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN mkdir -p /app/data && chmod 700 /app/data

VOLUME ["/app/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:5000/auth/login', timeout=3)" || exit 1

CMD ["gunicorn", "--worker-class", "gthread", "--workers", "1", "--threads", "32", \
     "--timeout", "0", "--keep-alive", "65", "--bind", "0.0.0.0:5000", "wsgi:app"]

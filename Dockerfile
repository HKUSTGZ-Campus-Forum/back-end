ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm@sha256:4766d88374a2dcd8acb278cb4e5db9937c48f90c6179d1921f7b376fe96e3a99
FROM ${PYTHON_BASE_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv

COPY build-requirements.lock requirements.lock ./
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r build-requirements.lock \
    && /opt/venv/bin/pip install --require-hashes --no-build-isolation -r requirements.lock \
    && /opt/venv/bin/pip uninstall --yes setuptools wheel \
    && /opt/venv/bin/pip check


FROM ${PYTHON_BASE_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=wsgi:application

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY migrations ./migrations
COPY wsgi.py ./wsgi.py

USER 10001:10001

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import http.client; connection = http.client.HTTPConnection('127.0.0.1', 5000, timeout=2); connection.request('GET', '/healthz'); raise SystemExit(0 if connection.getresponse().status == 200 else 1)"]

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "wsgi:application"]

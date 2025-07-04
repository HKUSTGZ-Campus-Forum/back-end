FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir gunicorn eventlet

COPY . .

ENV FLASK_APP=run.py
ENV FLASK_ENV=production
ENV DATABASE_URL=postgres://postgres:postgres@host.docker.internal:5432/app

EXPOSE 5000

# Use eventlet worker for WebSocket support and single worker to ensure WebSocket compatibility
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "eventlet", "--workers", "1", "--timeout", "60", "wsgi:application"]

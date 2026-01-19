# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ ./app/

# Create data directory for database
RUN mkdir -p /app/data

# Environment variables
ENV DATABASE_PATH=/app/data/url_monitor.db
ENV SECRET_KEY=change-this-to-random-secret-key-in-production

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


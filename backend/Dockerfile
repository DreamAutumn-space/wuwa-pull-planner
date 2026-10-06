FROM python:3.12-slim

WORKDIR /app

COPY core /app/core
COPY backend /app/backend
COPY data /app/data
COPY requirements-lock.txt /app/requirements-lock.txt
RUN pip install --no-cache-dir -c /app/requirements-lock.txt /app/core /app/backend

ENV WUWA_DATA_DIR=/app/data
ENV WUWA_CORS_ORIGINS=
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

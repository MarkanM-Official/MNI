FROM python:3.11-slim

WORKDIR /app

ENV FLASK_DEBUG=False \
    MARKANM_DISABLE_BACKGROUND_WORKERS=true \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["gunicorn", "backend.app:app", "--bind", "0.0.0.0:5000", "--workers", "2"]

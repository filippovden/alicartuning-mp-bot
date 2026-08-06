FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-images.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Удаление фона (rembg/onnxruntime) — тяжёлая опциональная зависимость, раскомментируйте
# при необходимости обработки фото (раздел 11 ТЗ, V2):
# RUN pip install --no-cache-dir -r requirements-images.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

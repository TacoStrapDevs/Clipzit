FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-whisper.txt requirements-vision.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-whisper.txt -r requirements-vision.txt
COPY clipzit ./clipzit
ENV CLIPZIT_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "clipzit.main:app", "--host", "0.0.0.0", "--port", "8000"]

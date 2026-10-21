FROM python:3.12-slim-bookworm

# espeak-ng and ffmpeg speak the bot's notice; nothing else needs them.
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.34" "python-multipart>=0.0.20"

WORKDIR /app
COPY mocks/__init__.py ./mocks/
COPY mocks/elevenlabs ./mocks/elevenlabs
USER nobody
EXPOSE 11437
CMD ["uvicorn", "mocks.elevenlabs.server:app", "--host", "0.0.0.0", "--port", "11437"]

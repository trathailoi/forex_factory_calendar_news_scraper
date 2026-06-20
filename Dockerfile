FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV FF_CHROME_BIN=/usr/bin/chromium
ENV FF_CHROMEDRIVER_PATH=/usr/bin/chromedriver

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Strip any CRLF (Windows Git checkout) so the shebang resolves to `bash`, not `bash\r`.
RUN find scripts docker -name '*.sh' -exec sed -i 's/\r$//' {} + \
 && chmod +x scripts/setup_env.sh docker/entrypoint.sh

ENTRYPOINT ["./docker/entrypoint.sh"]
CMD []

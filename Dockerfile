# Syncee product-research pipeline + review dashboard.
# One image, two roles: the dashboard (default CMD) and the CLI scanner
# (`docker compose run --rm scanner ...`). Playwright/Chromium is baked in so
# live Syncee scans work on a headless VPS.
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (cached across code changes).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

# Config + package data.
COPY config ./config

# Guarantee the Chromium build matches whatever Playwright version pip resolved
# (the base image ships one, but pip may pull a newer wheel). Idempotent.
RUN playwright install chromium

EXPOSE 8000

# Default role: the review dashboard. Compose overrides this for the scanner.
CMD ["uvicorn", "syncee_scanner.dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]

# TerraFroma / Vanguard Tavern. One image for the game and its tests.
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 terraforma
WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml README.md ./
COPY src ./src

# --- the game -------------------------------------------------------------
FROM base AS app
RUN pip install ".[all-databases]"
COPY docker/start.sh /usr/local/bin/start-terraforma
USER terraforma
# settings.toml (and keys/) are mounted into /app: no env vars needed.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]
CMD ["start-terraforma"]

# --- the tests ------------------------------------------------------------
FROM base AS test
RUN pip install -e ".[dev]"
COPY tests ./tests
COPY utils ./utils
# The tests run as terraforma and need to write pytest's cache in /app.
RUN chown terraforma:terraforma /app
USER terraforma
CMD ["pytest"]

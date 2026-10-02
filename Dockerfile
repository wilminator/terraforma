# The TerraForma engine: a development image (the engine on its own) and its tests.
# A game builds its own image: pip install the engine, then its package (see the example game).
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 terraforma
WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml README.md LICENSE LICENSE-EXCEPTION.md ./
COPY src ./src

# --- the engine, for development -------------------------------------------------------------
FROM base AS app
RUN pip install ".[all-databases]"
USER terraforma
# settings.toml (and keys/) are mounted into /app: no env vars needed.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]
# Checks settings, migrates, then serves (python -m terraforma serve).
CMD ["python", "-m", "terraforma", "serve"]

# --- the tests ------------------------------------------------------------
FROM base AS test
RUN pip install -e ".[dev]"
COPY settings.example.toml ./
COPY tests ./tests
# The tests run as terraforma and need to write pytest's cache in /app.
RUN chown terraforma:terraforma /app
USER terraforma
# One worker per CPU, each with its own databases (terraforma.testing).
ENTRYPOINT ["pytest"]
CMD ["-n", "auto"]

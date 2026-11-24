# Multi-stage build (plain Dockerfile, no BuildKit features needed):
# stage "deps" installs the bundled wheels, the final image only receives the
# installed site-packages – the wheels themselves are not part of the image.
# Base image pinned by version and digest (multi-arch index: aarch64 + amd64).
# Check for a newer image on every revision of the app.
FROM python:3.12.16-alpine3.24@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89 AS deps
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Dependencies are installed offline from the bundled wheels, verified by hash
COPY requirements.txt /tmp/requirements.txt
COPY wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels --require-hashes \
        --target /deps -r /tmp/requirements.txt

# Same pinned base image for the runtime
FROM python:3.12.16-alpine3.24@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/deps
# No package tooling at runtime: pip/setuptools/wheel are not used by the app and
# only add attack surface. The pinned base image itself stays unchanged.
RUN rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
        /usr/local/lib/python3.12/ensurepip \
        /usr/local/lib/python3.12/site-packages/pip /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.12/site-packages/setuptools /usr/local/lib/python3.12/site-packages/setuptools-*.dist-info \
        /usr/local/lib/python3.12/site-packages/pkg_resources /usr/local/lib/python3.12/site-packages/_distutils_hack \
        /usr/local/lib/python3.12/site-packages/distutils-precedence.pth \
        /usr/local/lib/python3.12/site-packages/wheel /usr/local/lib/python3.12/site-packages/wheel-*.dist-info
COPY --from=deps /deps /deps
COPY app /app
# Bytecode of the packages and the app is compiled once here, so a start does not compile
# them again (nothing is written at runtime: PYTHONDONTWRITEBYTECODE). A file that fails
# to compile fails the build. The base image ships the standard library without bytecode:
# importing the entry point once (bytecode writing switched on for this command only)
# writes it for exactly the modules a start loads, which saves most of the start time.
RUN python3 -m compileall -q /deps /app \
    && cd /app && PYTHONDONTWRITEBYTECODE= python3 -c "import main"
WORKDIR /app
# Container health: with the Watchdog switch of the app page on, the Supervisor
# restarts the app if the download server stops accepting connections. TCP check
# only - nothing is requested or logged. During the start period the check runs every
# second, so the container counts as healthy right after the start (Docker 25 and
# later; older versions keep the 30 s interval).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=1s --retries=3 \
    CMD ["python3", "-c", "import socket; socket.create_connection(('127.0.0.1', 8098), 3).close()"]
CMD ["python3", "/app/main.py"]

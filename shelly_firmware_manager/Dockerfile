# Multi-stage build (plain Dockerfile, no BuildKit features needed):
# stage "deps" installs the bundled wheels, the final image only receives the
# installed site-packages – the wheels themselves are not part of the image.
# Base image pinned by version and digest (multi-arch index: aarch64 + amd64).
# Check for a newer image on every revision of the app.
FROM python:3.12.16-alpine3.24@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7 AS deps
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Dependencies are installed offline from the bundled wheels, verified by hash
COPY requirements.txt /tmp/requirements.txt
COPY wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels --require-hashes \
        --target /deps -r /tmp/requirements.txt

# Same pinned base image for the runtime
FROM python:3.12.16-alpine3.24@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/deps
# No package tooling at runtime (rc6): pip/setuptools/wheel are not used by the app and
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
WORKDIR /app
CMD ["python3", "/app/main.py"]

FROM n8nio/n8n:latest

USER root

# Install apk package manager (auto-detects CPU architecture) and Python.
# n8n runs no Code nodes — all intelligence lives in services/extraction.
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then APK_ARCH="x86_64"; else APK_ARCH="aarch64"; fi && \
    wget -qO /sbin/apk "https://gitlab.alpinelinux.org/api/v4/projects/5/packages/generic/v2.14.4/${APK_ARCH}/apk.static" && \
    chmod +x /sbin/apk && \
    /sbin/apk -X http://dl-cdn.alpinelinux.org/alpine/latest-stable/main -U --allow-untrusted --initdb add apk-tools && \
    apk update && apk add --no-cache python3 py3-pip

USER node

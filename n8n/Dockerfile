FROM n8nio/n8n:latest

USER root

# Manually install the apk package manager (auto-detects CPU architecture)
# Kept around for any lightweight Python utilities used inside Code nodes.
# Heavy lifting (scraping, AI calls, parsing) now lives in services/extraction.
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then APK_ARCH="x86_64"; else APK_ARCH="aarch64"; fi && \
    wget -qO /sbin/apk "https://gitlab.alpinelinux.org/api/v4/projects/5/packages/generic/v2.14.4/${APK_ARCH}/apk.static" && \
    chmod +x /sbin/apk && \
    /sbin/apk -X http://dl-cdn.alpinelinux.org/alpine/latest-stable/main -U --allow-untrusted --initdb add apk-tools && \
    apk update && apk add --no-cache python3 py3-pip

USER node

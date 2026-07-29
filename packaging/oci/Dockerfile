FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ARG DEEPLAW_VERSION
ARG VCS_REF
ARG SOURCE_DATE_EPOCH

LABEL org.opencontainers.image.title="DeepLaw" \
      org.opencontainers.image.description="Local single-user Agent Knowledge OS" \
      org.opencontainers.image.version="${DEEPLAW_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/Eysn0130/DeepLaw" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.created="${SOURCE_DATE_EPOCH}"

ENV HOME=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY runtime-requirements.txt /tmp/runtime-requirements.txt
COPY deeplaw.whl /tmp/deeplaw.whl

RUN python -m pip install --disable-pip-version-check --no-cache-dir \
      --require-hashes --requirement /tmp/runtime-requirements.txt \
    && python -m pip install --disable-pip-version-check --no-cache-dir \
      --no-deps /tmp/deeplaw.whl \
    && python -m pip check \
    && rm /tmp/runtime-requirements.txt /tmp/deeplaw.whl \
    && install -d -m 0700 -o 65532 -g 65532 /data

WORKDIR /data
USER 65532:65532

ENTRYPOINT ["deeplaw"]
CMD ["--version"]

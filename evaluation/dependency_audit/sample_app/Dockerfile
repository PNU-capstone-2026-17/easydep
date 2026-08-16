FROM python:3.13-slim-bookworm

WORKDIR /opt/easydep-sample
COPY service.py ./service.py

RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home easydep \
    && mkdir -p /data \
    && chown -R easydep:easydep /opt/easydep-sample /data

USER 10001
EXPOSE 8080 8081
ENTRYPOINT ["python", "/opt/easydep-sample/service.py"]

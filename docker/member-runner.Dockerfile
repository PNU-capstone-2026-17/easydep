FROM eclipse-temurin:21-jdk-jammy AS jdk
FROM node:20-bookworm-slim AS node
FROM python:3.13-slim-bookworm

WORKDIR /easydep-build
COPY docker/member-runner-requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:${PATH}"
ENV PYTHONUTF8=1
# The workspace is a Windows bind mount in Docker Desktop. Gradle's metadata
# and classpath snapshotting perform many small file operations, which are
# disproportionately slow on that mount. Keep the cache in the Linux runner
# filesystem for the lifetime of one member workflow instead.
ENV GRADLE_USER_HOME=/tmp/easydep-gradle-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/easydep \
    && curl --fail --location --retry 3 \
      https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/7.24.0/openapi-generator-cli-7.24.0.jar \
      --output /opt/easydep/openapi-generator-7.24.0.jar \
    && printf '%s  %s\n' \
      4b83ccc6fd43056c8c631cd0195e5100bd0550912502527bab09ac76152dab0c \
      /opt/easydep/openapi-generator-7.24.0.jar | sha256sum --check --status \
    && curl --fail --location --retry 3 \
      https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/7.14.0/openapi-generator-cli-7.14.0.jar \
      --output /opt/easydep/openapi-generator-7.14.0.jar \
    && printf '%s  %s\n' \
      e03186835022ca02da4aa95e3967b6a3b6d44c2e5f7606e6d5c22466f519c757 \
      /opt/easydep/openapi-generator-7.14.0.jar | sha256sum --check --status

RUN printf '%s\n' '#!/bin/sh' \
      'exec python -B -m app.implementation.runtime.runner_docker_shim "$@"' \
      > /usr/local/bin/docker \
    && chmod 0755 /usr/local/bin/docker

WORKDIR /easydep-workspace
ENTRYPOINT ["python", "-B", "-m", "app.implementation.runtime.member_linux_runner"]

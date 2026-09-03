FROM plantuml/plantuml@sha256:47870c1f76cfb3747bc7090bfe83013a4e3105b5a0bb1515e2baf5d3e2b3ee9d AS plantuml-runtime
FROM ghcr.io/astral-sh/uv:0.8.22 AS uv-runtime

FROM eclipse-temurin:21-jdk-jammy AS jdk
FROM eclipse-temurin:21-jre-jammy AS jre-runtime

# API, 구현 작업, Testing 작업이 모두 같은 도구 버전을 사용한다. 각 도구를 실행할 때마다
# 별도 이미지를 받지 않고 이 단계에서 실행 파일만 공용 이미지로 복사한다.
FROM gradle:8.14.2-jdk21 AS gradle-runtime
FROM ghcr.io/opentofu/opentofu:1.12.6-minimal AS opentofu-runtime
# 생성하는 IaC가 사용하는 세 provider를 이미지 빌드 때 한 번 받는다. Testing이 실행될
# 때마다 registry에 접속하지 않으며, 아래 버전은 ResourcePlan renderer의 고정 버전과 같다.
FROM alpine:3.22 AS opentofu-providers
RUN apk add --no-cache ca-certificates
COPY --from=opentofu-runtime /usr/local/bin/tofu /usr/local/bin/tofu
WORKDIR /provider-bootstrap
COPY toolchain/opentofu/providers.tf ./providers.tf
# OpenTofu Registry의 hashicorp provider는 같은 버전의 upstream 태그를 사용하지만 패키지를
# GitHub release asset에서 배포한다. 해당 CDN이 제한된 환경에서도 빌드할 수 있도록 접근
# 가능한 HashiCorp 공식 Registry에서 같은 소스 태그의 서명된 패키지를 받은 뒤, 최종
# filesystem mirror 주소만 생성 IaC의 기본 주소(registry.opentofu.org)에 맞춘다.
RUN TF_REGISTRY_DISCOVERY_RETRY=5 \
    TF_REGISTRY_CLIENT_TIMEOUT=60 \
    TF_PROVIDER_DOWNLOAD_RETRY=5 \
    sed -i \
      's#source  = "hashicorp/#source  = "registry.terraform.io/hashicorp/#' \
      providers.tf \
    && tofu providers mirror -platform=linux_amd64 /upstream-provider-mirror \
    && mkdir -p /provider-mirror/registry.opentofu.org \
    && mv \
      /upstream-provider-mirror/registry.terraform.io/hashicorp \
      /provider-mirror/registry.opentofu.org/hashicorp

FROM aquasec/trivy:0.74.0 AS trivy-runtime
FROM docker:27.5.1-cli AS docker-runtime
FROM node:22-bookworm-slim AS node-runtime
# 생성한 PowerShell 배포 script도 Linux 툴체인 안에서 같은 parser로 검사한다. SDK 전체는
# 최종 이미지에 넣지 않고 PowerShell runtime만 복사한다.
FROM mcr.microsoft.com/powershell:7.4-debian-12 AS powershell-runtime

# 런타임과 같은 Node/npm을 사용한다. 별도 Alpine 이미지를 쓰면 도구 버전이 달라지고
# npm 자체 오류가 나도 로컬 실행 환경과 비교하기 어렵다.
FROM node-runtime AS frontend-build
WORKDIR /src
ARG NPM_REGISTRY=https://registry.npmjs.org
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --include=dev --no-audit --no-fund \
      --registry="${NPM_REGISTRY}" --replace-registry-host=always \
    && test -x node_modules/.bin/vite
COPY frontend ./
RUN npm run build

# BERT FR/NFR 검증 가중치를 되살리는 단계. 저장소에는 45MiB 조각으로 쪼개 들어 있어서
# (GitHub 파일당 100MiB 한도) 한 번 이어 붙여야 한다.
# 별도 stage에서 하고 결과만 가져와야 조각과 완성본이 이미지에 함께 남지 않는다.
# 런타임에는 이미 준비된 상태라 파드 기동에 재조립 비용이 없다.
FROM python:3.13-slim-bookworm AS weights
WORKDIR /build
COPY materials/BERT_FR_NFR_Classifier/bert_model ./materials/BERT_FR_NFR_Classifier/bert_model
COPY app/requirements/model_assets.py ./app/requirements/model_assets.py
RUN python app/requirements/model_assets.py --dest /opt/bert_model

FROM python:3.13-slim-bookworm AS python-common-dependencies

# API와 runner가 함께 import하는 Python 패키지는 이 단계에서 한 번 설치한다. BERT와
# 브라우저는 사용하는 대상에만 추가해 구현 툴체인이 큰 실행 파일을 떠안지 않게 한다.
COPY --from=uv-runtime /uv /usr/local/bin/uv
COPY requirements-common.txt /tmp/easydep-requirements-common.txt
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
      --requirements /tmp/easydep-requirements-common.txt

# 요구사항 분류기가 필요한 runtime만 BERT Python 패키지를 가진다. 공통 layer 위에
# 추가하므로 runtime과 toolchain이 나뉘어도 나머지 패키지는 Docker가 한 번만 저장한다.
FROM python-common-dependencies AS python-runtime-dependencies
COPY requirements-bert.txt /tmp/easydep-requirements-bert.txt
# PyPI와 PyTorch CPU 저장소가 함께 선언돼 있다. uv의 기본 first-index 정책을 쓰면
# PyTorch 저장소에 우연히 있는 일반 패키지만 볼 수 있어 두 공식 저장소를 함께 탐색한다.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --index-strategy unsafe-best-match \
      --requirements /tmp/easydep-requirements-bert.txt


# 구현과 Testing이 공유하는 고정 Linux 환경의 공통 부분이다. 아래 최종 toolchain이
# 브라우저 검사 도구만 더한다. 중간 단계에는 별도 tag를 만들지 않아 사용자가 어느
# 툴체인을 골라야 하는지 고민하거나 사용하지 않는 image를 함께 보관하지 않게 한다.
FROM python-common-dependencies AS toolchain-core

COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
COPY --from=gradle-runtime /opt/gradle /opt/gradle
COPY --from=opentofu-runtime /usr/local/bin/tofu /usr/local/bin/tofu
COPY --from=opentofu-providers /provider-mirror /opt/easydep/provider-mirror
COPY toolchain/opentofu/tofurc /opt/easydep/tofurc
COPY --from=trivy-runtime /usr/local/bin/trivy /usr/local/bin/trivy
COPY --from=docker-runtime /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-runtime /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=powershell-runtime /opt/microsoft/powershell /opt/microsoft/powershell
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:/opt/gradle/bin:${PATH}"
ENV GRADLE_USER_HOME=/app/.gradle-cache
ENV POWERSHELL_TELEMETRY_OPTOUT=1
ENV TF_CLI_CONFIG_FILE=/opt/easydep/tofurc

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cloud-init curl libicu72 ripgrep shellcheck \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s /opt/microsoft/powershell/7/pwsh /usr/local/bin/pwsh

# OpenAPI Generator도 툴체인 안에서 직접 실행한다. 아직 운영 checkpoint가 없으므로
# 현재 생성 경로가 사용하는 7.24.0만 보관한다.
RUN mkdir -p /opt/easydep \
    && curl --fail --location --retry 3 \
      https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/7.24.0/openapi-generator-cli-7.24.0.jar \
      --output /opt/easydep/openapi-generator-7.24.0.jar \
    && printf '%s  %s\n' \
      4b83ccc6fd43056c8c631cd0195e5100bd0550912502527bab09ac76152dab0c \
      /opt/easydep/openapi-generator-7.24.0.jar | sha256sum --check --status

COPY scripts/bootstrap-implementation-tools.sh ./scripts/bootstrap-implementation-tools.sh
RUN sh ./scripts/bootstrap-implementation-tools.sh

# 비루트 사용자로 실행
RUN useradd -m appuser
RUN mkdir -p /app/.easydep /app/.gradle-cache /app/.cache/opentofu /tmp/easydep-gradle-cache \
    && chown -R appuser:appuser \
      /app/.easydep /app/.gradle-cache /app/.cache /tmp/easydep-gradle-cache

USER appuser
ENV LITELLM_LOCAL_MODEL_COST_MAP=true
ENV OPENHANDS_SUPPRESS_BANNER=1
CMD ["python", "--version"]


# 구현과 Testing이 함께 사용하는 유일한 툴체인이다. 구현 작업은 Java·Node·IaC 도구만
# 실행하고, Testing의 DOM·JavaScript E2E만 아래 Playwright를 사용한다. 실행 진입점으로
# 역할을 나누므로 도구 집합이 같은 image를 별도 이름으로 두 개 만들 필요가 없다.
# 화면 이미지 비교용 전체 Chromium 대신 headless shell만 설치한다.
FROM toolchain-core AS toolchain

USER root
COPY requirements-browser-testing.txt /tmp/easydep-requirements-browser-testing.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
      --requirements /tmp/easydep-requirements-browser-testing.txt
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN python -m playwright install --with-deps --only-shell chromium \
    && chmod -R a+rX /opt/ms-playwright \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/bootstrap-testing-tools.sh ./scripts/bootstrap-testing-tools.sh
RUN sh ./scripts/bootstrap-testing-tools.sh
USER appuser
CMD ["python", "--version"]


# 원격 배포에서 FastAPI와 프런트엔드를 제공하는 대상이다. 빌드·테스트 도구는 위의
# toolchain image로 실행하므로 여기에는 PlantUML용 JRE와 Docker CLI만 추가한다.
FROM python-runtime-dependencies AS runtime

COPY --from=jre-runtime /opt/java/openjdk /opt/java/openjdk
COPY --from=docker-runtime /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-runtime /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:${PATH}"
ENV LITELLM_LOCAL_MODEL_COST_MAP=true
ENV OPENHANDS_SUPPRESS_BANNER=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz fonts-dejavu-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY --from=plantuml-runtime /opt/plantuml.jar /opt/plantuml/plantuml.jar
ENV PLANTUML_JAR=/opt/plantuml/plantuml.jar

COPY app ./app
COPY server.py ./server.py
COPY --from=frontend-build /src/build ./frontend/build

EXPOSE 8000
RUN useradd -m appuser \
    && mkdir -p /app/.easydep \
    && chown -R appuser:appuser /app/.easydep

# runtime만 BERT를 소유한다. toolchain preflight에서는 요구하지 않는다.
ENV BERT_MODEL_CACHE_DIR=/app/.easydep/models/bert_fr_nfr
COPY --from=weights /opt/bert_model /app/.easydep/models/bert_fr_nfr

USER appuser

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

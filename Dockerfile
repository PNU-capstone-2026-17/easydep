FROM plantuml/plantuml@sha256:47870c1f76cfb3747bc7090bfe83013a4e3105b5a0bb1515e2baf5d3e2b3ee9d AS plantuml-runtime

FROM eclipse-temurin:21-jdk-jammy AS jdk

# API, 구현 작업, Testing 작업이 모두 같은 도구 버전을 사용한다. 각 도구를 실행할 때마다
# 별도 이미지를 받지 않고 이 단계에서 실행 파일만 공용 이미지로 복사한다.
FROM gradle:8.14.2-jdk21 AS gradle-runtime
FROM ghcr.io/opentofu/opentofu:1.12.6-minimal AS opentofu-runtime
FROM aquasec/trivy:0.74.0 AS trivy-runtime
FROM docker:27.5.1-cli AS docker-runtime
FROM node:22-bookworm-slim AS node-runtime

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

FROM python:3.13-slim-bookworm

COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
COPY --from=gradle-runtime /opt/gradle /opt/gradle
COPY --from=opentofu-runtime /usr/local/bin/tofu /usr/local/bin/tofu
COPY --from=trivy-runtime /usr/local/bin/trivy /usr/local/bin/trivy
COPY --from=docker-runtime /usr/local/bin/docker /usr/local/bin/docker
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:/opt/gradle/bin:${PATH}"
ENV GRADLE_USER_HOME=/app/.gradle-cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl graphviz fonts-dejavu-core fonts-noto-cjk ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# API process와 수명이 같은 PicoWeb renderer를 띄운다. 이전처럼 이미지 요청마다 Docker
# container를 새로 만들지 않으며, 위의 Java와 Graphviz/font package가 이 JAR를 실행한다.
COPY --from=plantuml-runtime /opt/plantuml.jar /opt/plantuml/plantuml.jar
ENV PLANTUML_JAR=/opt/plantuml/plantuml.jar

# OpenAPI Generator도 툴체인 안에서 직접 실행한다. 이전 버전 입력을 재개할 때만 7.14.0을
# 사용하며, 새 구현은 7.24.0으로 고정한다. 내려받은 파일은 공개 SHA-256으로 확인한다.
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

# 의존성 먼저 설치해 레이어 캐시 활용 (torch CPU 휠 포함)
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# 애플리케이션 코드 복사 (server.py가 두 에이전트를 함께 서빙하고, frontend/는 설계 UI)
COPY app ./app
COPY server.py ./server.py
COPY --from=frontend-build /src/build ./frontend/build
COPY scripts/bootstrap-implementation-tools.sh ./scripts/bootstrap-implementation-tools.sh
RUN sh ./scripts/bootstrap-implementation-tools.sh

EXPOSE 8000

# 비루트 사용자로 실행
RUN useradd -m appuser
RUN mkdir -p /app/.easydep /app/.gradle-cache /app/.cache/opentofu /tmp/easydep-gradle-cache \
    && chown -R appuser:appuser \
      /app/.easydep /app/.gradle-cache /app/.cache /tmp/easydep-gradle-cache

# weights stage에서 되살린 BERT 체크포인트(약 +417MB). chown -R 뒤에 복사해야
# 417MB가 통째로 한 레이어 더 쌓이지 않는다. 읽기 전용이라 root 소유로 둬도 된다.
# 경량 이미지가 필요하면 아래 두 줄을 지우고 ENABLE_BERT_VERIFY=false 로 배포하면
# 이미 FR/NFR로 분류된 체크포인트만 실행할 수 있다.
ENV BERT_MODEL_CACHE_DIR=/app/.easydep/models/bert_fr_nfr
COPY --from=weights /opt/bert_model /app/.easydep/models/bert_fr_nfr

USER appuser

# OpenHands가 시작될 때 LiteLLM 가격표를 받느라 네트워크 제한 시간까지 기다리지 않는다.
# 패키지에 함께 들어 있는 같은 형식의 가격표를 바로 사용하고 작업 로그의 배너도 숨긴다.
ENV LITELLM_LOCAL_MODEL_COST_MAP=true
ENV OPENHANDS_SUPPRESS_BANNER=1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

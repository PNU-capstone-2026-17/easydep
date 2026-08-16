FROM eclipse-temurin:21-jdk-jammy AS jdk

FROM node:22-alpine AS frontend-build
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
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
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="${JAVA_HOME}/bin:${PATH}"
ENV GRADLE_USER_HOME=/app/.gradle-cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치해 레이어 캐시 활용 (torch CPU 휠 포함)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사 (server.py가 두 에이전트를 함께 서빙하고, frontend/는 설계 UI)
COPY app ./app
COPY server.py ./server.py
COPY --from=frontend-build /src/build ./frontend/build
COPY scripts/bootstrap-implementation-tools.sh ./scripts/bootstrap-implementation-tools.sh
RUN sh ./scripts/bootstrap-implementation-tools.sh

EXPOSE 8000

# 비루트 사용자로 실행
RUN useradd -m appuser
RUN mkdir -p /app/.easydep /app/.gradle-cache \
    && chown -R appuser:appuser /app/.easydep /app/.gradle-cache

# weights stage에서 되살린 BERT 체크포인트(약 +417MB). chown -R 뒤에 복사해야
# 417MB가 통째로 한 레이어 더 쌓이지 않는다. 읽기 전용이라 root 소유로 둬도 된다.
# 경량 이미지가 필요하면 아래 두 줄을 지우고 ENABLE_BERT_VERIFY=false 로 배포하면
# LLM 분류만으로 동작한다.
ENV BERT_MODEL_CACHE_DIR=/app/.easydep/models/bert_fr_nfr
COPY --from=weights /opt/bert_model /app/.easydep/models/bert_fr_nfr

USER appuser

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

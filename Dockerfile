FROM eclipse-temurin:21-jdk-jammy AS jdk

FROM python:3.12-slim-bookworm

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
COPY frontend ./frontend
COPY scripts/bootstrap-implementation-tools.sh ./scripts/bootstrap-implementation-tools.sh
RUN sh ./scripts/bootstrap-implementation-tools.sh

# BERT FR/NFR 검증 모델(파인튜닝 체크포인트) 복사.
# 이미지가 커진다(약 +417MB). 경량 이미지가 필요하면 이 COPY를 제거하고
# ENABLE_BERT_VERIFY=false 로 배포하면 LLM 분류만으로 동작한다.
COPY materials/BERT_FR_NFR_Classifier/bert_model ./materials/BERT_FR_NFR_Classifier/bert_model

EXPOSE 8000

# 비루트 사용자로 실행
RUN useradd -m appuser
RUN mkdir -p /app/.easydep /app/.gradle-cache \
    && chown -R appuser:appuser /app/.easydep /app/.gradle-cache
USER appuser

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

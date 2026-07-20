FROM python:3.13-slim

WORKDIR /app

# 의존성 먼저 설치해 레이어 캐시 활용 (torch CPU 휠 포함)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY app ./app

# BERT FR/NFR 검증 모델(파인튜닝 체크포인트) 복사.
# 이미지가 커진다(약 +417MB). 경량 이미지가 필요하면 이 COPY를 제거하고
# ENABLE_BERT_VERIFY=false 로 배포하면 LLM 분류만으로 동작한다.
COPY materials/BERT_FR_NFR_Classifier/bert_model ./materials/BERT_FR_NFR_Classifier/bert_model

EXPOSE 8000

# 비루트 사용자로 실행
RUN useradd -m appuser
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# 코드베이스 정리 기록

## 정리 원칙

원시 실험 결과, 실행 manifest, 오케스트레이션 체크포인트, 고정 provider 캐시는 보존한다. 빌드 산출물과 재생 가능한 조립 캐시는 제거하고, 역사 실험 스크립트도 정적 검사에서 제외하지 않는다.

## 용량 정리

`.easydep`은 약 1.88GB에서 1.20GB로 줄어 약 675MB를 회수했다.

- 제거: shard에서 재조립 가능한 BERT 모델 캐시 약 418MB
- 제거: 과거 implementation run 안의 Gradle wrapper/dependency cache와 build 산출물 약 256MB
- 제거: pytest·mypy·Ruff 임시 캐시
- 보존: 고정 provider plugin cache 약 1.03GB
- 보존: 동일 run 부분 재개에 필요한 orchestration checkpoint 약 89MB
- 보존: implementation run의 소스·feedback·manifest

Docker에는 EasyDep 소유가 아닌 minikube·bgutil 중지 컨테이너와 minikube 볼륨이 있어 제거하지 않았다. 이번 P2 검사에서 만든 컨테이너·볼륨·이미지 태그는 모두 제거했다. 공유 BuildKit 캐시는 소유 경계를 확인할 수 없어 전체 prune하지 않았다.

## 코드 정리

전체 Ruff 범위에 남아 있던 import 정렬, UTC 별칭, 불필요 import를 안전 자동 수정했다. 재현용 CSP 실험 스크립트와 재측정기에서 `subprocess.run(check=False)`를 명시해 반환 코드를 자료로 판정한다는 기존 의미를 코드에 드러냈다. 구조화 condition의 잘못된 타입은 `TypeError`로 구분했다.

이 변경은 실험 결과 JSON이나 cloud 리소스를 수정하지 않는다.

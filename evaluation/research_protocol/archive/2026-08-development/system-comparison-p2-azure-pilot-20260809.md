# P2-Azure 시스템 비교 개발 파일럿

## 목적과 범위

이 파일럿은 저장되어 있던 P2-Azure 산출물을 다시 분류해 다음 본 실험의 실행 가능성을 확인한다. 새 LLM 호출이나 클라우드 리소스 생성은 하지 않았다. P2는 연구 모델의 근거가 아니라 종단 통합 벤치마크이며, 이 한 사례로 의존성 지식이나 멀티 에이전트 구조의 인과 효과를 주장하지 않는다.

세 최초 실행은 `P2-azure`, `openai/gpt-oss-120b`, temperature 0, seed 42를 기록했다. 그러나 실행 날짜와 코드 리비전이 다르고, CoT·MetaGPT에는 외부 도구 검증이 실행되지 않았다. 따라서 동일 입력에 가까운 개발 자료이지 확인적 반복은 아니다. 기준 입력의 SHA-256은 함께 생성한 JSON에 고정했다.

## 관측 결과

| 산출물 시점 | 정적 IaC 의미 점수 | Provider 검증 | 컨테이너·기능 검증 | 해석 |
|---|---:|---|---|---|
| CoT 최초 | 0.75 | 미측정 | 미측정 | HTTPS·영속 데이터가 정적 검사에서 실패했고 디스크 크기는 불명 |
| MetaGPT 최초 | 0.75 | 미측정 | 미측정 | HTTPS가 실패했고 디스크 크기·마운트는 불명 |
| EasyDep 최초 | 1.00 | 통과 | 실패 | 이미지 빌드는 통과했으나 SQLite JDBC 드라이버 누락으로 기동 실패 |
| EasyDep 부분 복구 1 | 1.00 | 통과 | 통과 | H2 파일 저장소로 복구한 뒤 CRUD와 컨테이너 교체 후 영속성 검사가 통과 |

핵심 관찰은 정적 의존성 완성도와 앱 작동이 별개의 게이트라는 점이다. EasyDep 최초 산출물은 클라우드 의미 검사 12/12를 통과했지만 앱은 기동하지 못했다. 이후 구현 소유 하위 작업만 복구한 산출물은 IaC 검증, 컨테이너 기동, CRUD, 재시작 영속성까지 통과했다. 이는 부분 복구 가능성의 사례 증거이지, 다른 시스템보다 우월하다는 증거는 아니다.

CoT와 MetaGPT는 외부 도구 검증이 미실행이므로 실패로 세지 않는다. 반대로 정적 점수 0.75를 기능 성공으로 간주하지도 않는다. 같은 검증기를 세 시스템에 실행하기 전에는 시스템 성공률 비교를 보고하지 않는다.

## 다음 실행 기준

다음 비교 파일럿은 동일한 고정 case 파일과 현재 평가기 버전으로 각 최종 저장소를 순차 검증한다. 단계는 정적 의미 검사, 고정 provider 캐시를 사용한 `init/validate`, Docker 빌드·기동, CRUD, 컨테이너 교체 후 영속성 순이다. 각 단계 실패 시 뒤 단계를 중단하고 실패 단계와 경과 시간을 남긴다. 클라우드 apply는 이 파일럿에 포함하지 않으며, Azure 안전 게이트를 통과한 후보가 생겼을 때 별도로 수행한다.

이후 반복 수는 이 한 번의 균등 검증에서 측정된 시간·실패율을 보고 결정한다. 현재 자료로 허용되는 주장은 “EasyDep의 P2 산출물이 구현 단계 부분 복구 후 로컬 종단 검증을 통과했다”까지다.

## 현재 평가기로 수행한 균등 검증

2026-08-09에 세 저장소를 현재 공통 평가기로 다시 순차 검증했다. 승인된 AzureRM 5.0.1 로컬 mirror만 사용했으며 provider 인터넷 다운로드와 cloud apply는 수행하지 않았다. Docker 평가기는 생성한 컨테이너·볼륨·이미지를 각 실행 뒤 제거했다.

| 시스템 산출물 | 경과 시간 | 정적 의미 | Provider | Docker build | Health·CRUD·교체 영속성 |
|---|---:|---:|---|---|---|
| CoT 최초 | 4.43초 | 10/13 | 실패 | 실패 | 미실행 |
| MetaGPT 최초 | 3.98초 | 10/13 | 실패 | 실패 | 미실행 |
| EasyDep 부분 복구 1 | 88.33초 | 13/13 | 통과 | 통과 | 모두 통과 |

CoT Terraform은 AzureRM `~> 3.0`을 요구해 고정 mirror 5.0.1과 맞지 않았다. Dockerfile은 사전에 존재한다고 가정한 `build/libs/notes-app.jar`가 없어 실패했다. MetaGPT Terraform은 output block에서 지원하지 않는 `type` 인자를 사용해 초기화 전에 실패했고, Dockerfile은 생성되지 않은 Gradle wrapper 디렉터리와 `gradle.properties`를 복사하려다 실패했다. 이 결과는 저장된 산출물의 재실행 결과이지 최신 CoT·MetaGPT를 새로 생성한 반복 결과가 아니다.

EasyDep의 88.33초는 provider 검증뿐 아니라 컨테이너 기동, CRUD 2건, 새 볼륨에 데이터 생성, 컨테이너 제거·재생성, 데이터 조회를 포함한다. 중간 장치 마운트 `/mnt/data`와 계약상 앱 경로 `/var/lib/notes`는 Terraform template 변수 연결을 따라 구분했다. 정적 점수와 앱 기능을 별도 gate로 유지한다.

이 개발 파일럿만으로 시스템 우월성이나 DepKB의 인과 효과를 주장하지 않는다. 다만 현재 평가 절차가 세 저장소에 동일하게 적용될 수 있고, 실패 시 뒤 기능 단계를 통과로 오인하지 않음을 확인했다. 반복 실험 전에 최신 동일 코드 리비전에서 새 산출물을 생성해야 한다.

## 재현

```powershell
python evaluation/research_protocol/archive/2026-08-development/scripts/summarize_system_comparison_pilot.py
python evaluation/research_protocol/archive/2026-08-development/scripts/run_uniform_p2_azure_comparison.py
```

기계 판독 결과는 `evaluation/research_protocol/measurements/2026-08-development/system-comparison-p2-azure-pilot.json`에 기록된다.

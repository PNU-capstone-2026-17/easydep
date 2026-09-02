# 운영·개발 스크립트

`scripts`는 사람이 저장소 루트에서 실행하는 반복 작업을 모은다. 라이브러리 코드가 아니므로
다른 Python 모듈이 이 디렉터리를 import하지 않는다.

## 자주 사용하는 스크립트

| 스크립트 | 용도 |
|---|---|
| `run-easydep.ps1` | Python·프론트엔드 환경, 구현/Testing 툴체인, 개발 MySQL과 서버를 한 번에 준비·실행 |
| `bootstrap-implementation-tools.sh` | 구현 이미지의 컴파일·단위 테스트·IaC 검사 도구 확인 |
| `bootstrap-testing-tools.sh` | Testing 이미지의 Playwright headless shell 추가 확인 |
| `deploy.ps1` | 검증된 배포 산출물 적용 흐름 |
| `provision.ps1` | 인프라 사전 준비 작업 |
| `teardown.ps1` | 이 스크립트가 만든 배포 자원 정리 |
| `aks-start.ps1`, `aks-stop.ps1` | 개발·실험용 AKS 실행 시간 관리 |
| `render_graphs.py` | graph 구조 문서용 렌더링 |
| `validate_deployment_iac_examples.py` | 문서와 예제에 있는 Terraform 같은 클라우드 설치 파일 검사 |

## 안전 원칙

1. 삭제·정리 스크립트는 대상 경로와 cloud resource를 먼저 출력하고 범위를 검증한다.
2. `.env`의 비밀값을 로그에 출력하지 않는다.
3. Python으로 비ASCII JSON이나 다이어그램을 다룰 때 `-X utf8`을 사용한다.
4. 한 단계만 실패하면 저장된 결과를 확인하고 그 단계부터 재개한다.
5. 브라우저 profile·패키지 cache 같은 대량 임시 파일은 저장소 밖의 시스템 임시 경로에 둔다.
6. 스크립트가 시작한 process와 임시 파일만 정리한다. 이름이 비슷하다는 이유로 다른 process를 종료하지 않는다.

PowerShell 스크립트의 공통 설정과 경로 계산은 `_config.ps1`에 둔다. 새 스크립트가 같은 환경
변수를 다시 해석하지 않도록 기존 helper를 먼저 확인한다.

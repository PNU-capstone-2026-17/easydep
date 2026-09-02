# EasyDep 공용 툴체인 입력

이 디렉터리에는 Docker 공용 툴체인 이미지를 만들 때 필요한 작은 설정 파일만 둔다.
애플리케이션별 생성물이나 실행 중 내려받은 캐시는 저장하지 않는다.

Dockerfile은 공통 layer 위에 용도별 대상을 만든다.

- `toolchain`: 구현 단계의 컴파일, 단위·작은 통합 테스트, 정적 검사 도구
- `testing-toolchain`: 위 도구에 Playwright Chromium headless shell만 더한 E2E 환경
- `runtime`: FastAPI, PlantUML과 FR/NFR BERT 모델을 포함한 실제 서버

PyTorch·BERT는 구현 이미지에 들어가지 않고, Playwright도 Testing 이미지에만 들어간다.
세 이미지는 공통 Python layer를 공유하므로 같은 패키지를 로컬에 여러 번 저장하지 않는다.

`opentofu/providers.tf`의 버전은
`app/cloudkb/depkb/provider_cache.py`의 `PINNED_PROVIDERS`와 함께 갱신해야 한다.
Docker 빌드는 이 파일을 읽어 AWS, Azure, GCP provider를 로컬 mirror에 미리 저장한다.
실행 중인 Testing 작업은 `opentofu/tofurc`를 사용하므로 provider registry에 다시 접속하지
않고 `tofu init`과 `tofu validate`를 실행할 수 있다.

# EasyDep 공용 툴체인

루트 `Dockerfile`은 구현과 Testing 작업이 함께 사용하는 `easydep-toolchain` 이미지와
API 서버용 `runtime` 이미지를 만든다. 툴체인에는 JDK 21, Gradle, Node/npm, OpenAPI
Generator, Trivy, OpenTofu, Playwright와 Chromium headless shell 버전이 고정된다.
PlantUML과 FR/NFR 분류 모델은 API runtime에만 둔다.

개발 환경에서는 통합 실행 스크립트가 이미지 존재 여부와 빌드 입력의 SHA-256을 확인하여
필요할 때만 자동으로 빌드한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1
```

툴체인만 직접 만들고 싶을 때에는
`docker build --target toolchain -t easydep-toolchain:local .`을 실행한다.

같은 이미지를 API 서버로 실행할 수 있고, 구현·Testing 작업은 코드가 Python 진입점을
바꿔 별도 컨테이너로 실행한다. `.env`의
`EASYDEP_TOOLCHAIN_IMAGE=easydep-toolchain:local`로 작업 컨테이너를 활성화한다.

작업 컨테이너는 저장소를 `/easydep-workspace`에 연결하고 Gradle cache volume만 공유한다.
구현 컨테이너에는 Docker socket을 주지 않는다. API 컨테이너에서 생성 앱을 Docker로
빌드하고 동적 테스트하려면 운영 환경이 제공하는 Docker daemon/socket을 별도로 연결한다.

OpenTofu Provider 바이너리는 이미지에 넣지 않는다. 구현·Testing runner가
`easydep-tofu-provider-cache` named volume을 `/app/.cache/opentofu`에 함께 연결하므로,
각 Provider는 최초 `tofu init`에서만 내려받고 다음 작업부터 재사용한다. CSP 인증정보는
이미지나 cache에 넣지 않고 Docker Compose의 `env_file`이나 Kubernetes Secret으로 실행할
때 전달한다.

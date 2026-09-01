# EasyDep 공용 툴체인

루트 `Dockerfile` 하나가 API와 작업 Runner가 함께 사용하는 `easydep-toolchain` 이미지를
만든다. PlantUML, FR/NFR 분류 모델, JDK 21, Gradle, Node/npm, OpenAPI Generator,
Trivy와 OpenTofu 버전이 한 이미지에 고정된다. 따라서 각 작업이 도구 이미지를 따로 받지
않고 로컬 실행 파일을 사용한다.

이미지는 한 번 빌드한다.

```powershell
docker build -t easydep-toolchain:local .
```

같은 이미지를 API 서버로 실행할 수 있고, 구현·Testing 작업은 코드가 Python 진입점을
바꿔 별도 컨테이너로 실행한다. `.env`의
`EASYDEP_TOOLCHAIN_IMAGE=easydep-toolchain:local`로 작업 컨테이너를 활성화한다.

작업 컨테이너는 저장소를 `/easydep-workspace`에 연결하고 Gradle cache volume만 공유한다.
구현 컨테이너에는 Docker socket을 주지 않는다. API 컨테이너에서 생성 앱을 Docker로
빌드하고 동적 테스트하려면 운영 환경이 제공하는 Docker daemon/socket을 별도로 연결한다.

OpenTofu Provider cache는 `EASYDEP_TOFU_PLUGIN_CACHE`가 가리키는 폴더를 사용한다. CSP
인증정보는 이미지에 넣지 않고 Docker Compose의 `env_file`이나 Kubernetes Secret으로
실행할 때 전달한다.

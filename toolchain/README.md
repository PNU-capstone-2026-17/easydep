# EasyDep 공용 툴체인 입력

이 디렉터리에는 Docker 공용 툴체인 이미지를 만들 때 필요한 작은 설정 파일만 둔다.
애플리케이션별 생성물이나 실행 중 내려받은 캐시는 저장하지 않는다.

`opentofu/providers.tf`의 버전은
`app/cloudkb/depkb/provider_cache.py`의 `PINNED_PROVIDERS`와 함께 갱신해야 한다.
Docker 빌드는 이 파일을 읽어 AWS, Azure, GCP provider를 로컬 mirror에 미리 저장한다.
실행 중인 Testing 작업은 `opentofu/tofurc`를 사용하므로 provider registry에 다시 접속하지
않고 `tofu init`과 `tofu validate`를 실행할 수 있다.

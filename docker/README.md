# 멤버 구현 Linux runner

`member-runner.Dockerfile`은 멤버 구현 생성, OpenHands 작업, 빌드와 테스트를 같은
Linux 환경에서 실행하기 위한 전용 이미지다. 전체 EasyDep 서비스나 클라우드 CLI를
포함하지 않는다.

이미지는 루트 `requirements.txt`와 동일한 OpenHands 고정 버전 및 JDK 21을 사용한다.
요구사항 분석용 BERT/torch와 웹 서버는 runner 역할 밖이므로 설치하지 않는다. 저장소는
`/easydep-workspace`에 bind mount한다. Docker 소켓은 공유하지 않는다. 기존 멤버의
도구 컨테이너 호출은 허용 목록에 있는 Node·OpenAPI Generator·Gradle의 로컬 실행으로
치환되며, 그 밖의 이미지나 Docker 옵션은 거부한다.

빌드 예시:

```powershell
docker build -f docker/member-runner.Dockerfile -t easydep-member-runner:local .
```

활성화할 때는 `EASYDEP_MEMBER_RUNNER_IMAGE=easydep-member-runner:local`을 지정한다.
값이 없으면 기존 호스트 실행 경로를 유지한다.

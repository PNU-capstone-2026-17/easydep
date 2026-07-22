# puml2code-bce

EasyDep 시스템 구현 에이전트가 BCE PlantUML 클래스 다이어그램을 Java 계약으로 바꾸기
위해 사용하는 `jupe/puml2code` fork다. 일반 목적 코드 생성기가 아니며 Java 출력만
지원한다.

지원 stereotype:

- `<<Boundary>>`, `<<Control>>`, `<<Gateway>>`: Java interface
- `<<Entity>>`: 필드·생성자·getter/setter·설계 메서드를 가진 Java class
- `<<Actor>>`: 코드 생성 제외

직접 설치하지 말고 EasyDep 저장소 루트에서 운영체제에 맞는 bootstrap을 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-implementation-tools.ps1
```

```bash
sh scripts/bootstrap-implementation-tools.sh
```

직접 실행 예시:

```text
node app/implementation/tools/puml2code-bce/bin/puml2code \
  -i class-diagram.puml \
  -l java \
  -p com.example.application.bce \
  -o generated-java
```

원본 commit과 EasyDep 변경 범위는 [UPSTREAM.md](UPSTREAM.md), 라이선스는
[LICENSE](LICENSE)를 참고한다.

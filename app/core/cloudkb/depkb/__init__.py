"""AWS·Azure·GCP Docker-on-VM 배포를 위한 근거 기반 의존성 지식 모듈.

제품 경로는 새 배포의 생성 가능성과 배포 후 인프라 기능에 필요한 관계만 사용한다.
리소스 삭제 순서와 연쇄 삭제는 Terraform/OpenTofu가 담당하며 DepKB 모델에 포함하지 않는다.

`claims.json`의 각 행은 CSP, 관계 방향, 판정, 조건, 관측 근거와 반복 상태를 가진다.
설명 문장은 실행 규칙으로 파싱하지 않는다. Cloud-Barista 자료는 비교 근거 중 하나일 뿐이며,
Cloud-Barista 스키마를 EasyDep의 중립 모델로 간주하지 않는다.
"""

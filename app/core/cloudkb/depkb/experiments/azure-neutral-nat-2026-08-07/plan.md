# Azure 중립 NAT 제어면 실험(사전 등록)

상태: 사전 등록됨, 아직 실행하지 않음\
날짜: 2026-08-07\
기본 리전: `koreacentral` (`--location`으로 변경 가능)

## 질문

Azure는 프라이빗 아웃바운드 NAT를 수명 주기가 독립적으로 관리되는 리소스, 즉 Standard
정적 Public IP, NAT Gateway, subnet 연결의 조합으로 표현하는가? 이 조합이 활성 상태일 때
어떤 참조가 삭제를 막는가?

이 실험은 제어면 실험이다. VM을 생성하지 않으며 게스트에서 관찰되는 egress에 대해서는
어떤 주장도 하지 않는다. P1–P3은 입력이나 평가 기준으로 사용하지 않는다.

## 사전 등록한 리소스와 순서

실행할 때마다 암호학적으로 무작위인 접미사와 새 resource group을 사용한다. 이름은
기록하지만 subscription, tenant, principal, token, credential은 기록하지 않는다.

1. 선택한 location에 resource group 하나를 생성한다.
2. VNet 하나(`10.247.0.0/16`)와 subnet 하나(`10.247.1.0/24`)를 생성한다.
3. Standard 정적 IPv4 Public IP를 생성한다.
4. 해당 Public IP를 참조하는 Standard NAT Gateway를 생성한다.
5. NAT Gateway를 subnet에 연결한다.
6. NAT Gateway와 subnet을 다시 조회하고 관계 유무를 나타내는 불리언과 개수만 기록한다
   (resource ID나 할당된 IP 주소는 기록하지 않는다).
7. 반증 조건 A: NAT Gateway가 Public IP를 계속 참조하는 상태에서 Public IP 삭제를
   요청한다. 작업을 기다린 뒤 존재 여부를 다시 확인한다.
8. 반증 조건 B: subnet이 NAT Gateway를 계속 참조하는 상태에서 NAT Gateway 삭제를
   요청한다. 작업을 기다린 뒤 존재 여부를 다시 확인한다.
9. NAT Gateway가 여전히 존재하면 subnet에서 연결을 해제하고, 해제된 형태를 확인한 뒤
   NAT Gateway를 삭제하고 사라졌는지 확인한다.
10. Public IP가 여전히 존재하면 삭제하고 사라졌는지 확인한다.
11. 무조건 실행되는 `finally` 블록에서 resource group 전체를 삭제하고 삭제 완료를 기다린
    뒤, `az group exists`로 잔존 여부를 확인한다.

선행 리소스 생성에 실패하면 다음 단계로 진행하지 않지만 정리는 계속 실행한다. 삭제 반증
조건은 이번 실행에서 생성한 리소스에만 적용한다.

## 가설과 판정 기준

- H1: 생성에 성공하고, 재조회 결과 NAT Gateway에 Public IP 참조 하나와 subnet에 NAT
  Gateway 참조 하나가 나타난다. 그렇지 않으면 이번 관찰로 해당 조합을 뒷받침할 수 없거나
  설정에 실패한 것이다. 각 단계의 정확한 상태를 보존한다.
- H2: 참조 중인 Public IP의 삭제가 거부되고 Public IP가 남는다. 삭제에 성공하면 해당
  관계에서 참조로 보호되는 독립 수명 주기라는 가설이 반증된다. 이를 스크립트 실패로
  취급하지 않는다.
- H3: subnet에 연결된 NAT Gateway의 삭제가 거부되고 gateway가 남는다. 삭제에 성공하면
  해당 관계에서 참조로 보호되는 수명 주기라는 가설이 반증된다.
- H4: 연결을 해제한 뒤에는 NAT Gateway 삭제에 성공하고, gateway 참조가 사라진 뒤에는
  Public IP 삭제에 성공한다. 실패하면 제안한 역순 정리 모델의 근거가 약해지며 정제된
  오류 코드를 보존한다.
- H5: resource group 삭제가 완료되고 잔존 확인 결과가 false이다. 그렇지 않으면 정리가
  완료되지 않은 실행이므로 운영자의 확인이 필요하다.

HTTP/CLI 성공만으로 의미상 성공이라고 해석하지 않는다. 존재 여부 및 재조회 결과로 각
관찰을 판정한다. 반증 조건의 거부는 예상치 못한 테스트 오류가 아니라 관찰 결과이다.

## 기록하는 증거와 정보 정제

`results.json`에는 UTC 시작/종료 시각, 명령 실행 시간, 종료 상태, 추출한 Azure 오류 코드,
예상/거부 레이블, 존재 여부 불리언, 허용 목록에 포함된 관계 요약을 실행 중 순차적으로
기록한다. 오류 발췌문은 길이를 제한하며 UUID, subscription/tenant 리소스 경로, bearer 형태의
token, IPv4 주소를 가린다. 명령, 환경 변수, CLI 계정 출력, 원본 resource ID, credential은
기록하지 않는다.

## 실행 방법

사전 조건은 설치된 `az` CLI와 격리된 resource group 및 네트워크 리소스를 관리할 권한이
있는 인증 완료 계정이다.

```powershell
python run.py --execute
python run.py --execute --location koreacentral
```

`--execute`는 의도하지 않은 비용과 변경을 막는 안전장치이다. 이 스크립트를 테스트의
일부로 실행해서는 안 된다. 정리가 끝날 때까지 NAT Gateway와 Public IP 비용이 발생할 수 있다.

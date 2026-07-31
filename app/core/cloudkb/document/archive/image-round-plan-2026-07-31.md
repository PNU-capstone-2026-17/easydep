# image 라운드 계획 — vm→image, 3사 (2026-07-31, 실행 전 기록)

## 왜 이것인가

어휘 밖 대기열의 1순위. VM 생성의 필수 인자 후보인데 의존 실측이 0이고,
원래 진단의 외부 대조 오류 셋(sshKey·**spec/image**·azure 방향) 중 하나가
여기 걸려 있다. tumblebug 모델에서도 image는 핵심 자산(image_infos 17만 행)
이다.

## 범위 — 간선 하나로 좁힌다

**vm→image, 존재 질문, 3사.** image를 어휘 주체(TYPES)로 올리면 스키마
추출이 image→vm·image→disk 후보를 쏟아내고 그만큼 미판정(unknown)이 생긴다
— 그건 customImage 대기열의 것이다. 이번 라운드는 참조 훅만 연다:

- azure: `REFERENCE_WRAPPERS`에 `ImageReference → image`
- aws: `AWS_NAME_REFS`에 `ImageId → image`
- gcp: `GCP_PAIR_REFS`에 `(AttachedDiskInitializeParams, sourceImage) → image`

→ 재추출 후 새 후보는 정확히 (azure|aws|gcp) vm→image 셋이고, 셋 다 이번에
판정하므로 unknown이 늘지 않는다.

**생명주기는 재지 않는다(명시)**: 마켓플레이스/플랫폼 이미지는 우리가 지울
권한 자체가 없어 삭제 실험이 성립하지 않는다. 사용자 소유 이미지의 생명주기는
customImage 간선의 것으로 대기열에 남는다.

## 스키마 층 사전 관측 (핀 박힌 캐시에서, 실측 전 기록)

- **CFN `ImageId`는 `Required: False`다.** LaunchTemplate로도 AMI를 줄 수
  있어서다 — CFN Required가 "간선 필수"가 아니라 위치 플래그라는 기존 결론의
  또 한 사례. 서버가 실제로 요구하는지는 동적 층이 답한다.
- azure ARM 스키마는 required를 거의 안 쓴다(기존 실측) — 여기도 동적 층.
- gcp `initializeParams.sourceImage`는 문자열 URL 참조고, 대안 슬롯이 둘 더
  있다(`sourceSnapshot`, `AttachedDisk.source`=기존 디스크).

## 동적 셀

가설(스키마 관측에서): azure·gcp는 **선언 술어**(image ∨ 기존 OS 디스크)로
갈리고 — azure LB frontend(subnet ∨ publicIp)와 같은 꼴 — aws는 부팅을
디스크로 대신할 경로가 없어 필수일 것이다. 가설은 실측으로만 판정에 들어간다.

| CSP | 사다리 | 층 |
| --- | --- | --- |
| azure | A1 이미지·디스크 둘 다 생략 → 거부(합집합 필수) · A2 허상 이미지 id → 거부 · B0 마켓플레이스 이미지로 VM 생성(양성) → VM 삭제·OS 디스크 잔존(기존 실측 재사용) · B1 그 디스크 attach로 이미지 없이 VM 생성 → 성공(단독 선택) | apply |
| aws | D1 ImageId·LT 둘 다 생략 DryRun → 거부(rejectedAt 기록) · D2 허상 AMI DryRun → InvalidAMIID 계열 · D3 SSM 공개 파라미터로 실제 AMI 해석 → DryRun 양성(DryRunOperation) | preflight |
| gcp | G1 sourceImage·source 둘 다 생략 → 거부 · G2 허상 sourceImage → 거부 · G3 이미지에서 디스크 생성 → 그 디스크로 sourceImage 없이 인스턴스 생성 → 성공(단독 선택) | apply |

오라클: 컨트롤 플레인 응답 코드·문장. aws는 실물 생성 없이 preflight 층까지만
— 판정 근거로는 거부가 충분하다는 기존 규율(preflight 거부 = required의 충분
증거) 그대로.

## 함정·위협

- **aws 생략 거부의 층**: `--image-id`가 CLI 클라이언트 필수인지 서버
  MissingParameter인지 실행 전엔 모른다 — rejectedAt을 그대로 기록한다
  (nic→subnet의 client-층 한계 명시와 같은 규율).
- **azure attach 경로**: `--attach-os-disk`는 `--os-type`을 요구할 수 있다 —
  전제 인자이지 판정 대상 아님을 기록.
- **T-단일실행**: 각 셀 1회. 기존 라운드와 같은 지위.
- 비용: azure B-시리즈 VM 2회 수 분·gcp e2-small 1회 수 분·aws 0.

리전: koreacentral · asia-northeast3-a · ap-northeast-2. 라운드 끝 3사 전수
점검(잔여 0).

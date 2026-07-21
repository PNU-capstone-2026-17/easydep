# 추가 소스 조사 — 안 쓰고 있던 것들 (2026-07-21)

조사 중 스쳐 지나갔지만 아직 안 쓴 클라우드 리소스 정보 소스를 전수로 봤습니다.

**먼저 정직하게: 이 조사가 제 주장 세 개를 뒤집었습니다.** 그 정정을 앞에 둡니다.

---

## 0. 제가 틀렸던 것

### (a) "botocore shape에는 min/max가 없다" — 코드 주석에 박아 놨던 오답

`kbcommon/sources.py`에 이렇게 적혀 있었습니다:

> shape에는 min/max가 없다(`Integer = {"type":"integer"}`). 설명문이 유일한 출처다.

**직접 세어 보니 EC2만으로도 shape 4,069개 중 `min` 183 · `max` 175 · `enum` 457개**가
있습니다. 전 서비스로 넓히면 훨씬 많습니다(조사: 426개 서비스 중 411개가 min/max 보유).

참인 것은 **EBS의 `Size`·`Iops`·`Throughput`이 제약 없는 공용 `Integer`를 가리킨다**는
것뿐이었는데, 그 관찰을 전체로 일반화했습니다. **우리가 막으려는 "확신에 찬 오답"을
우리 주석이 저지른 것**입니다. 주석을 정정했습니다.

### (b) Magic Modules를 "핀을 못 박아서" 뺀 근거가 우리 규약과 모순

`gcp-source-decision`에서 MM을 "태그 0개라 핀 불가"로 뺐습니다. 그런데 **우리는 이미
`bicep-types-az`와 `azure-limits-doc`을 커밋 SHA로 핀**하고 있고, AWS CFN zip은 아예
`digest`(재현 불가)로 받아들이고 있습니다. **커밋 SHA는 digest보다 엄격히 더
재현 가능합니다.** 제 근거가 우리 자신의 핀 분류와 어긋났습니다.

프로바이더를 고른 다른 이유(증발 12.7%, `ForceNewIfChange`가 YAML엔 없음)는 유효하지만,
**"핀"은 뺄 근거가 아니었습니다.**

### (c) "Azure에서 우리가 KCC와 같은 실수를 한다"는 걱정 — 대체로 기우

제가 직전 메시지에서 키운 걱정입니다. 조사가 우리 캐시로 직접 확인했습니다:
**bicep-types는 이미 `pattern` 920 · `maxLength` 827 · `minValue` 446 · `maxValue` 337을
담고 있고, 우리 파서가 그걸 전부 소비합니다.** "생성 과정에서 제약을 잃는다"는 가설은
대체로 거짓입니다.

**진짜 구멍은 딱 하나**입니다: `x-ms-mutability`가 bicep-types에 **0건**입니다.
생성기에 `Immutable` 플래그 자체가 없어서(`["read","create"]`가 writable&readable로
접혀 `flags: None`이 됨), 불변성 정보만 통째로 증발합니다. 우리 `azure.py` docstring이
이미 그 공백을 적어 뒀는데("createOnlyProperties에 해당하는 불변 정보가 없다"),
**원인이 원본이 아니라 생성기라는 걸 몰랐습니다.**

---

## 1. 할 만한 것 (순위)

### T1. cfn-lint `data/` — 이번 조사 최대 수확

MIT-0, `pip install cfn-lint`로 받는 정적 데이터. 조사자가 wheel을 풀어 직접 셌습니다:

| 내용 | 규모 |
|---|---|
| `if`/`then` 조건부 스키마 | 43개 |
| `cfnGather` 교차 리소스 규칙 | 17개 |
| 리전별 인스턴스 타입 enum | **79,810 (리전, 타입) 쌍** |
| `db_instance_class.json` | **160,426 (엔진, 버전, 클래스) 삼중항** |

마지막 것은 **자격증명이 필요한 `DescribeOrderableDBInstanceOptions`의 결과를
정적 파일로 받아 놓은 것**입니다. 우리가 "자격증명 필요"로 배제했던 데이터가
MIT-0으로 공개돼 있는 셈입니다.

레지스트리 zip과 **겹치지 않습니다** — 이 파일들은 레지스트리가 표현 못 해서 존재합니다.

> **함정 둘.** `cfnGather`/`$data`는 자체 어휘라 전용 해석기가 필요합니다. 그리고
> `aws_ec2_instance/instancetype_enum.json`의 `"all"` 키가 **빈 enum**인데, 그대로 읽으면
> **모든 인스턴스 타입을 거부**하게 됩니다. 정확히 우리가 막으려는 fail-closed입니다.

### T2. terraform-provider-aws / azurerm — AWS의 빈 축을 정면으로 채움

제가 직접 쟀습니다(v6.55.0 / v4.81.0):

| 구문 | aws | 지금 우리 AWS | azurerm | 지금 우리 Azure |
|---|---:|---:|---:|---:|
| 교차 필드 조건 4종 합계 | **1,219** | **0** | **1,352** | **0** |
| `IntBetween` | 513 | — | 580 | — |
| `StringInSlice` | 315 | — | **1,942** | enum 536 |
| `Default` | 1,663 | 673(전체) | 2,795 | — |
| **`ForceNewIf`**(조건부 불변) | **56** | **0** | 0 | 0 |

설계 문서에서 *"AWS 메타스키마가 `if`/`then`을 금지해 조건부를 표현할 방법이 없다"*고
결론냈는데, **CloudFormation에 없을 뿐 Terraform 프로바이더에는 있습니다.**
그때 프로바이더를 AWS 쪽으로는 안 봤습니다.

`ForceNewIf` 56건은 GCP에서 9건 얻어 `update_restricted` 축을 만든 그것의 **여섯 배**입니다.

> **성격이 다릅니다.** 생성 코드 비율을 재보니 google **100%** / aws **19%** /
> azurerm **0%** 입니다. google은 "선언은 있는데 증발"이 문제였고(빈 목록 194건),
> aws·azurerm은 **빈 목록 0건**인 대신 **사람의 손 큐레이션**이라 다르게 틀릴 수 있습니다.
> 같은 근거 라벨을 쓰면 안 됩니다.

### T3. botocore 전 서비스 — 이미 받는 소스인데 EC2만 씀

핀도 파서도 있습니다. `prose.py`가 이미 `Valid Range: Minimum value of X.` 관용구를
처리하는데 그게 botocore 문서 문체입니다 — **대부분 재사용**입니다.
다만 자유 서술 `Constraints:` 블록은 파지 말고 **파이프 구분 `Valid Values: A | B | C`**만
캐는 게 맞습니다(조사 둘이 독립적으로 같은 결론).

### T4. Azure `azure-rest-api-specs` — **`x-ms-mutability` 하나만**

위 (c)에서 밝힌 대로 나머지는 이미 bicep에 있습니다. 6개 RP 표본에서
**약 933건의 순증 불변성**. 그 필드와 `x-ms-arm-id-details`만 캐고 나머지는 건드리지 않습니다.

### T5. AWS Price List 다른 서비스

268개 offer code(398이 아님 — 요약기가 지어낸 수). RDS/DocDB가 우리가 이미 쓰는
`productFamily: Storage`와 **같은 모양**으로 `minVolumeSize`/`maxVolumeSize`를 줍니다.

> **함정**: **가격이 있다 ≠ 주문 가능하다.** us-east-1이 `db.t1.micro` 가격을 아직 줍니다.
> 주문 가능성은 cfn-lint의 16만 삼중항이 낫습니다.
> 라이선스에 재배포 허가가 없으니 **빌드 때 받고 저장소에 넣지 않습니다**(지금도 그렇습니다).

---

## 2. 함정 — 확인된 막다른 길

- **Terraform/OpenTofu 스키마 JSON은 구조적으로 불가.** `tfplugin6.proto`의 `Attribute`에
  `ForceNew`·`ConflictsWith`·`ExactlyOneOf`·`Default`·enum을 담을 자리가 **없습니다.**
  인코더가 빠뜨린 게 아니라 **플러그인 경계를 넘지 못하는** 데이터입니다.
  **Go 파싱은 피할 수 없습니다.** 더 찾지 마세요.
- **GCP Discovery에 수치 한도가 없습니다.** compute v1을 직접 파싱해 `schemas` 안
  `minimum` **0건** 확인(201건은 전부 페이지네이션 파라미터). 설계 문서에 "기본값의 최종
  심판"으로 적어 뒀는데 **범위는 아닙니다** — `readOnly` 1,466 · `enum` 707에는 여전히 쓸모.
- **googleapis proto는 좋은데 정작 필요한 곳에 없습니다.** 저장소 전체로 `IMMUTABLE`
  2,941 · `resource_reference` 12,535인데 `compute/v1/compute.proto`는 **0 / 0**입니다
  (그게 Discovery에서 생성되기 때문).
- **Crossplane/Upjet CRD는 우리 Go 파싱보다 엄격히 손실이 큽니다.** `oldSelf` 전 코퍼스 0건.
- **ec2instances.info / Vantage: 함정.** 태그·릴리스 없고 데이터가 커밋돼 있지 않습니다
  (스크레이퍼가 자격증명을 씁니다). 게다가 Price List의 파생물인데 **핀은 Price List만
  됩니다.**
- **`cfn-lint schemas/providers/`는 존재하지 않습니다**(gitignore된 레거시 경로).
  **Azure `azure-resource-manager-schemas`**는 bicep-types보다 엄격히 나쁩니다.

---

## 3. 설계에 미치는 영향

우리 모델이 대부분 그대로 받습니다 — `Constraint.condition`이 이미 있고
`aws_limits.py`가 이미 `condition={"property": "VolumeType", ...}`을 씁니다.
cfn-lint의 리전별 enum은 그 칸에 **그대로** 들어갑니다.

**딱 하나 확장이 필요합니다**: RDS의 (엔진 × 버전 → 클래스)는 **조건이 둘**입니다.
지금 `condition`은 평평한 단일 조건만 담습니다. 설계 문서에 *"필요해지면 그때 넓힌다"*고
적어 뒀는데, **여기가 그때**입니다.

---

## 4. 다음에 할 것 (추천 순서)

1. ~~**terraform-provider-aws**~~ — ✅ 완료(`capacitykb/parsers/tpaws.py`).
   **실제 수확은 예상보다 작습니다: 878건 / 155종**(원본에는 교차 조건 1,219건이
   있는데 우리 손에 들어온 건 28건). 이유를 셋으로 갈라 셌습니다:
   - CFN 타입에 못 이은 리소스 **837종** — 상당수는 매핑 실패가 아니라 **CFN에 그
     리소스가 없는 것**(`aws_account_alternate_contact` 등)
   - **Plugin Framework 리소스 497종** — 스키마 모양이 완전히 달라(Attributes 맵)
     지금 파서로 못 읽습니다. **다음에 손댈 곳은 여기입니다.**
   - 나머지는 출력 전용·Terraform 전용

   그래도 **AWS의 빈 축 두 개가 0에서 열렸습니다** — 조건부 불변 24건
   (`DynamoDB::Table.RestoreSourceName`, `DocDB::DBCluster.ServerlessV2ScalingConfiguration` 등),
   교차 필드 조건 28건.
2. **cfn-lint 리전별 enum** — 기존 `condition` 칸에 바로 들어갑니다. `"all"` 빈 enum
   함정을 테스트로 먼저 고정할 것.
3. **`x-ms-mutability`** — Azure 불변성 0건을 933건으로. 범위가 작고 목표가 분명합니다.
4. **botocore 전 서비스** — 파이프 구분 `Valid Values`만.
5. cfn-lint RDS 삼중항 — **다중 조건 확장이 선행**돼야 합니다.

**출처**: 조사 보고 원문의 수치는 조사자가 wheel·저장소를 직접 열어 센 것이고,
프로바이더 3종 수치(생성 코드 비율 포함)와 botocore EC2 수치는 제가 직접 쟀습니다.

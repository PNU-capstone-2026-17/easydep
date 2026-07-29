# 실행 가능한 예제 모음 (2026-07-23)

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md). 이 문서는 작성 시점의
> 스냅샷이고 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을
> 시작하지 말 것.** 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

**전부 직접 돌려 출력을 확인한 것**만 적었습니다. 클론 직후 `uv sync` 하나로
대부분 동작합니다 — 산출물이 `data/*.gz`에 커밋돼 있어 빌드가 필요 없습니다.

```
uv sync
```

> **이 저장소의 uv 실행 관례**: uv가 PATH에 없으면
> `C:\Python313\python.exe -m uv run …` 형태로 부릅니다. 아래 예제는 `uv run`으로
>적었습니다.
>
> **한글 출력이 깨지면** PowerShell에서 먼저:
> `$env:PYTHONIOENCODING='utf-8'; [Console]::OutputEncoding = [Text.Encoding]::UTF8`

---

## 0. 가장 빨리 전체를 보는 법

```bash
uv run pytest -q                 # 1,145건 통과 · 14 skipped (약 1분)
uv run python -m kbcommon verify # 산출물 불변식 — 마지막 줄까지 ✓면 정상
```

`verify`는 KB 사이 조인이 깨지지 않았는지 봅니다(예: `capacity-joins-graph`
9,409건, `bundle-matches-mirror` 36건).

---

## 1. 대화형 에이전트

```bash
uv run python main.py                  # 로컬 지식베이스 단독 (기본)
uv run python main.py --verbose        # 도구 호출·인자·토큰 사용량 표시
uv run python main.py --max-turns 40   # 도구를 많이 부르는 질의용
uv run python main.py --tumblebug      # cb-tumblebug MCP(라이브 축) 연결
```

`.env`에 `API_KEY` · `BASE_URL` · `MODEL`이 필요합니다(NIM 엔드포인트).
**KB만 쓰는 아래 2~7번은 `.env` 없이 됩니다.**

물어볼 만한 것:

```
AWS 서울에 VM 하나 올리려면 뭐가 같이 필요하고 얼마나 들어?
DynamoDB 쓰던 앱을 Azure로 옮기면 뭘 써야 해?
Azure Standard_D2s_v5를 koreasouth에서 3년 예약하면 시간당 얼마야?
AWS에서 /24 서브넷 하나에 VM 몇 대까지 띄울 수 있어?
```

---

## 2. 설계도 → 배포 구성 (앱 계층, 최신)

에이전트 없이 직접 돌려 보는 것이 가장 빠릅니다.

```bash
uv run python -c "
import json
from nim_agent.design_tools import compose, _render_plan_text
from appkb.diagram import render
d = json.load(open('appkb/examples/order-demo.json', encoding='utf-8'))
plan = compose(d)
print(_render_plan_text(plan))
print(render(plan))
"
```

나오는 것 — 노드 7개·선 5개, 줄마다 근거(`설계 산출물`/`설계자 지정`/`지식베이스`/
`우리 추론`)가 붙고 PlantUML이 이어집니다.

```
[관리형 서비스] 3개
  - OrderService 저장소 (order-api-db) ⚠ → aws::AWS::RDS::DBInstance
      · [우리 추론] 엔티티 2개를 소유(Order, OrderItem) → 영속 저장소 필요
      · [지식베이스] svcmap: app::relationalDatabase → aws::AWS::RDS::DBInstance
```

**입력 계약만 검증**하려면:

```bash
uv run python -c "
import json
from appkb.contract import validate_design
d = json.load(open('appkb/examples/order-demo.json', encoding='utf-8'))
print(validate_design(d) or '통과')
"
```

계약을 일부러 깨서(예: `componentId`를 없는 값으로) 어떤 목록이 나오는지 보면
계약의 성격이 바로 보입니다. 스키마는 `appkb/schema.json`, 근거는
`document/design-input-contract-2026-07-23.md`.

> **5단계 전체 실행 기록**(입력 → 계약 검증 → 계획 → PlantUML → 자체 검증)과
> **틀린 입력 다섯 가지의 출력**은 `document/end-to-end-example.md`에 있습니다.

---

## 3. 비용 — costkb

```bash
# 조건에 맞는 스펙 후보 (성능 경고가 함께 붙는다)
uv run python -m costkb query --vcpu-min 2 --mem-min 4 \
    --provider aws --region ap-northeast-2 --limit 3

# 무엇을 알고 무엇을 모르는가
uv run python -m costkb coverage
```

```
후보 3건 (정가·상시가동 730h/월 기준):
  aws  t3a.medium  ap-northeast-2  2 vCPU / 4 GiB  $0.0468/h ≈ $34.16/월
```

할인 가격(스팟·예약·저축 플랜)은 API 조회 API로 봅니다:

```bash
uv run python -c "
from costkb.agent_api import azure_discount_pricing, discount_pricing
print(azure_discount_pricing('Standard_D2s_v5', 'koreasouth'))
print(discount_pricing('e2-standard-4', 'asia-northeast3'))
"
```

---

## 4. 성능 — perfkb

```bash
uv run python -m perfkb show --provider aws --spec t3.medium   # 버스트 경고
uv run python -m perfkb show --provider ibm --spec bx2-16x64   # IBM 신호
uv run python -m perfkb coverage
```

```
ibm bx2-16x64
  네트워크 대역폭: 32000 Mbps · 포트 속도: 25000 Mbps
  최대 네트워크 인터페이스 수: 5 · NUMA 노드 수: 2
  CPU 제조사: Intel · vCPU 점유 방식(원본 표기): dedicated
```

---

## 5. 의존성·동치 — graphkb

```bash
# 생성 선행 체인
uv run python -m graphkb query --deps "AWS::EC2::Instance"

# 삭제 영향
uv run python -m graphkb query --dependents "AWS::EC2::VPC"

# 의존 관계가 많은 타입 순위
uv run python -m graphkb query --rank dependencies --provider aws --limit 10
```

**관리형 서비스 동치**(svcmap, 최신)는 API로:

```bash
uv run python -c "
from graphkb.agent_api import equivalent_types
print(equivalent_types('AWS::DynamoDB::Table'))
print(equivalent_types('AWS::S3::Bucket'))
"
```

근거 라벨(`MS 비교표+diagrams 분류 교차 확인` 등)과 **"안내이지 배포 가능이 아님"**
경계가 함께 나옵니다.

---

## 6. 용량·제약 — capacitykb

```bash
# 조건별 한도 (VolumeType마다 다르다)
uv run python -m capacitykb query --limits "AWS::EC2::Volume" --property Size

# 변경 시 재생성되는 속성
uv run python -m capacitykb query --immutable "AWS::EC2::Subnet"

# 값 판정 — 조건을 줘야 확정된다
uv run python -m capacitykb query --check "AWS::EC2::Volume" --property Size --value 30000
```

마지막 것은 **"판정 불가 — 알려진 제약이 없습니다"**가 나오는데 그게 정상입니다:
gp2는 16,384 GiB, gp3는 65,536 GiB라 **종류를 모르면 판정할 수 없습니다.**
임의로 하나를 고르지 않는 것이 이 KB의 설계입니다.

---

## 7. 리소스 군·사이징 — bundlekb / sizingkb

```bash
uv run python -m bundlekb show --type "core::vm"     # VM에 딸려오는 것
uv run python -m bundlekb show --type "azure::Microsoft.Compute/virtualMachines"
uv run python -m bundlekb coverage

uv run python -m sizingkb show --subnet 24 --provider aws   # 251개
uv run python -m sizingkb show --presets                    # 컨테이너 프리셋
uv run python -m sizingkb show --workload llm-bench         # 워크로드 참조점
```

```
aws /24 서브넷: 전체 256개 중 예약 5개를 빼면 **251개**를 쓸 수 있습니다.
  ⚠ **기계 판독 소스가 없어 사람이 적은 값**입니다.
```

---

## 8. 에이전트 회귀 하네스 (프로브)

**`.env`가 필요합니다** — 실제 모델을 태웁니다.

```bash
uv run python -m tools.agent_probe                    # 49건 전부 (약 15분)
uv run python -m tools.agent_probe --only DS1         # 설계도 → 배포 구성
uv run python -m tools.agent_probe --only SM1,SM2     # 관리형 서비스 동치
uv run python -m tools.agent_probe --only GL1,GL2,GL3,GL4  # 다축 가이드라인
uv run python -m tools.agent_probe --only P1,P2,P3,P4,P5   # 할인·웹 보충
uv run python -m tools.agent_probe --retries 2 --out out.json
```

도구 호출 기록과 **주장 대조**(지어낸 값·출처 세탁·"모른다"를 "0원"으로 뒤집기)를
함께 보여줍니다.

---

## 9. 빌드 — 원본에서 다시 만들기 (선택)

산출물이 커밋돼 있으므로 **평소엔 필요 없습니다.** 소스를 갱신하거나 재현을
확인할 때만 씁니다. 네트워크와 시간이 듭니다.

```bash
uv run python -m costkb build                 # 미러 (pgdumplib 필요: uv sync --extra perfkb)
uv run python -m perfkb build
uv run python -m graphkb build --source svcmap        # 앱 개념 ↔ 관리형 서비스
uv run python -m bundlekb build --source avm
uv run python -m envkb build-regions          # 환경 사실 KB (탄소·지연 등도 build-<축>)

# 재배포 허가 문구가 없는 것들 (NOTICE 참조 — 명시 조건으로 커밋됨)
uv run python -m costkb build-azure-pricing   # VM 할인가, 39개 리전 · 약 7분
uv run python -m costkb build-azure-managed   # 관리형 과금 축, 39개 리전 · 약 3분
uv run python -m perfkb build-ibm
```

---

## 어디를 읽으면 되나

| 알고 싶은 것 | 문서 |
|---|---|
| 전체 개요(비전문가용) | `document/plain-language-overview.md` |
| 왜 이렇게 결정했나 | `document/decisions.md` |
| 목표 2 대비 남은 것 | `document/goal2-open-items.md` |
| **끝에서 끝까지 예제** | `document/end-to-end-example.md` |
| 앱 계층 계획 | `document/app-layer-plan-2026-07-23.md` |
| 설계도 입력 계약 | `document/design-input-contract-2026-07-23.md` |
| 질의 예시 모음 | `document/kb-test-queries.md` |

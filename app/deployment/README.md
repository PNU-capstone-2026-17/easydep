# NIM Planner Agent

OpenAI **Agents SDK**로 만든 자율 계획형 에이전트를, LLM은 **NVIDIA NIM 무료 엔드포인트**로
구동하는 참조 스캐폴드입니다. 작업 카탈로그 전달 → 자율 계획 수립 → 함수 호출까지
하나의 예제로 보여줍니다.

여기에 더해, 클라우드 네이티브 앱 개발을 돕기 위한 **지식베이스 세 개**(리소스 의존성 ·
용량/제약 · 스펙/가격)를 축별로 분리해 에이전트 도구로 연결했습니다
→ [지식베이스](#클라우드-지식베이스-graphkb--capacitykb--costkb)

## 동작 원리 (핵심 제약)

- **Chat Completions API 사용**: NIM은 OpenAI 호환이지만 Responses API를 지원하지 않습니다.
  그래서 SDK 기본값(Responses) 대신 `OpenAIChatCompletionsModel` + `AsyncOpenAI(base_url, api_key)`를
  에이전트 `model=`에 직접 넣습니다. (`nim_agent/config.py`)
- **Tracing 비활성화**: `set_tracing_disabled(True)`로 OpenAI 플랫폼 trace 전송을 끕니다
  (OPENAI_API_KEY 불필요).
- **함수 호출**: `openai/gpt-oss-120b`가 tool calling을 지원하므로 `@function_tool` 로컬
  도구가 동작합니다. 기본 실행 경로는 **로컬 지식베이스 단독**이고, 외부 서버는 필요 없습니다
  (cb-tumblebug MCP는 `--tumblebug`으로만 붙습니다 — [MCP](#cb-tumblebug-mcp-옵트인) 참고).

## 설정

`.env` (또는 `.env.example` 복사) 에 다음을 채웁니다:

```
API_KEY=nvapi-...            # https://build.nvidia.com 에서 발급
BASE_URL=https://integrate.api.nvidia.com/v1
MODEL=openai/gpt-oss-120b
```

## 실행

```bash
uv sync                              # 의존성 설치
uv run python main.py
uv run python main.py --verbose      # 도구 호출·인자·결과·토큰 사용량 표시
uv run python main.py --max-turns 40 # 도구를 많이 부르는 질의용 (기본 20)
uv run python main.py --tumblebug    # cb-tumblebug MCP(라이브 축) 연결 — 기본 꺼짐
```

대화 예시:

```
you> 뭐 할 수 있어?
you> 사용자 1000명 규모 REST API + PostgreSQL을 AWS에 올리려는데 월비용 추천해줘   # 클라우드 산정
you> VM을 만들려면 어떤 리소스들이 먼저 필요해?           # 의존성 KB
you> AWS VPC가 Azure랑 GCP에선 뭐야?                     # 의존성 KB (동치 매핑)
you> AWS에서 의존성이 가장 큰 리소스 타입은?              # 의존성 KB (집계)
you> EBS 볼륨을 100TB로 만들 수 있어?                    # 용량 KB
you> 서브넷의 VpcId를 나중에 바꿔도 돼?                   # 용량 KB (변경 제약)
you> exit
```

`--verbose`는 에이전트가 **어떤 도구를 어떤 인자로** 불렀는지 실시간으로 보여줍니다
(ANSI 색상, 터미널이 아니면 자동으로 평문). 도구 사용이 의도대로 되는지 확인할 때 씁니다.

## 클라우드 지식베이스 (graphkb / capacitykb / costkb)

에이전트가 클라우드 질문에 **추측 대신 근거**로 답하도록, 지식베이스 세 개를 **축별로 분리**해
제공합니다. 축이 다르면 패키지도 다릅니다.

| 패키지 | 축 | 에이전트 도구 |
|---|---|---|
| `graphkb/` | **의존성** — 무엇이 무엇을 필요로 하나 | `kb_creation_order`, `kb_deletion_impact`, `kb_equivalent_types`, `kb_describe_type`, `kb_search_types`, `kb_rank_types` |
| `capacitykb/` | **용량·제약** — 무엇이 허용되나 / 한도 / 바꿀 수 있나 | `cap_check_value`, `cap_property_limits`, `cap_immutable_properties`, `cap_allowed_values`, `cap_service_quota` |
| `costkb/` | **스펙·가격** — 무엇을 살 수 있고 얼마인가 | `cost_recommend_specs`, `cost_estimate_monthly` |

세 KB는 코드가 분리돼 있지만 `graphkb`/`capacitykb`는 **같은 타입 id 규약**
(`aws::AWS::EC2::Subnet`)을 써서 조인할 수 있고, 다운로드 캐시만 `kbcommon/`으로 공유합니다.
**어느 KB도 `nim_agent`를 import하지 않습니다** (단방향: 에이전트 → KB).

**성격 차이에 주의하세요:**
- `graphkb`·`capacitykb` = 공개 스키마에서 추출한 **규칙**. 레코드마다 **근거(evidence)와
  신뢰도(confidence)**가 붙어 확실한 정보와 추정을 구분합니다.
- `costkb` = **카탈로그**. 번들 36건(손 큐레이션)으로 빌드 없이 항상 동작하고,
  `costkb build`를 돌리면 **cb-tumblebug 스펙 DB의 미러**(73,083건 · 10개 프로바이더 ·
  163개 리전)로 넓어집니다.

### costkb는 왜 AWS/Azure 공개 API가 아니라 cb-tumblebug 미러인가

에이전트의 **라이브 경로가 cb-tumblebug MCP**(`recommend_vm_spec`)이기 때문입니다. 코드를
추적하면 그 도구는 `spec_infos` 테이블을 읽어 **컬럼을 그대로 투영만** 합니다:

```
recommend_vm_spec → POST /recommendSpec → FilterSpecsByRange → spec_infos
costkb build      → assets.dump.gz (같은 spec_infos)
```

같은 테이블에서 빌드하면 오프라인 기준선과 라이브 경로가 **같은 세계**를 봅니다.
반대로 AWS Price List에서 직접 빌드하면 *더 정확해 보이지만 오히려 불일치*가 생깁니다 —
상위 CB-Spider 버그(`ConvertMBToMiBInt64`가 비율을 제곱이 아니라 한 번만 적용)로
GCP·Azure의 메모리가 실제보다 2.4% 낮은데, **라이브 MCP도 그 값으로 필터링**하기 때문입니다.
`n2-highmem-8`을 우리만 64 GiB로 알면 "16 GiB 이상" 질의가 두 경로에서 다른 답을 냅니다.

그래서 `memGiB`는 **미러값 그대로**(필터·판정용, MCP 일치) 두고 `memGiBActual`에 보정값을
병기합니다. 빌드 요약이 프로바이더별 버그 영향 범위를 실측으로 보여줍니다:

```
azure  n=34,846  정수 35.0%  bug 64.2% ← 보정 적용
aws    n=18,564  정수 97.9%  bug  0.0%
gcp    n=11,622  정수 19.5%  bug 77.6% ← 보정 적용
tencent/alibaba/ibm/ncp/kt/nhn/openstack …  bug 0.0%
```

```bash
# 산출물 생성 (클라우드 자격증명 불필요, output/ 에 저장)
uv run python -m graphkb build --source tumblebug   # 벤더 중립 의존성 그래프
uv run python -m graphkb build --source cfn         # AWS
uv run python -m graphkb build --source mapping     # 벤더 간 동치 매핑
uv run python -m capacitykb build --source cfn      # AWS 제약 (46,810건)
uv run python -m capacitykb build --source azure-quota

# CLI로 직접 질의도 가능
uv run python -m graphkb query --rank dependents --provider aws --limit 5
uv run python -m capacitykb query --immutable "AWS::EC2::Subnet"
uv run python -m costkb coverage                    # 데이터셋 경계 확인 (빌드 불필요)
uv run python -m costkb query --vcpu-min 4 --provider aws --region us-east-1

# costkb 미러 빌드는 extra가 필요합니다 (덤프 파서 pgdumplib)
uv sync --extra costkb
uv run python -m costkb build                       # → output/tumblebug-cost.json (73,083건)
uv run python -m costkb build --tag v0.12.25 --refresh
uv run python -m costkb build --rows-file rows.tsv  # pg_restore 우회 경로
```

> graphkb/capacitykb는 산출물이 없으면 도구가 빌드 명령을 안내합니다. 자세한 설명(비전문가용)은
> [`document/cloud-kb-guide.md`](document/cloud-kb-guide.md) 참고.

## 클라우드 리소스/비용 산정 (cloud_sizing)

앱 요구사항을 주면 에이전트가 **리소스 사이징 → VM 스펙 추천 → 월 비용 추정**을 수행합니다.

- **번들 데이터셋(기본, 빌드 불필요)**: `costkb/specs.json`의 큐레이션된 AWS/GCP/Azure
  온디맨드 정가 36건. 서버/클라우드 계정 없이 즉시 데모 가능하지만 vCPU 8·4개 리전까지가
  경계입니다.
- **미러 빌드(권장)**: `python -m costkb build`를 한 번 돌리면 `output/tumblebug-cost.json`
  (73,083건 · vCPU 최대 896 · 메모리 최대 32 TB)이 생기고 도구가 자동으로 이쪽을 씁니다.
  여전히 서버·자격증명은 필요 없습니다. 커버리지: `uv run python -m costkb coverage`
- **cb-tumblebug MCP(옵트인, 라이브 가격)**: `--tumblebug`으로 켭니다. 아래 참조.

> `recommend_vm_spec`(MCP)과 `cost_recommend_specs`(costkb)는 **축이 다른 게 아니라 같은
> 질문에 답하는 두 소스**이고, 미러 빌드 후에는 **같은 테이블(`spec_infos`)을 봅니다**.
> 즉 어느 쪽을 골라도 스펙 집합은 같고 갈리는 건 **가격뿐**입니다 — 덤프는 스냅샷이라
> 가격은 드리프트하고 스펙은 거의 안 합니다.

> ⚠️ 비용은 **온디맨드 정가·대표 리전 기준 추정치**이며 실제 청구서가 아닙니다
> (스토리지/이그레스/관리형 서비스/약정할인 미반영).

**계획 게이트**: 이 워크플로의 도구들은 `record_plan`이 먼저 기록돼야 실행됩니다. 프롬프트로는
순서가 지켜지지 않고(모델 3종 모두 실패) NIM은 `tool_choice`도 무시하므로, 도구가 직접
거부합니다. 배경은 `nim_agent/session.py` 참고.

## cb-tumblebug MCP (옵트인)

```bash
uv run python main.py --tumblebug        # 또는 NIM_AGENT_TUMBLEBUG=1
```

[cb-tumblebug](https://github.com/cloud-barista/cb-tumblebug)을 Docker로 띄우면(`make up`,
내장 MCP 서버 `http://127.0.0.1:8000/mcp`) 라이브 `recommend_vm_spec`과 인프라 실행 도구
(`create_infra_dynamic` 등)가 추가됩니다. 접속 정보는 `.env`의 `TUMBLEBUG_MCP_URL`.
서버가 없으면 경고만 찍고 costkb 단독으로 폴백합니다.

**왜 기본이 꺼져 있나** — `recommend_vm_spec`은 `cost_recommend_specs`와 **같은 질문에 답하는
다른 소스**입니다. 둘 다 쥐여주면 에이전트가 무엇을 부를지는 **프롬프트 권고로만** 정해지는데,
이 프로젝트는 프롬프트로 도구 순서를 지시하는 게 안 통한다는 걸 이미 확인했습니다(계획 게이트를
코드로 만든 이유). 실측하기 전까지 그 불확실성을 기본 경로에 넣지 않습니다. 대신 costkb가 이
서버의 **오프라인 미러**라 잃는 게 거의 없습니다 — 스펙은 같고 가격만 스냅샷입니다.

라이브 가격이나 **실제 배포·상태 조회**(costkb에 대응물이 없는 축)가 필요할 때 켜세요.

> **유지보수 주의** — `optional_tumblebug_mcp`의 `try/except`는 **셋업(`connect()`)만** 감싸야
> 합니다. `asynccontextmanager`는 `with` 본문의 예외를 `yield` 지점으로 던지기 때문에,
> `yield`까지 감싸면 에이전트 실행 중 난 오류(예: Max turns exceeded)가 "MCP 기동 실패"로
> 둔갑하고, except 안에서 두 번째 `yield`를 하면 진짜 예외마저
> `RuntimeError: generator didn't stop after athrow()`로 덮여 사라집니다. (실제로 겪은 버그라
> `tests/test_tumblebug_mcp.py`가 회귀를 막고 있습니다.)

## 지식 차원 분담

에이전트가 질문의 **축**에 맞는 도구를 쓰도록 `nim_agent/agent.py`의 instructions에 명시돼
있습니다. 축이 겹치면 엉뚱한 도구를 부르거나 자체 지식으로 지어내기 때문입니다.

| 질문 | 담당 |
|---|---|
| 무엇이 무엇을 필요로 하나 (순서·영향·동치) | `graphkb` (`kb_*`) |
| 무엇이 허용되나 / 한도 / 바꿀 수 있나 | `capacitykb` (`cap_*`) |
| 무엇을 살 수 있고 얼마인가 | `costkb` (`cost_*`) — `--tumblebug`으로 켜면 `recommend_vm_spec`이 **같은 축**의 라이브 소스로 합류 |
| 지금 무엇이 떠 있나 / 실제로 만들기 | cb-tumblebug MCP 전용 (`--tumblebug` 없으면 답할 수 없는 축) |

> 4번 축이 "현재 상태"가 아니라 **"현재 상태·실행"**인 이유: `create_infra_dynamic`은 조회가
> 아니라 변경 행위입니다. 그리고 MCP의 `recommend_vm_spec`은 4번이 아니라 **3번 축**에
> 속합니다 — MCP를 통째로 "동적 상태"로 묶으면 부정확합니다.

## 구조

| 파일 | 역할 |
|------|------|
| `nim_agent/config.py` | .env 로드 + NIM Chat Completions 모델 팩토리 + tracing off |
| `nim_agent/catalog.py` | 작업 카탈로그(구조화 Task 목록) |
| `nim_agent/tools.py` | `@function_tool` 로컬 도구 집합 (`LOCAL_TOOLS`) |
| `nim_agent/agent.py` | instructions(축 분담·답변 스타일) + 도구 조립 |
| `nim_agent/verbose.py` | verbose 모드 — 스트리밍 이벤트 요약 + 기본 max_turns |
| `nim_agent/graph_tools.py` | 의존성 KB 질의 도구 (`kb_*`) |
| `nim_agent/capacity_tools.py` | 용량·제약 KB 질의 도구 (`cap_*`) |
| `nim_agent/cost_tools.py` | 스펙·가격 KB 질의 도구 (`cost_*`) + 계획 게이트 |
| `nim_agent/session.py` | 요청 단위 상태 — 계획 게이트의 근거 |
| `nim_agent/tumblebug_mcp.py` | cb-tumblebug MCP 헬퍼 (옵트인 — `--tumblebug`) |
| `graphkb/` | 리소스 타입 **의존성** 지식베이스 (파서·모델·질의·Neo4j 적재) |
| `capacitykb/` | 리소스 **용량·제약** 지식베이스 (파서·모델·질의·산문 추출) |
| `costkb/` | 인스턴스 **스펙·가격** 지식베이스 (cb-tumblebug 미러 빌드·번들 폴백·조회) |
| `costkb/parsers/dump.py` | `assets.dump.gz`(PostgreSQL custom dump) → `spec_infos` 행 |
| `costkb/parsers/tumblebug.py` | 행 → 레코드 **순수 투영** (미러 충실도 규칙이 여기 있음) |
| `kbcommon/` | 세 KB가 공유하는 다운로드 캐시 |
| `document/` | 요구사항 브리프 + 비전문가용 해설서 + 수동 테스트 질의집 |
| `main.py` | 대화형 루프 진입점 |

## 테스트

```bash
uv run pytest        # 373개, 전부 오프라인 (fixture 기반)
```

`costkb` 테스트는 `tests/conftest.py`가 `output_dir`을 빈 임시 디렉터리로 고정해 **항상 번들
36건**을 봅니다 — 그러지 않으면 `costkb build`를 한 번 돌린 개발자에게만 테스트가 깨집니다.
미러 투영은 `spec_infos` 모양의 행 dict fixture로 `test_costkb_projection.py`가 따로 검증합니다
(덤프 34 MB 다운로드 불필요).

**pytest가 못 잡는 것** — "모델이 실제로 도구를 부르는가"는 자동 검사로 고정하기 어렵습니다.
그건 [`document/kb-test-queries.md`](document/kb-test-queries.md)의 질의집으로 손으로 확인합니다
(축별 스모크 · 교차 축 · 함정 질의 · 체크리스트). **모델을 바꿨다면 여기부터 돌려보세요.**

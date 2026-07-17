"""kb-test-queries.md 전수 검사 하네스.

각 질의를 K회 실행해 (1) 어떤 도구가 어떤 순서로 호출됐는지, (2) 최종 답변이 기대
문자열을 담는지 자동 판정한다. 결과는 JSONL로 증분 기록(중단돼도 보존).

판정은 두 종류:
- 기계적: 기대 도구 존재/금지 도구 부재/순서(계획 게이트) — 확실
- 휴리스틱: 답변 substring — 참고용, semantic은 사람이 최종 확인
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
# repo 루트 = tools/kb_audit/ 에서 두 단계 위
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from agents.exceptions import MaxTurnsExceeded

from nim_agent.agent import build_agent
from nim_agent.session import SessionState
from nim_agent.verbose import run_agent

OUT = Path(__file__).with_name("results-latest.jsonl")
K = int(sys.argv[1]) if len(sys.argv) > 1 else 3

# 각 스펙: id, q(질의), expect(있어야 할 도구), forbid(없어야 할 도구),
# plan_before(이 도구들 앞에 record_plan이 와야 함), want(답변에 있어야 할 substring),
# nogood(답변에 없어야 할 substring)
SPECS = [
    # 1-1 graphkb
    dict(id="1-1a", q="VM을 만들려면 어떤 리소스들이 먼저 필요해?", expect=["kb_creation_order"], forbid=["web_search", "record_plan"]),
    dict(id="1-1b", q="AWS VPC를 지우면 뭐가 영향받아?", expect=["kb_deletion_impact"], forbid=["web_search", "record_plan"]),
    dict(id="1-1c", q="AWS의 VPC가 Azure랑 GCP에선 각각 뭐야?", expect=["kb_equivalent_types"], forbid=["web_search", "record_plan"]),
    dict(id="1-1d", q="GCP ComputeInstance는 정확히 뭘 참조해?", expect=["kb_describe_type"], forbid=["web_search", "record_plan"]),
    dict(id="1-1e", q="AWS에서 rds 들어가는 리소스 타입 찾아줘", expect=["kb_search_types"], forbid=["web_search", "record_plan"]),
    dict(id="1-1f", q="AWS에서 의존성이 가장 큰 리소스 타입 5개는?", expect=["kb_rank_types"], forbid=["web_search"]),
    # 1-2 capacitykb
    dict(id="1-2a", q="EBS 볼륨을 100TB로 만들 수 있어?", expect=["cap_check_value"], forbid=["web_search"]),
    dict(id="1-2b", q="Lambda 함수 MemorySize 제약이 뭐야?", expect=["cap_property_limits"], forbid=["web_search"]),
    dict(id="1-2c", q="서브넷에서 나중에 못 바꾸는 속성이 뭐야?", expect=["cap_immutable_properties"], forbid=["web_search"]),
    dict(id="1-2d", q="ECS 서비스 LaunchType에 뭘 넣을 수 있어?", expect=["cap_allowed_values"], forbid=["web_search"]),
    dict(id="1-2e", q="Azure 가상 네트워크 관련 쿼터 알려줘", expect=["cap_service_quota"], forbid=["web_search"]),
    # 1-3 costkb multi-step (계획 게이트)
    dict(id="1-3", q="GCP에서 vCPU 8개 이상, 메모리 60GiB 이상인 VM을 3대 돌릴 때 월 비용을 계산해줘.",
         expect=["record_plan", "cost_recommend_specs", "cost_estimate_monthly"], forbid=["web_search"],
         plan_before=["cost_recommend_specs", "cost_estimate_monthly"]),
    # 1-4 perfkb
    dict(id="1-4a", q="AWS m5.large 최신 세대야? 성능 특성 알려줘.", expect=["perf_instance_profile"], forbid=["web_search"]),
    dict(id="1-4b", q="AWS m5.large랑 m6i.large 성능 비교해줘.", expect=["perf_compare"], forbid=["web_search"]),
    dict(id="1-4c", q="지속 EBS 대역폭 4000Mbps 이상인 AWS 인스턴스 뭐 있어?", expect=["perf_specs_by_ebs_baseline"], forbid=["web_search"]),
    # 2 cross-axis
    dict(id="2-1", q="RDS를 AWS에 올리려는데, 먼저 필요한 리소스와 나중에 못 바꾸는 속성을 정리해줘.",
         expect=["kb_creation_order", "cap_immutable_properties"], forbid=["web_search"]),
    dict(id="2-2", q="AWS 서브넷이 Azure에선 뭐고, 그 Azure 쪽 서브넷 개수 제한은 얼마야?",
         expect=["kb_equivalent_types", "cap_service_quota"], forbid=["web_search"]),
    dict(id="2-3", q="사용자 1000명 규모 REST API 서버 하나를 AWS에 올리려고 해. 필요한 리소스 생성 순서와 월 비용을 알려줘. 리전은 us-east-1, 단일 인스턴스로 계획 세워서 진행해.",
         expect=["record_plan", "cost_recommend_specs"], forbid=["web_search"], plan_before=["cost_recommend_specs"]),
    # 3 traps
    dict(id="3-1", q="지금 내 계정에 떠 있는 VM 목록 보여줘.", expect=[], forbid=["web_search"],
         want=["없", "MCP", "연결"], soft_want=True),  # 거절/불가 안내
    dict(id="3-2", q="EBS 볼륨을 100TB로 만들 수 있어?", expect=["cap_check_value"], forbid=["web_search"],
         want=["보류", "확인", "문서"], soft_want=True),  # 불확실성 유지
    dict(id="3-3", q="AWS t3.medium 시간당 얼마야?", expect=["cost_recommend_specs"], forbid=["web_search"]),
    dict(id="3-4", q="GCP n2-highmem-8 메모리가 몇 GiB야?", expect=["cost_recommend_specs"], forbid=["web_search"],
         want=["64", "62.5"]),  # 미러값·보정값 둘 다
    dict(id="3-5", q="vCPU 2000개짜리 인스턴스 추천해줘.", expect=["cost_recommend_specs"], forbid=["web_search"]),
    dict(id="3-6", q="AWS의 VPC는 Azure에선 뭐라고 불러?", expect=["kb_equivalent_types"], forbid=["record_plan", "web_search"]),
    dict(id="3-7", q="AWS us-east-1에서 vCPU 2개, 메모리 4GiB 이상인 가장 저렴한 상시 부하용 VM 스펙을 추천해줘. 단일 인스턴스로 계획 세워서 진행해.",
         expect=["record_plan", "cost_recommend_specs"], forbid=["web_search"], plan_before=["cost_recommend_specs"],
         want=["버스트"], soft_want=True),
    dict(id="3-8", q="AWS m5.large랑 Azure Standard_D2s_v3 중에 뭐가 더 빨라?", forbid=["web_search"],
         want=["비교", "불가", "다르"], soft_want=True),  # 프로바이더 간 비교 거부
]


def tool_calls(result) -> list[str]:
    names = []
    for item in getattr(result, "new_items", []):
        if getattr(item, "type", None) == "tool_call_item":
            raw = getattr(item, "raw_item", None)
            names.append(getattr(raw, "name", None) or type(raw).__name__)
    return names


def judge(spec: dict, tools: list[str], answer: str) -> dict:
    tset = set(tools)
    checks = {}
    for t in spec.get("expect", []):
        checks[f"expect:{t}"] = t in tset
    for t in spec.get("forbid", []):
        checks[f"forbid:{t}"] = t not in tset
    for gate in spec.get("plan_before", []):
        if gate in tools and "record_plan" in tools:
            checks[f"plan_before:{gate}"] = tools.index("record_plan") < tools.index(gate)
        elif gate in tools:
            checks[f"plan_before:{gate}"] = False  # 게이트 도구는 불렸는데 계획이 없음
        else:
            checks[f"plan_before:{gate}"] = None  # 게이트 도구 자체가 안 불림(별도 expect가 잡음)
    for w in spec.get("want", []):
        key = f"want:{w}"
        checks[key] = (w in answer)
    # 내부 용어 누출 (전 질의 공통)
    leaks = [t for t in ["kb_creation_order", "cost_recommend_specs", "perf_compare", "record_plan",
                         "cap_check_value", "core::", "aws::AWS::"] if t in answer]
    checks["no_tool_name_leak"] = (len(leaks) == 0)
    hard = [v for k, v in checks.items() if v is not None and not k.startswith("want:")]
    return {"checks": checks, "leaks": leaks, "hard_pass": all(hard)}


async def run_once(agent, spec: dict) -> dict:
    try:
        result = await run_agent(
            agent, [{"role": "user", "content": spec["q"]}],
            verbose=False, context=SessionState(),
        )
        tools = tool_calls(result)
        answer = str(result.final_output or "")
        verdict = judge(spec, tools, answer)
        return {"tools": tools, "answer_len": len(answer), "answer_head": answer[:400],
                **verdict, "error": None}
    except MaxTurnsExceeded:
        return {"tools": ["<MAX_TURNS>"], "error": "MaxTurnsExceeded", "hard_pass": False, "checks": {}, "leaks": []}
    except Exception as exc:  # noqa: BLE001
        return {"tools": [], "error": f"{type(exc).__name__}: {exc}", "hard_pass": False, "checks": {}, "leaks": []}


async def main() -> None:
    agent = build_agent()
    OUT.write_text("", encoding="utf-8")
    total = len(SPECS) * K
    done = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for spec in SPECS:
            for run_i in range(K):
                rec = await run_once(agent, spec)
                rec = {"id": spec["id"], "run": run_i, **rec}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                done += 1
                mark = "OK " if rec.get("hard_pass") else "FAIL"
                print(f"[{done:3}/{total}] {spec['id']} run{run_i} {mark} tools={rec.get('tools')}")
                time.sleep(1.0)  # NIM 레이트리밋 완화
    print("완료:", OUT)


asyncio.run(main())

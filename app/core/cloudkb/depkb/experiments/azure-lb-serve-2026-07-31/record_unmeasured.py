"""LB 서빙 셀을 미판정으로 기록한다 — 기준선을 못 세웠다.

이 셀은 판정에 들어가지 않는다. 대신 (a) 왜 못 쟀는지 (b) 그 과정에서
실측된 구성 제약 (c) 다음 시도의 조건을 남긴다.

실행: `python record_unmeasured.py`
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

UNMEASURED = (
    "**LB 서빙 신호는 미판정이다.** 기준선(LB 프론트엔드로 HTTP 200)을 끝내 "
    "못 세웠다. 확인한 것: 백엔드 리스너 정상(게스트 로컬 200) · 풀 등록 1 · "
    "LB 규칙의 fe/be/probe 연결 정상 · NSG 22/80 허용. 시도한 구성 넷이 전부 "
    "실패했다: (1) 인스턴스 PIP 유지 (2) PIP 제거 (3) PIP 제거+아웃바운드 "
    "규칙 (4) +disableOutboundSNAT. 남은 원인 후보: LB 헬스 프로브 실패"
    "(azure는 백엔드 헬스를 CLI로 노출하지 않아 확인하지 못했다) · PIP 제거 "
    "후 http.server의 생사 미확인(관리 SSH 경로를 잃었다). "
    "**다음 시도의 조건**: 관리 접근을 LB 인바운드 NAT 규칙으로 분리해 PIP "
    "없이도 게스트를 볼 것 · 프로브 상태를 Azure Monitor 메트릭으로 볼 것."
)

CONSTRAINTS = (
    "셀은 미판정이지만 **azure Standard LB의 구성 제약 둘을 실측**했다"
    "(존재 의존이 아니라 구성 제약 — 계획층이 알아야 하는 것): "
    "(1) 인스턴스 레벨 PIP가 붙은 NIC는 아웃바운드 규칙이 있는 풀을 참조할 수 "
    "없다(NicWithPublicIpCannotReferencePoolWithOutboundRule). "
    "(2) 같은 프론트엔드를 LB 규칙과 아웃바운드 규칙이 공유하려면 LB 규칙의 "
    "disableOutboundSNAT가 true여야 한다"
    "(LoadBalancingRuleMustDisableSNATSinceSameFrontendIPConfiguration…). "
    "둘 다 서버가 이름으로 말한 제약이다."
)


def main() -> None:
    path = HERE / "results.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["steps"]["Z2.unmeasured-final"] = {
        "ok": False, "errorCodes": ["BASELINE_UNREACHED"], "excerpt": UNMEASURED}
    doc["steps"]["Z3.config-constraints-observed"] = {
        "ok": True, "errorCodes": [], "excerpt": CONSTRAINTS}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print("recorded as unmeasured")


if __name__ == "__main__":
    main()

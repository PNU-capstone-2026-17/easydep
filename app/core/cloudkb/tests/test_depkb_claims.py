"""주장 산출물(claims.json)의 구조적 불변식.

빌드 자체가 인용 코드를 실험 실측과 대조해 죽지만(판정-측정 정합), 여기서는
산출물이 그 빌드의 사영으로 남아 있는가와 판정 규율(통과·침묵은 증거가 아니다)을
지킨다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.depkb import build_claims

_ARTIFACT = Path(build_claims.__file__).resolve().parent / "claims.json"


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(_ARTIFACT.read_text(encoding="utf-8"))


def test_claims_are_recomputable(artifact) -> None:
    """산출물은 빌드의 사영이다 — 손대려면 판정표(우리 구성)를 고쳐라."""
    assert artifact == build_claims.build()


def test_verdicts_require_dynamic_evidence(artifact) -> None:
    """required/optional/holds는 동적 층(preflight 거부·apply) 증거 없이 못 선다.

    스키마 후보만으로 판정이 서면 그건 '참조가 있다'를 '필요하다'로 승격한 것 —
    이 저장소가 세 번 저지른 실패의 재발이다.
    """
    for c in artifact["claims"]:
        if c["verdict"] == "unknown":
            continue
        dynamic = [e for e in c["evidence"] if e["layer"] in ("preflight", "apply")]
        assert dynamic, f"{c['csp']} {c['subject']}→{c['object']}: 동적 증거 없는 판정"


def test_unknown_is_recorded_not_hidden(artifact) -> None:
    """unknown에는 사유가 붙는다 — 빈칸이 아니라 '미실행'의 기록이다."""
    for c in artifact["claims"]:
        if c["verdict"] == "unknown":
            assert c["note"], "unknown에 사유가 없다"


def test_aws_core_and_the_key_story_closure(artifact) -> None:
    """aws 검증핵 — 그리고 sshKey 이야기의 완결.

    같은 DryRun 성공 하나가 서브넷·키 둘 다의 생략 가능을 증명한다(기본 VPC
    대체 실측). CB의 'sshKey 필수'는 이제 3사 전부에서 도구의 요구로 확정됐다 —
    aws 선택(동적) · azure 무참조(스키마) · gcp 자원 부재(스키마).
    aws nic→subnet의 required는 클라이언트 층 거부라는 한계가 note에 있다.
    """
    got = {(c["subject"], c["object"], c["question"]): c["verdict"]
           for c in artifact["claims"] if c["csp"] == "aws"
           and c["verdict"] != "unknown"}
    assert got == {
        ("nic", "subnet", "existence"): "required",
        ("nic", "subnet", "lifecycle"): "holds",
        ("nic", "firewall", "existence"): "optional",
        ("nic", "firewall", "lifecycle"): "holds",
        ("subnet", "network", "existence"): "required",
        ("subnet", "network", "lifecycle"): "holds",
        ("firewall", "network", "existence"): "optional",
        ("vm", "subnet", "existence"): "optional",
        ("vm", "sshKey", "existence"): "optional",
        ("vm", "nic", "existence"): "optional",
        ("vm", "firewall", "existence"): "optional",
        ("vm", "disk", "existence"): "optional",
        # image 라운드(2026-07-31): 3사 중 유일한 required — 기존 볼륨 부팅
        # 경로가 없다. 생략 거부가 서버층(MissingParameter)이다.
        ("vm", "image", "existence"): "required",
        ("loadBalancer", "subnet", "existence"): "required",
        ("loadBalancer", "firewall", "existence"): "optional",
        ("k8sCluster", "subnet", "existence"): "required",
        ("k8sCluster", "subnet", "lifecycle"): "holds",
        ("k8sCluster", "network", "lifecycle"): "holds",
        ("k8sCluster", "firewall", "existence"): "optional",
        # k8s 층 합성(2026-07-31): CLB·SG 합성이 노드 0에서도 — 삭제는 동반 정리.
        # k8sPvc→disk는 완결 라운드에서 닫혔다(IRSA 전제까지 갖춰 Bound).
        ("k8sPvc", "disk", "existence"): "optional",
        ("k8sPvc", "disk", "lifecycle"): "holds",
        ("k8sService", "loadBalancer", "existence"): "optional",
        ("k8sService", "loadBalancer", "lifecycle"): "holds",
        # 합성 2라운드: 기본 구성에서 Ingress 컨트롤러 부재 — 합성 없음.
        ("k8sIngress", "loadBalancer", "existence"): "optional",
        # 기능 의존 첫 라운드: EIP 분리 무방비 + TCP 도달성 상실·회복 실측.
        ("vm", "publicIp", "function"): "holds",
        # 기능 2라운드: SG 교체(관계 변이)·IGW 라우트 삭제 — 둘 다 무방비.
        ("vm", "firewall", "function"): "holds",
        ("subnet", "internetGateway", "function"): "holds",
        # VPN 라운드: 게이트웨이는 VPC 없이 서고 **attach**가 VPC를 요구한다.
        ("vpn", "network", "existence"): "required",
        ("vpn", "network", "lifecycle"): "holds",
        # 신호 4종 라운드: 메타데이터(IMDS 자격증명)·아웃바운드.
        # vm→iamRole은 **존재 optional인데 기능은 결속**이다.
        ("vm", "iamRole", "function"): "holds",
        # iamRole 라운드: EKS 거부 관측의 승격 + 인스턴스 프로필 생략 성공.
        ("k8sCluster", "iamRole", "existence"): "required",
        ("vm", "iamRole", "existence"): "optional",
        # customImage 라운드: AMI의 원본은 **인스턴스**다(3사 중 유일).
        ("customImage", "vm", "existence"): "required",
        ("vm", "customImage", "existence"): "optional",
        # dns·fs 라운드: 사설 영역은 VPC 필수(aws 고유) · EFS는 접속점만
        # 서브넷을 요구한다(저장소 자체는 네트워크 무관).
        ("globalDns", "globalDnsRecord", "existence"): "required",
        ("globalDns", "globalDnsRecord", "lifecycle"): "holds",
        ("globalDns", "network", "existence"): "required",
        ("fileSystem", "subnet", "existence"): "required",
        ("fileSystem", "subnet", "lifecycle"): "holds",
    }
    key_claim = next(c for c in artifact["claims"] if c["csp"] == "aws"
                     and (c["subject"], c["object"]) == ("vm", "sshKey"))
    assert key_claim["verdict"] == "optional"


def test_no_unknown_remains_in_the_vocabulary(artifact) -> None:
    """3사 × 어휘 전체가 판정됐다(2026-07-31) — unknown이 다시 생기면
    스키마 층에 새 후보가 들어온 것이니 실험을 따라 붙여야 한다."""
    unknown = [(c["csp"], c["subject"], c["object"])
               for c in artifact["claims"] if c["verdict"] == "unknown"]
    assert not unknown, f"판정 없는 주장이 생겼다: {unknown}"


def test_k8s_nodegroup_modality_flips(artifact) -> None:
    """k8sCluster→k8sNodeGroup: azure 필수 ↔ gcp 선택(서버가 default-pool 합성).
    세 번째 양상 반전이자, CNA 층에서도 중립 플래그가 못 서는 증거다."""
    by_csp = {c["csp"]: c["verdict"] for c in artifact["claims"]
              if (c["subject"], c["object"], c["question"])
              == ("k8sCluster", "k8sNodeGroup", "existence")}
    assert by_csp == {"azure": "required", "gcp": "optional"}


def test_k8s_subnet_flips_between_managed_synthesis_and_user_placement(artifact) -> None:
    """k8sCluster→subnet: azure는 서비스가 vnet을 합성해 선택 ↔ aws는 서로 다른
    AZ의 서브넷 2개를 사용자에게 요구. 같은 CNA 자원의 정반대 계약이다."""
    rows = {c["csp"]: c for c in artifact["claims"]
            if (c["subject"], c["object"], c["question"])
            == ("k8sCluster", "subnet", "existence")}
    assert rows["azure"]["verdict"] == "optional"
    assert rows["aws"]["verdict"] == "required"
    assert "다른 AZ" in (rows["aws"]["predicate"] or "")


def test_vm_nic_modality_flips_across_csps(artifact) -> None:
    """vm→nic: azure 필수 · gcp 필수 · aws 선택(서버 ENI 암묵) — 두 번째 양상
    반전. vm→disk(gcp만 필수)와 함께, '벤더 중립 필수 플래그 하나'로는 이
    지식을 표현할 수 없다는 논거의 기둥이다."""
    by_csp = {c["csp"]: c["verdict"] for c in artifact["claims"]
              if (c["subject"], c["object"], c["question"])
              == ("vm", "nic", "existence")}
    assert by_csp == {"azure": "required", "gcp": "required", "aws": "optional"}


def test_gcp_core_and_the_modality_flip(artifact) -> None:
    """gcp 검증핵 — 그리고 첫 CSP 양상 반전: vm→disk.

    azure는 OS 디스크를 서버가 합성해 **선택**, gcp는 부트 디스크 명세가
    **필수**다. 같은 간선의 필연이 CSP에 따라 뒤집힌다는 것이 CSP 색인 주장
    형식이 필요한 이유고, 이 반전이 그 첫 실측이다.
    """
    got = {(c["subject"], c["object"], c["question"]): c["verdict"]
           for c in artifact["claims"] if c["csp"] == "gcp"
           and c["verdict"] != "unknown"}
    assert got == {
        ("subnet", "network", "existence"): "required",
        ("subnet", "network", "lifecycle"): "holds",
        ("firewall", "network", "existence"): "optional",
        ("vm", "nic", "existence"): "required",
        ("vm", "disk", "existence"): "required",
        ("vm", "disk", "lifecycle"): "holds",
        # image 라운드(2026-07-31): 'Boot disk must have a source specified' —
        # sourceImage ∨ 기존 디스크의 선언 술어.
        ("vm", "image", "existence"): "optional",
        ("nic", "network", "existence"): "optional",
        ("nic", "subnet", "existence"): "optional",
        ("nic", "subnet", "lifecycle"): "holds",
        ("loadBalancer", "network", "existence"): "optional",
        ("loadBalancer", "subnet", "existence"): "optional",
        ("k8sCluster", "network", "existence"): "optional",
        ("k8sCluster", "subnet", "existence"): "optional",
        ("k8sCluster", "subnet", "lifecycle"): "holds",
        ("k8sCluster", "network", "lifecycle"): "holds",
        ("k8sCluster", "k8sNodeGroup", "existence"): "optional",
        # k8s 층 합성(2026-07-31): LB는 성좌(FR+targetPool+방화벽)로 합성된다.
        ("k8sService", "loadBalancer", "existence"): "optional",
        ("k8sService", "loadBalancer", "lifecycle"): "holds",
        ("k8sPvc", "disk", "existence"): "optional",
        ("k8sPvc", "disk", "lifecycle"): "holds",
        # 합성 2라운드: 내장 컨트롤러의 **전역** HTTP LB 성좌 — 유일한 합성 CSP.
        ("k8sIngress", "loadBalancer", "existence"): "optional",
        ("k8sIngress", "loadBalancer", "lifecycle"): "holds",
        # 기능 의존 첫 라운드: accessConfig 삭제 무방비(재부여는 새 임시 IP).
        ("vm", "publicIp", "function"): "holds",
        # 기능 2라운드: 규칙 삭제(관계 변이 부재)·기본 라우트 삭제 — 무방비.
        ("vm", "firewall", "function"): "holds",
        ("network", "internetGateway", "function"): "holds",
        # VPN 라운드: network 필드 필수 — vpn 어휘가 3사 완결됐다.
        ("vpn", "network", "existence"): "required",
        ("vpn", "network", "lifecycle"): "holds",
        # 신호 6: 서비스 디스커버리 — Pod는 Running인데 이름만 죽는다.
        ("k8sService", "k8sCluster", "function"): "holds",
        # iamRole 라운드: serviceAccounts null 실물 — 서버가 안 붙인다.
        ("vm", "iamRole", "existence"): "optional",
        # customImage 라운드: 원본은 디스크(aws와 반전), 결속은 없다.
        ("customImage", "disk", "existence"): "required",
        ("vm", "customImage", "existence"): "optional",
        # dns·fs 라운드: Filestore는 파일시스템 자체가 네트워크를 요구한다
        # (aws와 층이 다르다). 거부 층까지만 측정.
        ("globalDns", "globalDnsRecord", "existence"): "required",
        ("globalDns", "globalDnsRecord", "lifecycle"): "holds",
        ("fileSystem", "network", "existence"): "required",
    }
    azure = {(c["subject"], c["object"], c["question"]): c["verdict"]
             for c in artifact["claims"] if c["csp"] == "azure"}
    assert azure[("vm", "disk", "existence")] == "optional"
    assert got[("vm", "disk", "existence")] == "required"


def test_the_verified_azure_core_holds(artifact) -> None:
    """azure 검증핵 — 바뀌면 실험 기록이 바뀐 것이니 사람이 봐야 한다."""
    got = {(c["subject"], c["object"], c["question"]): c["verdict"]
           for c in artifact["claims"] if c["csp"] == "azure"
           and c["verdict"] != "unknown"}
    assert got == {
        ("nic", "subnet", "existence"): "required",
        ("nic", "subnet", "lifecycle"): "holds",
        ("vm", "nic", "existence"): "required",
        ("subnet", "network", "existence"): "required",
        ("subnet", "network", "lifecycle"): "holds",
        ("nic", "firewall", "existence"): "optional",
        ("nic", "firewall", "lifecycle"): "holds",
        ("nic", "publicIp", "existence"): "optional",
        ("nic", "publicIp", "lifecycle"): "holds",
        ("vm", "disk", "existence"): "optional",
        ("vm", "disk", "lifecycle"): "holds",
        # image 라운드(2026-07-31): imageReference ∨ 기존 OS 디스크 attach —
        # 잔존 OS 디스크로 이미지 없이 VM이 선다(B1 실측).
        ("vm", "image", "existence"): "optional",
        ("network", "subnet", "existence"): "optional",
        ("subnet", "firewall", "existence"): "optional",
        ("loadBalancer", "subnet", "existence"): "optional",
        ("loadBalancer", "publicIp", "existence"): "optional",
        ("loadBalancer", "subnet|publicIp|publicIPPrefix", "existence"): "required",
        ("k8sCluster", "k8sNodeGroup", "existence"): "required",
        ("k8sCluster", "subnet", "existence"): "optional",
        ("k8sCluster", "subnet", "lifecycle"): "holds",
        ("k8sCluster", "network", "lifecycle"): "holds",
        ("vpn", "subnet", "existence"): "required",
        ("vpn", "subnet", "lifecycle"): "holds",
        ("vpn", "publicIp", "existence"): "required",
        ("vpn", "publicIp", "lifecycle"): "holds",
        # k8s 층 합성(2026-07-31): 상시 LB에 규칙 합성(gcp 성좌 신설과 꼴이
        # 다르다) · CSI 디스크는 첫 소비자 시점(WaitForFirstConsumer 실측).
        ("k8sService", "loadBalancer", "existence"): "optional",
        ("k8sService", "loadBalancer", "lifecycle"): "holds",
        ("k8sPvc", "disk", "existence"): "optional",
        ("k8sPvc", "disk", "lifecycle"): "holds",
        # 합성 2라운드: 기본 구성에서 Ingress 컨트롤러 부재 — 합성 없음.
        ("k8sIngress", "loadBalancer", "existence"): "optional",
        # 기능 의존 첫 라운드: 한 쌍(nic→publicIp)에서 세 질문 전부 판정.
        ("nic", "publicIp", "function"): "holds",
        # 기능 2라운드: 서브넷-NSG 분리 무방비(secure-by-default와 합성 효과).
        # 라우팅 셀은 azure에 대응 자원이 없다 — 간선 부재가 기록이다.
        ("subnet", "firewall", "function"): "holds",
        # 신호 4종 라운드: DNS 해석·볼륨 I/O(게스트 안에서 관측).
        ("globalDns", "network", "function"): "holds",
        ("vm", "disk", "function"): "holds",
        # 신호 5(2026-08-01): **존재 판정에 없는 간선을 기능이 열었다.**
        ("loadBalancer", "vm", "function"): "holds",
        # iamRole 라운드: managed identity 미지정 생성 성공.
        ("vm", "iamRole", "existence"): "optional",
        # customImage 라운드: 원본은 디스크, 결속은 없다(graphkb 관측과 갈림).
        ("customImage", "disk", "existence"): "required",
        ("vm", "customImage", "existence"): "optional",
        # dns·fs 라운드: 파일 공유는 네트워크가 아니라 스토리지 계정 밑이다.
        # DNS 생명주기는 **없다** — 레코드가 있어도 영역이 지워진다(반전).
        ("globalDns", "globalDnsRecord", "existence"): "required",
        ("fileSystem", "storageAccount", "existence"): "required",
        # 완결 라운드: RWX는 CSI가 계정+공유를 합성(RP 미등록이 2R 실패
        # 원인이었다) · AKS는 identity를 서버가 합성(EKS required와 반전).
        ("k8sPvc", "fileSystem", "existence"): "optional",
        ("k8sPvc", "fileSystem", "lifecycle"): "holds",
        ("k8sCluster", "iamRole", "existence"): "optional",
    }


def test_vm_image_flips_between_disjunctive_and_required(artifact) -> None:
    """vm→image(2026-07-31): azure·gcp는 선언 술어(이미지 ∨ 기존 디스크)로
    선택, aws는 기존 볼륨 부팅 경로가 없어 필수 — 네 번째 양상 반전이고,
    외부 대조 오류 셋(sshKey·spec/image·azure 방향) 중 spec/image가 닫혔다.
    aws의 CFN Required:False는 위치 플래그(LaunchTemplate)임이 note에 있다."""
    rows = {c["csp"]: c for c in artifact["claims"]
            if (c["subject"], c["object"], c["question"])
            == ("vm", "image", "existence")}
    assert rows["aws"]["verdict"] == "required"
    assert rows["azure"]["verdict"] == "optional"
    assert rows["gcp"]["verdict"] == "optional"
    for csp in ("azure", "gcp"):
        assert rows[csp]["predicate"].startswith("disjunctive:"), csp


def test_dns_zone_deletion_flips_and_filesystem_anchors_differ(artifact) -> None:
    """dns·fs 라운드(2026-07-31) 둘:
    ① 레코드가 남은 영역의 삭제가 gcp·aws는 거부인데 **azure는 성공**한다 —
    azure에는 이 생명주기 행이 아예 없다(없는 결속을 적지 않는다).
    ② fileSystem이 무엇에 매달리는지가 3사 3색 — aws는 서브넷(접속점 경유),
    gcp는 네트워크(저장소 자체), azure는 스토리지 계정(경로 중첩)."""
    life = {c["csp"] for c in artifact["claims"]
            if (c["subject"], c["object"], c["question"])
            == ("globalDns", "globalDnsRecord", "lifecycle")}
    assert life == {"gcp", "aws"}, f"azure는 결속이 없어야 한다: {life}"
    anchors = {c["csp"]: c["object"] for c in artifact["claims"]
               if c["subject"] == "fileSystem" and c["question"] == "existence"}
    assert anchors == {"aws": "subnet", "gcp": "network",
                       "azure": "storageAccount"}


def test_custom_image_holds_no_source_and_flips_its_source_kind(artifact) -> None:
    """customImage(2026-07-31) 둘: ① 3사 모두 이미지가 원본을 붙잡지 않는다
    — 생명주기 행 자체가 없다(없는 결속을 적지 않는다). graphkb의
    node→customImage 생명주기 관측과 컨트롤 플레인이 갈린 자리다.
    ② 원본의 **종류**가 갈린다 — azure·gcp는 디스크, aws는 인스턴스."""
    life = [c for c in artifact["claims"] if c["question"] == "lifecycle"
            and "customImage" in (c["subject"], c["object"])]
    assert not life, f"결속 없음이 실측인데 생명주기 행이 생겼다: {life}"
    sources = {c["csp"]: c["object"] for c in artifact["claims"]
               if c["subject"] == "customImage" and c["question"] == "existence"}
    assert sources == {"azure": "disk", "gcp": "disk", "aws": "vm"}


def test_function_is_a_third_question_axis(artifact) -> None:
    """기능 의존(2026-07-31): 존재·생명주기가 거부 코드로 잰 것과 달리
    컨트롤 플레인이 막지 않는(무방비) 지대를 기능 신호(TCP 도달성)로 쟀다.
    azure nic→publicIp는 세 질문이 전부 판정된 첫 쌍 — 존재 optional(선택) ·
    생명주기 holds(붙어 있으면 삭제 거부) · 기능 holds(떼면 도달성 상실,
    막지 않음). 한 필드였다면 이 셋은 표현 자체가 불가능했다."""
    az = {c["question"]: c["verdict"] for c in artifact["claims"]
          if c["csp"] == "azure"
          and (c["subject"], c["object"]) == ("nic", "publicIp")}
    assert az == {"existence": "optional", "lifecycle": "holds",
                  "function": "holds"}
    for csp, subj in (("gcp", "vm"), ("aws", "vm")):
        row = next(c for c in artifact["claims"] if c["csp"] == csp
                   and (c["subject"], c["object"], c["question"])
                   == (subj, "publicIp", "function"))
        assert row["verdict"] == "holds"
        assert row["predicate"].startswith("무방비:"), csp


def test_optional_and_lifecycle_are_independent(artifact) -> None:
    """nic→firewall: 생성엔 선택인데 붙어 있으면 삭제가 막힌다 — 질문 둘을 한
    필드에 눌렀다면 이 사실은 표현 자체가 불가능했다. 유형 분리의 존재 증명."""
    nf = {c["question"]: c["verdict"] for c in artifact["claims"]
          if c["csp"] == "azure" and (c["subject"], c["object"]) == ("nic", "firewall")}
    assert nf == {"existence": "optional", "lifecycle": "holds"}

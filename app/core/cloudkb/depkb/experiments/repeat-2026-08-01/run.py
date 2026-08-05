"""반복 측정 — **단일 실행 위협**을 재는 라운드.

## 왜 있나

118주장이 각각 **한 번 돌린 결과**다. 반복이 0회라 "이 코드가 항상 나오는가"에
답할 것이 없었고, 여정 문서에 위협으로만 적혀 있었다. 여기서 그 일부를 닫는다.

## 무엇을 반복하나 — 거부 프로브만

**거부는 자원을 만들지 않는다.** 그래서 몇 번을 돌려도 과금이 없고, 마침 우리
`required` 판정의 증거가 대부분 거부 코드다. 성공 프로브(=`optional` 판정의
증거)는 자원을 만들므로 여기서 반복하지 않는다 — **그쪽은 여전히 단일 실행이고,
그 사실을 결과에 적는다.**

3사에서 같은 종류를 고른다:

    azure   ARM 템플릿 검증(`deployment group validate`) — 필수 참조 생략
    aws     EC2 DryRun — 필수 인자 생략
    gcp     `--dry-run`이 없어 **실제 생성 요청**을 보낸다. 거부되므로 자원은
            안 생기지만, 수락되면 생기므로 거부가 확실한 것만 고른다

## 무엇이 결과인가

프로브마다 **K회 중 같은 코드가 몇 번 나왔는가**. 전부 같으면 그 주장의 증거는
반복 가능하고, 갈리면 그 주장을 다시 봐야 한다. **갈리는 것을 찾는 것이 목적**
이므로 "전부 같았다"도 결과다.

실행: `python run.py [반복수]`  (기본 3)
"""

import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: 거부 코드 추출. 세 CSP의 꼴이 달라 셋을 함께 본다.
#:
#: **gcp는 코드가 아니라 문장으로 답한다** — `Invalid value for field 'X'`.
#: 1차에서 이걸 못 잡아 `FAIL`로 셌는데, 그러면 "무엇이 갈렸는가"를 볼 수 없다.
#: 필드 이름까지 잡아야 같은 거부인지 아닌지가 구별된다.
_CODE = re.compile(r'"code":\s*"([^"]+)"|\(([A-Za-z]+[A-Za-z0-9]*)\)'
                   r"|([A-Za-z]+\.[A-Za-z]+Exception)"
                   r"|Invalid value for field '([^']+)'")
RG = "depkb-repeat"
LOC = "koreacentral"


def _run(exe: str, args: list[str], timeout: int = 300) -> dict:
    path = shutil.which(exe)
    if not path:
        return {"ok": False, "errorCodes": ["NO_CLI"], "excerpt": f"{exe} 없음"}
    r = subprocess.run([path, *args], capture_output=True, text=True,
                       timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    return {"ok": r.returncode == 0,
            "errorCodes": list(dict.fromkeys(codes)),
            "excerpt": text.strip().replace("\r", "")[:400]}


def az(args, **kw):
    return _run("az", [*args, "--only-show-errors"], **kw)


def aws(args, **kw):
    return _run("aws", args, **kw)


def gcloud(args, **kw):
    return _run("gcloud", args, **kw)


#: 반복할 프로브. **전부 거부가 기대되는 것**이라 자원이 생기지 않는다.
#: 각 항목은 (이름, 대응 주장, 호출).
def probes(template: Path) -> list[tuple[str, str, callable]]:
    return [
        # azure — ARM 검증. 필수 참조를 뺀 템플릿을 검증에 건다.
        ("azure.nic-without-subnet", "azure nic→subnet existence=required",
         lambda: az(["deployment", "group", "validate", "-g", RG,
                     "--template-file", str(template / "nic-no-subnet.json"),
                     "-o", "json"])),
        ("azure.lb-frontend-empty",
         "azure loadBalancer→subnet|publicIp|publicIPPrefix existence=required",
         lambda: az(["deployment", "group", "validate", "-g", RG,
                     "--template-file", str(template / "lb-empty-frontend.json"),
                     "-o", "json"])),
        ("azure.lb-frontend-both",
         "azure loadBalancer 선언 술어의 **배타성**(2026-08-01 신규)",
         lambda: az(["deployment", "group", "validate", "-g", RG,
                     "--template-file", str(template / "lb-both.json"),
                     "-o", "json"])),
        # aws — DryRun. 필수 인자를 빼면 거부된다.
        ("aws.runinstances-no-image", "aws vm→image existence=required",
         # **리전을 명시한다.** 1차에서 `NoRegion`이 나왔는데 그건 CLI 설정
         # 오류이지 API의 거부가 아니다 — 오라클이 아닌 것을 셀 뻔했다.
         lambda: aws(["ec2", "run-instances", "--dry-run", "--region",
                      "ap-northeast-2", "--instance-type", "t3.micro",
                      "--output", "json"])),
        # gcp — dry-run이 없다. 존재하지 않는 서브넷을 참조해 거부를 받는다.
        ("gcp.instance-dangling-subnet", "gcp nic→subnet (custom 모드 조건부)",
         lambda: gcloud(["compute", "instances", "create", "depkb-repeat-probe",
                         "--zone", "asia-northeast3-a",
                         "--subnet", "depkb-no-such-subnet-xyz",
                         "--format", "json", "--quiet"])),
    ]


_TEMPLATES = {
    "nic-no-subnet.json": {
        "type": "Microsoft.Network/networkInterfaces",
        "apiVersion": "2023-09-01", "name": "d-nic", "location": LOC,
        "properties": {"ipConfigurations": [
            {"name": "ipcfg", "properties": {"privateIPAllocationMethod": "Dynamic"}}]},
    },
    "lb-empty-frontend.json": {
        "type": "Microsoft.Network/loadBalancers",
        "apiVersion": "2023-09-01", "name": "d-lb", "location": LOC,
        "sku": {"name": "Standard"},
        "properties": {"frontendIPConfigurations": [
            {"name": "fe", "properties": {}}]},
    },
    "lb-both.json": {
        "type": "Microsoft.Network/loadBalancers",
        "apiVersion": "2023-09-01", "name": "d-lb2", "location": LOC,
        "sku": {"name": "Standard"},
        "properties": {"frontendIPConfigurations": [{"name": "fe", "properties": {
            "subnet": {"id": "[resourceId('Microsoft.Network/virtualNetworks/"
                             "subnets','d-vnet','d-subnet')]"},
            "publicIPAddress": {"id": "[resourceId("
                                      "'Microsoft.Network/publicIPAddresses','d-pip')]"},
        }}]},
    },
}


def write_templates(into: Path) -> Path:
    into.mkdir(exist_ok=True)
    for name, resource in _TEMPLATES.items():
        (into / name).write_text(json.dumps({
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/"
                       "deploymentTemplate.json#",
            "contentVersion": "1.0.0.0", "resources": [resource],
        }), encoding="utf-8")
    return into


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    doc = {"_note": ("반복 측정 — 거부 프로브를 K회 돌려 코드가 같은지 본다. "
                     "**성공 프로브는 자원을 만들어 반복하지 않았다**(여전히 "
                     "단일 실행). 갈리는 것을 찾는 것이 목적이라 '전부 같았다'도 "
                     "결과다."),
           "repeats": repeats,
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}, "agreement": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    templates = write_templates(HERE / "templates")
    steps["S0.create-rg"] = az(["group", "create", "-n", RG, "-l", LOC])
    save()

    for name, claim, call in probes(templates):
        seen: list[tuple[str, ...]] = []
        for i in range(1, repeats + 1):
            result = call()
            key = tuple(result["errorCodes"]) or ("ok" if result["ok"] else "FAIL",)
            seen.append(key)
            steps[f"{name}#{i}"] = result
            save()
            print(f"{name}#{i:<2} {'/'.join(key)}", flush=True)
        tally = Counter(seen)
        top, hits = tally.most_common(1)[0]
        doc["agreement"][name] = {
            "claim": claim, "repeats": repeats,
            "codes": ["/".join(k) for k in seen],
            "agreed": hits, "stable": hits == repeats,
            "modal": "/".join(top),
        }
        save()
        print(f"  → {name}: {hits}/{repeats} 일치 ({'/'.join(top)})", flush=True)

    steps["T1.delete-rg"] = az(["group", "delete", "-n", RG, "--yes", "--no-wait"])
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()
    unstable = [k for k, v in doc["agreement"].items() if not v["stable"]]
    print(f"\n반복 {repeats}회 · 프로브 {len(doc['agreement'])} · "
          f"흔들린 것 {len(unstable)} {unstable}")


if __name__ == "__main__":
    main()

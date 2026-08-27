"""게스트 실행 하네스 — 기능 신호를 VM **안에서** 관측한다.

계획: `document/archive/functional-signals4-plan-2026-07-31.md`.
기능 축 1·2라운드의 신호는 외부 TCP 도달성 하나였다(위협 ④). DNS 해석·
볼륨 I/O·아웃바운드·메타데이터는 전부 게스트 안에서만 보이므로 SSH로 명령을
실행한다.

## 규율

- **OS 기본 도구만 쓴다** — `getent`·`dd`·`curl`·`mount`. 설치가 필요하면
  그건 앱이고 "앱 없이"라는 이 라운드의 전제가 깨진다.
- **기준선을 먼저 세운다** — 변이 전에 명령이 도는 것을 확인하지 않으면
  실패가 '의존 상실'인지 '접속 불가'인지 갈리지 않는다.
- 접속 자체의 실패(`SSH_UNREACHABLE`)와 명령의 실패(종료 코드)를 **다른
  코드로** 기록한다. 섞으면 하네스 결함이 판정으로 둔갑한다.

실험 스크립트가 import해서 쓴다(단독 실행 대상 아님).
"""

from __future__ import annotations

import shutil
import subprocess

SSH = shutil.which("ssh")
_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
]


def run(ip: str, user: str, key: str, command: str,
        timeout: int = 60) -> dict:
    """게스트에서 명령 하나를 돌린다.

    Returns: 다른 실험 스텝과 같은 모양(ok·errorCodes·excerpt) + rc·stdout.
    접속 실패는 `SSH_UNREACHABLE`, 명령 실패는 `EXIT_<rc>`로 갈라 적는다.
    """
    if SSH is None:
        return {"ok": False, "errorCodes": ["NO_SSH_CLIENT"], "excerpt": ""}
    proc = subprocess.run(
        [SSH, *_OPTS, "-i", key, f"{user}@{ip}", command],
        capture_output=True, text=True, timeout=timeout, check=False)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # ssh 자체가 못 붙으면 255를 내고 stdout이 비어 있다.
    if proc.returncode == 255:
        return {"ok": False, "errorCodes": ["SSH_UNREACHABLE"],
                "rc": 255, "stdout": "", "excerpt": err[:400]}
    return {"ok": proc.returncode == 0,
            "errorCodes": [] if proc.returncode == 0 else [f"EXIT_{proc.returncode}"],
            "rc": proc.returncode, "stdout": out,
            "excerpt": (out + ("\n" + err if err else ""))[:500]}


def probe(ip: str, user: str, key: str, command: str, want_ok: bool,
          budget: int, confirm: int = 1, interval: int = 10) -> dict:
    """명령의 성공/실패가 want_ok가 될 때까지 재시도(연속 confirm회).

    기능 신호의 상실·회복 관측에 쓴다. 시한 내 도달 못 하면 사실대로 적는다
    — 시한 초과는 '결속 없음'이 아니라 미판정이다.
    """
    import time

    deadline = time.time() + budget
    tries = streak = 0
    last: dict = {}
    while time.time() < deadline:
        tries += 1
        last = run(ip, user, key, command)
        got = last["ok"]
        streak = streak + 1 if got == want_ok else 0
        print(f"guest[{command[:28]}] ok={got} want={want_ok} "
              f"streak={streak}", flush=True)
        if streak >= confirm:
            return {"ok": True, "errorCodes": [],
                    "excerpt": f"want_ok={want_ok} 도달 (시도 {tries}, "
                               f"연속 {streak}, rc={last.get('rc')}) "
                               f"out={last.get('stdout','')[:120]}"}
        time.sleep(interval)
    return {"ok": False, "errorCodes": ["PROBE_TIMEOUT"],
            "excerpt": f"{budget}초 내 want_ok={want_ok}×{confirm} 미도달 — "
                       f"마지막 rc={last.get('rc')} {last.get('excerpt','')[:200]}"}

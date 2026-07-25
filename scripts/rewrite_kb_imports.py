"""agent-sdk 패키지의 절대 임포트를 새 배치(app/deployment/)로 옮긴다.

병합 계획 3단계 도구 — `docs/agent-sdk-merge-plan.md` 참고.

**왜 정규식이 아니라 AST인가.** 패키지 이름과 똑같은 문자열이 코드에 75건 더 있고,
대부분은 임포트가 아니라 사용자에게 보이는 **근거 라벨**이다:

    notes.append(Note(text, ORIGIN_KB, "costkb"))     # 답변에 실리는 출처 표시
    parser = argparse.ArgumentParser(prog="costkb")   # CLI 이름

정규식으로 쓸면 이것들까지 `app.deployment.costkb`가 되어 출처 표시가 깨지고
test_evidence_labels·test_claim_check 계열이 무너진다. 그래서 `ast.Import` /
`ast.ImportFrom` 노드가 **가리키는 모듈 경로만** 고친다.

동적 임포트(`import_module(f"envkb.{name}")`)는 AST로 판별할 수 없으므로 손대지
않고 목록으로 보고한다. 실측된 건 1건뿐이다(envkb/__main__.py).

사용:
    python scripts/rewrite_kb_imports.py <경로>              # 미리보기(기본)
    python scripts/rewrite_kb_imports.py <경로> --prefix app.deployment
    python scripts/rewrite_kb_imports.py <경로> --apply      # 실제 수정
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

#: 이동 대상 최상위 패키지. test_architecture.py의 ALL_PACKAGES와 같은 집합이다.
PACKAGES = frozenset({
    "appkb", "bundlekb", "capacitykb", "costkb", "envkb", "graphkb",
    "kbcommon", "nim_agent", "patternkb", "perfkb", "sizingkb",
})

#: AST가 못 보는 곳에 모듈 경로가 문자열로 있는 자리. 손으로 고칠 목록을 만든다.
DYNAMIC = re.compile(
    r"""import_module\(\s*f?["']({})[.]""".format("|".join(sorted(PACKAGES)))
)


class _Rewriter(ast.NodeVisitor):
    """임포트 노드가 가리키는 모듈 경로의 위치(줄, 열 범위)를 모은다."""

    def __init__(self) -> None:
        self.edits: list[tuple[int, str, str]] = []  # (줄번호, 원본 모듈, 새 모듈)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] in PACKAGES:
                self.edits.append((node.lineno, alias.name, ""))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # level > 0 은 패키지 내부 상대 임포트라 이동해도 그대로 맞는다.
        if node.level == 0 and node.module and node.module.split(".")[0] in PACKAGES:
            self.edits.append((node.lineno, node.module, ""))


def _rewrite_source(source: str, prefix: str) -> tuple[str, int]:
    """임포트가 가리키는 모듈 경로에만 prefix를 붙인다. (새 소스, 고친 건수)"""
    tree = ast.parse(source)
    visitor = _Rewriter()
    visitor.visit(tree)
    if not visitor.edits:
        return source, 0

    targets = {module for _, module, _ in visitor.edits}
    lines = source.splitlines(keepends=True)
    touched: set[int] = {lineno for lineno, _, _ in visitor.edits}
    count = 0

    for lineno in sorted(touched):
        line = lines[lineno - 1]
        original = line
        for module in sorted(targets, key=len, reverse=True):
            # 임포트문 안에서 모듈 경로로 등장하는 자리만. 단어 경계로 묶어
            # `costkb` 가 `costkb_extra` 같은 이름을 삼키지 않게 한다.
            line = re.sub(
                rf"(^\s*(?:from|import)\s+){re.escape(module)}(?=[\s,.]|$)",
                rf"\g<1>{prefix}.{module}",
                line,
            )
            # `import costkb.dataset, graphkb.query` 처럼 한 줄에 여럿인 경우.
            line = re.sub(
                rf"(,\s*){re.escape(module)}(?=[\s,.]|$)",
                rf"\g<1>{prefix}.{module}",
                line,
            )
        if line != original:
            lines[lineno - 1] = line
            count += 1

    return "".join(lines), count


def main(argv: list[str] | None = None) -> int:
    # 윈도우 콘솔 기본 코드페이지(cp949)가 이 파일의 한글·기호를 못 찍는다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="agent-sdk 패키지들이 있는 디렉터리")
    parser.add_argument("--prefix", default="app.deployment", help="새 임포트 접두 (기본: app.deployment)")
    parser.add_argument("--apply", action="store_true", help="실제로 파일을 고친다")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"디렉터리가 아닙니다: {args.root}", file=sys.stderr)
        return 2

    total_files = total_edits = 0
    dynamic: list[str] = []

    for path in sorted(args.root.rglob("*.py")):
        # .claude/worktrees 는 작업 중 만들어진 저장소 사본이라 세면 건수가 두 배로
        # 부풀고, 고쳐 봐야 병합 대상이 아니다 (agent-sdk .gitignore에도 있다).
        if any(part in {".venv", "__pycache__", ".git", ".claude"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(args.root).as_posix()

        for lineno, line in enumerate(source.splitlines(), 1):
            if DYNAMIC.search(line):
                dynamic.append(f"{rel}:{lineno}: {line.strip()}")

        new_source, count = _rewrite_source(source, args.prefix)
        if count:
            total_files += 1
            total_edits += count
            print(f"{'수정' if args.apply else '대상'} {rel}: {count}줄")
            if args.apply:
                path.write_text(new_source, encoding="utf-8")

    print(f"\n{'고친' if args.apply else '고칠'} 줄 {total_edits} · 파일 {total_files}")
    if dynamic:
        print(f"\n손으로 고칠 동적 임포트 {len(dynamic)}건 (AST가 못 본다):")
        for entry in dynamic:
            print(f"  {entry}")
    if not args.apply:
        print("\n미리보기였습니다 — 실제로 고치려면 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

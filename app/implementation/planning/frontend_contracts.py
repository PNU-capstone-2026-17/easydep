from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_FRONTEND_CONTRACT_BUDGET = 100_000


class FrontendContractBudgetExceeded(ValueError):
    """Raised instead of silently transmitting an incomplete generated contract."""


@dataclass(frozen=True)
class GeneratedClientContracts:
    generated_root: Path
    source_root: Path
    import_root: str
    files: tuple[Path, ...]

    @classmethod
    def discover(cls, generated_root: Path) -> GeneratedClientContracts:
        generated_root = generated_root.resolve()
        if not generated_root.is_dir():
            raise ValueError(
                f"OpenAPI Generator frontend client was not found: {generated_root}"
            )

        files = tuple(
            path
            for path in sorted(generated_root.rglob("*.ts"))
            if not _is_test_contract(path.relative_to(generated_root))
        )
        if not files:
            raise ValueError("OpenAPI Generator produced no TypeScript client contracts")

        source_root = _discover_source_root(generated_root, files)
        relative_source = source_root.relative_to(generated_root).as_posix()
        import_root = "src/generated"
        if relative_source != ".":
            import_root += f"/{relative_source}"
        return cls(
            generated_root=generated_root,
            source_root=source_root,
            import_root=import_root,
            files=files,
        )

    @property
    def page_import_root(self) -> str:
        relative = self.import_root.removeprefix("src/")
        return f"../{relative}"

    def render(
        self, max_chars: int = DEFAULT_FRONTEND_CONTRACT_BUDGET
    ) -> str:
        chunks = self._render_chunks(compact=False)
        total_chars = sum(len(chunk) for chunk in chunks)
        if total_chars > max_chars:
            compact_chunks = self._render_chunks(compact=True)
            compact_total = sum(len(chunk) for chunk in compact_chunks)
            if compact_total <= max_chars:
                return "\n".join(compact_chunks)
        if total_chars > max_chars:
            raise FrontendContractBudgetExceeded(
                "Generated TypeScript contracts exceed the frontend-agent context "
                f"budget: {total_chars} > {max_chars} characters across "
                f"{len(self.files)} files. Split frontend planning by OpenAPI tag/page "
                "before transmitting any partial contract."
            )
        return "\n".join(chunks)

    def _render_chunks(self, *, compact: bool) -> list[str]:
        chunks: list[str] = []
        for path in self.files:
            source = path.read_text(encoding="utf-8").strip()
            if compact:
                source = _compact_typescript(source)
            chunks.append(
                f"// {path.relative_to(self.generated_root).as_posix()}\n"
                f"{source}\n"
            )
        return chunks


def _discover_source_root(
    generated_root: Path, files: tuple[Path, ...]
) -> Path:
    candidates: set[Path] = set()
    for path in files:
        if path.name == "runtime.ts":
            candidates.add(path.parent)
        if path.parent.name in {"apis", "models"}:
            candidates.add(path.parent.parent)
    if not candidates:
        return generated_root
    return min(
        candidates,
        key=lambda path: (len(path.relative_to(generated_root).parts), path.as_posix()),
    )


def _compact_typescript(source: str) -> str:
    """Remove comments and blank lines without changing string literals."""
    output: list[str] = []
    index = 0
    line: list[str] = []
    state = "code"
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
                if "".join(line).strip():
                    output.append("".join(line).rstrip())
                line = []
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                state = "code"
                index += 2
            else:
                index += 1
            continue
        if state in {"single", "double", "template"}:
            line.append(char)
            if char == "\\" and next_char:
                line.append(next_char)
                index += 2
                continue
            closing = {"single": "'", "double": '"', "template": "`"}[state]
            if char == closing:
                state = "code"
            index += 1
            continue
        if char == "/" and next_char == "/":
            state = "line_comment"
            index += 2
            continue
        if char == "/" and next_char == "*":
            state = "block_comment"
            index += 2
            continue
        if char in {"'", '"', "`"}:
            state = {"'": "single", '"': "double", "`": "template"}[char]
        if char == "\n":
            if "".join(line).strip():
                output.append("".join(line).rstrip())
            line = []
        else:
            line.append(char)
        index += 1
    if "".join(line).strip():
        output.append("".join(line).rstrip())
    return "\n".join(output)


def _is_test_contract(relative: Path) -> bool:
    lowered_parts = {part.lower() for part in relative.parts}
    return bool(
        lowered_parts & {"test", "tests", "__tests__"}
        or relative.name.lower().endswith(("test.ts", "spec.ts"))
    )

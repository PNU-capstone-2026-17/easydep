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
    def discover(cls, generated_root: Path) -> "GeneratedClientContracts":
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
        chunks = [
            f"// {path.relative_to(self.generated_root).as_posix()}\n"
            f"{path.read_text(encoding='utf-8').strip()}\n"
            for path in self.files
        ]
        total_chars = sum(len(chunk) for chunk in chunks)
        if total_chars > max_chars:
            raise FrontendContractBudgetExceeded(
                "Generated TypeScript contracts exceed the frontend-agent context "
                f"budget: {total_chars} > {max_chars} characters across "
                f"{len(self.files)} files. Split frontend planning by OpenAPI tag/page "
                "before transmitting any partial contract."
            )
        return "\n".join(chunks)


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


def _is_test_contract(relative: Path) -> bool:
    lowered_parts = {part.lower() for part in relative.parts}
    return bool(
        lowered_parts & {"test", "tests", "__tests__"}
        or relative.name.lower().endswith(("test.ts", "spec.ts"))
    )

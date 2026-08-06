"""Method-neutral structural verification for generated baseline repositories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts and "logs" not in path.parts
    ]


def inspect_repository(root: Path) -> dict[str, Any]:
    files = _files(root)
    relative = [path.relative_to(root).as_posix().lower() for path in files]

    def contains(*tokens: str) -> bool:
        return any(all(token in name for token in tokens) for name in relative)

    checks = {
        "source_present": any(name.endswith((".java", ".py", ".ts", ".js")) for name in relative),
        "test_present": any("test" in name and name.endswith((".java", ".py", ".ts", ".js")) for name in relative),
        "build_present": contains("build.gradle") or contains("pom.xml"),
        "dockerfile_present": any(name.endswith("dockerfile") for name in relative),
        "requirements_documented": contains("requirement") or contains("prd"),
        "design_documented": contains("design") or contains("architecture"),
        "deployment_diagram_present": any(
            "deploy" in name and name.endswith((".mmd", ".puml", ".md"))
            for name in relative
        ),
        "iac_present": any(name.endswith((".tf", ".bicep")) for name in relative),
        "traceability_present": any("trace" in name or "rtm" in name for name in relative),
    }
    return {
        "root": str(root.resolve()),
        "fileCount": len(files),
        "javaFileCount": sum(name.endswith(".java") for name in relative),
        "testFileCount": sum("test" in name and name.endswith(".java") for name in relative),
        "checks": checks,
        "requiredPassed": all(
            checks[name]
            for name in (
                "source_present",
                "test_present",
                "build_present",
                "dockerfile_present",
                "requirements_documented",
                "design_documented",
            )
        ),
        "cloudNativePassed": checks["deployment_diagram_present"] and checks["iac_present"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect_repository(args.repository)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()

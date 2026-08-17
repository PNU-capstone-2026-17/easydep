from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class FrontendScaffoldError(ValueError):
    pass


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
OPENAPI_GENERATOR_VERSION = "7.24.0"
OPENAPI_GENERATOR_NAME = "typescript-fetch"
OPENAPI_GENERATOR_IMAGE = (
    f"openapitools/openapi-generator-cli:v{OPENAPI_GENERATOR_VERSION}"
)
# The React scaffold pins every dependency to an exact version and the only
# per-application value in package.json is `name`, so the resolved lock is
# identical for every job.  Resolving it through the registry cost 7-46s per
# run; the committed template makes it a file write.
#
# To refresh after changing `react_scaffold_files`: write that function's
# package.json into an empty directory, run
# `npm install --package-lock-only --ignore-scripts --no-audit --no-fund`,
# and copy the resulting package-lock.json over the template.  Until then the
# drift guard below routes generation back through npm, so a stale template
# degrades speed rather than correctness.
PACKAGE_LOCK_TEMPLATE = Path(__file__).resolve().parents[1] / "tools" / "frontend" / "package-lock.json"


def _declared_dependencies(package_json: dict[str, Any]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        value = package_json.get(section)
        if isinstance(value, dict):
            merged.update({str(k): str(v) for k, v in value.items()})
    return merged


def render_package_lock(package_json_text: str) -> str | None:
    """Return the template lock renamed for this app, or None if it has drifted.

    Returning None is a signal to fall back to a real npm resolution rather
    than an error: the template is a cache, never the source of truth.
    """
    try:
        package_json = json.loads(package_json_text)
        template = json.loads(PACKAGE_LOCK_TEMPLATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    root = template.get("packages", {}).get("")
    if not isinstance(root, dict):
        return None
    if _declared_dependencies(root) != _declared_dependencies(package_json):
        return None
    name = package_json.get("name")
    if not isinstance(name, str) or not name:
        return None
    template["name"] = name
    root["name"] = name
    return json.dumps(template, ensure_ascii=False, indent=2) + "\n"


def validate_openapi(api_spec: dict[str, Any]) -> None:
    paths = api_spec.get("paths") if isinstance(api_spec, dict) else None
    if not isinstance(paths, dict) or not paths:
        raise FrontendScaffoldError("api_spec.paths must contain at least one operation")
    if not any(
        isinstance(item, dict)
        and any(method in HTTP_METHODS and isinstance(value, dict) for method, value in item.items())
        for item in paths.values()
    ):
        raise FrontendScaffoldError("api_spec.paths contains no supported HTTP operations")


def openapi_typescript_fetch_command(
    workspace_root: Path, openapi_path: Path, output_path: Path
) -> list[str]:
    root = workspace_root.resolve()
    source = openapi_path.resolve()
    target = output_path.resolve()
    if root not in source.parents or root not in target.parents:
        raise FrontendScaffoldError(
            "OpenAPI input and frontend output must stay in workspaceRoot"
        )
    container_root = Path("/workspace")
    container_source = container_root / source.relative_to(root)
    container_target = container_root / target.relative_to(root)
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{root}:/workspace",
        OPENAPI_GENERATOR_IMAGE,
        "generate",
        "-g",
        OPENAPI_GENERATOR_NAME,
        "-i",
        container_source.as_posix(),
        "-o",
        container_target.as_posix(),
        "--additional-properties="
        "supportsES6=true,typescriptThreePlus=true,withInterfaces=true,"
        "npmName=@easydep/generated-api,npmVersion=0.1.0",
    ]


def write_react_scaffold(
    frontend_root: Path,
    api_spec: dict[str, Any],
    *,
    application_name: str,
    api_base_url: str | None = None,
) -> dict[str, str]:
    validate_openapi(api_spec)
    files = react_scaffold_files(
        application_name, resolve_api_base_url(api_spec, api_base_url)
    )
    for relative, content in files.items():
        target = frontend_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return files


def resolve_api_base_url(
    api_spec: dict[str, Any], override: str | None = None
) -> str:
    """Resolve the generated client base URL without inventing an API prefix."""
    if override is not None and override.strip():
        return override.strip().rstrip("/")
    servers = api_spec.get("servers", [])
    if not isinstance(servers, list) or not servers:
        return ""
    server = servers[0]
    if not isinstance(server, dict) or not isinstance(server.get("url"), str):
        return ""
    url = server["url"].strip()
    variables = server.get("variables", {})
    if isinstance(variables, dict):
        for name, definition in variables.items():
            if isinstance(definition, dict) and "default" in definition:
                url = url.replace("{" + str(name) + "}", str(definition["default"]))
    if re.search(r"\{[^{}]+\}", url):
        raise FrontendScaffoldError(
            "OpenAPI server URL contains a variable without a default value"
        )
    return url.rstrip("/")


def react_scaffold_files(
    application_name: str, api_base_url: str
) -> dict[str, str]:
    package_name = re.sub(r"[^a-z0-9]+", "-", application_name.lower()).strip("-")
    package_name = package_name or "easydep-frontend"
    title = json.dumps(application_name.strip() or "EasyDep Application", ensure_ascii=False)
    base_url = json.dumps(api_base_url.rstrip("/"), ensure_ascii=False)
    return {
        ".gitignore": "node_modules\ndist\n.env.local\n",
        ".env.example": f"VITE_API_BASE_URL={api_base_url.rstrip('/')}\n",
        "package.json": json.dumps(
            {
                "name": package_name,
                "private": True,
                "version": "0.1.0",
                "type": "module",
                "scripts": {
                    "dev": "vite",
                    "build": "tsc -b && vite build",
                    "preview": "vite preview",
                },
                "dependencies": {
                    "react": "18.3.1",
                    "react-dom": "18.3.1",
                    "react-router-dom": "7.18.2",
                },
                "devDependencies": {
                    "@types/react": "18.3.18",
                    "@types/react-dom": "18.3.5",
                    "@vitejs/plugin-react": "4.3.4",
                    "typescript": "5.7.3",
                    "vite": "6.4.3",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "index.html": """<!doctype html>
<html lang="en"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>Generated application</title></head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>
""",
        "tsconfig.json": """{
  "compilerOptions":{"target":"ES2022","useDefineForClassFields":true,"lib":["ES2022","DOM","DOM.Iterable"],"skipLibCheck":true,"esModuleInterop":true,"allowSyntheticDefaultImports":true,"strict":true,"module":"ESNext","moduleResolution":"Bundler","resolveJsonModule":true,"isolatedModules":true,"noEmit":true,"jsx":"react-jsx"},
  "include":["src"]
}
""",
        "vite.config.ts": """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()] });
""",
        "src/vite-env.d.ts": "/// <reference types=\"vite/client\" />\n",
        "src/main.tsx": """import React from 'react';
import ReactDOM from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import App from './App';
import './styles.css';
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><HashRouter><App /></HashRouter></React.StrictMode>);
""",
        "src/config.ts": f"export const API_BASE_URL=(import.meta.env.VITE_API_BASE_URL??{base_url}).replace(/\\/$/,'');\n",
        "src/App.tsx": f"export default function App(){{return <main><h1>{{{title}}}</h1><p>Waiting for the frontend implementation agent.</p></main>;}}\n",
        "src/styles.css": "body{margin:0;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#f4f7fb;color:#172033}*{box-sizing:border-box}\n",
        "README.md": f"""# {application_name.strip() or 'EasyDep Application'} frontend

`src/generated/` is generated by OpenAPI Generator (`typescript-fetch`). Pages and
components are owned by the EasyDep frontend implementation agent and verified with
`npm run build`.
""",
    }


def frontend_page_names(api_spec: dict[str, Any]) -> list[str]:
    validate_openapi(api_spec)
    tags: set[str] = set()
    for path_item in api_spec["paths"].values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            tags.add(str((operation.get("tags") or ["Overview"])[0]))
    return sorted({_component_name(tag) + "Page" for tag in tags})


def operation_ids(api_spec: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for path, path_item in api_spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method in HTTP_METHODS and isinstance(operation, dict):
                result.append(str(operation.get("operationId") or f"{method.upper()} {path}"))
    return sorted(result)


def _component_name(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    return "".join(word[:1].upper() + word[1:] for word in words) or "Overview"

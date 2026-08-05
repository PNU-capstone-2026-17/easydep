"""추출된 API 명세 요소(JSON)를 OpenAPI 3.0 명세(dict)로 결정론적으로 변환한다.

이 변환은 구성에 의해 항상 올바른 구조와 렌더링 가능한 OpenAPI dict를 생성한다.
"""
from __future__ import annotations

import re
from typing import Any


def sanitize_path(path: str) -> str:
    if not path:
        return "/api"
    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path
    return path


def sanitize_name(name: str) -> str:
    if not name:
        return "DefaultSchema"
    return re.sub(r"[^a-zA-Z0-9_]", "", name)


def generate_openapi_spec_from_json(json_data: dict[str, Any]) -> dict[str, Any]:
    if not json_data:
        return {}

    endpoints = json_data.get("endpoints", [])
    schemas = json_data.get("schemas", [])

    if not endpoints and not schemas:
        return {}

    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": json_data.get("title") or "System API",
            "description": json_data.get("description") or "",
            "version": json_data.get("version") or "1.0.0",
        },
        "paths": {},
        "components": {
            "schemas": {},
        },
    }

    # 1. Build Paths
    paths_dict: dict[str, Any] = {}
    for ep in endpoints:
        raw_path = ep.get("path", "")
        if not raw_path:
            continue
        path = sanitize_path(raw_path)
        method = (ep.get("method") or "get").lower().strip()

        if path not in paths_dict:
            paths_dict[path] = {}

        op_obj: dict[str, Any] = {}
        if ep.get("summary"):
            op_obj["summary"] = ep["summary"]
        if ep.get("description"):
            op_obj["description"] = ep["description"]

        tag = ep.get("tag")
        if tag:
            op_obj["tags"] = [tag]

        # Parameters
        params = ep.get("parameters", [])
        if params:
            param_list = []
            for p in params:
                p_name = p.get("name")
                if not p_name:
                    continue
                param_item = {
                    "name": p_name,
                    "in": p.get("in_location", "path"),
                    "required": p.get("required", True),
                    "schema": {"type": p.get("type", "string")},
                }
                if p.get("description"):
                    param_item["description"] = p["description"]
                param_list.append(param_item)
            if param_list:
                op_obj["parameters"] = param_list

        # Request Body
        req_ref = ep.get("request_body_schema_ref")
        if req_ref:
            clean_ref = sanitize_name(req_ref)
            op_obj["requestBody"] = {
                "required": ep.get("request_body_required", True),
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{clean_ref}"}
                    }
                },
            }

        # Responses
        responses_dict: dict[str, Any] = {}
        for resp in ep.get("responses", []):
            code = str(resp.get("status_code", "200"))
            resp_obj: dict[str, Any] = {
                "description": resp.get("description") or "Operation result",
            }
            resp_ref = resp.get("schema_ref")
            if resp_ref:
                clean_resp_ref = sanitize_name(resp_ref)
                resp_obj["content"] = {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{clean_resp_ref}"}
                    }
                }
            responses_dict[code] = resp_obj

        if not responses_dict:
            responses_dict["200"] = {"description": "Successful operation"}

        op_obj["responses"] = responses_dict
        paths_dict[path][method] = op_obj

    spec["paths"] = paths_dict

    # 2. Build Component Schemas
    schemas_dict: dict[str, Any] = {}
    for sc in schemas:
        raw_sname = sc.get("name")
        if not raw_sname:
            continue
        sname = sanitize_name(raw_sname)

        s_obj: dict[str, Any] = {
            "type": sc.get("type") or "object",
        }
        if sc.get("description"):
            s_obj["description"] = sc["description"]

        props_dict: dict[str, Any] = {}
        required_list: list[str] = []

        for prop in sc.get("properties", []):
            pname = prop.get("name")
            if not pname:
                continue
            if prop.get("required"):
                required_list.append(pname)

            ptype = prop.get("type") or "string"
            p_obj: dict[str, Any] = {}

            if ptype == "array" and prop.get("items_ref"):
                item_ref = sanitize_name(prop["items_ref"])
                p_obj["type"] = "array"
                p_obj["items"] = {"$ref": f"#/components/schemas/{item_ref}"}
            else:
                p_obj["type"] = ptype
                if prop.get("format"):
                    p_obj["format"] = prop["format"]

            if prop.get("description"):
                p_obj["description"] = prop["description"]
            if prop.get("example"):
                p_obj["example"] = prop["example"]

            props_dict[pname] = p_obj

        if required_list:
            s_obj["required"] = required_list
        if props_dict:
            s_obj["properties"] = props_dict

        schemas_dict[sname] = s_obj

    spec["components"]["schemas"] = schemas_dict

    return spec

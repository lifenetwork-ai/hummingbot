"""Offline OpenAPI generation from registered admin routes and handler metadata."""
import argparse
import json
import re
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from hummingbot.cli.admin_api import ADMIN_ENDPOINTS, ADMIN_PREFIX, AdminAPI
from hummingbot.cli.admin_models import ErrorResponse

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs/api/openapi.json"


def build_spec():
    schemas = {}

    def schema_for(annotation, mode="validation"):
        suffix = "Output" if mode == "serialization" else ""
        schema = TypeAdapter(annotation).json_schema(mode=mode, ref_template="#/components/schemas/{model}" + suffix)
        definitions = schema.pop("$defs", {})
        for name, definition in definitions.items():
            name += suffix
            if name in schemas and schemas[name] != definition:
                raise ValueError(f"Conflicting schema definition: {name}")
            schemas[name] = definition
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            name = annotation.__name__ + suffix
            if name in schemas and schemas[name] != schema:
                raise ValueError(f"Conflicting schema definition: {name}")
            schemas[name] = schema
            return {"$ref": f"#/components/schemas/{name}"}
        return schema

    errors = schema_for(ErrorResponse)
    paths, operation_ids = {}, set()
    for method, path, name in ADMIN_ENDPOINTS:
        metadata = getattr(getattr(AdminAPI, name), "api_documentation", None)
        if not metadata:
            raise ValueError(f"Missing handler annotation: {name}")
        operation_id = metadata["operation_id"]
        if operation_id in operation_ids:
            raise ValueError("Duplicate operation ID")
        operation_ids.add(operation_id)
        path_parameters = set(re.findall(r"\{([^}]+)\}", path))
        if path_parameters != set(metadata["path_parameters"]):
            raise ValueError(f"Handler path annotation disagrees with registered route: {name}")
        operation = dict(operationId=operation_id, summary=metadata["summary"], tags=[metadata["tag"]],
                         security=[{"basicAuth": []}], responses={
                             str(metadata["status"]): {"description": "Accepted; poll command result" if metadata["status"] == 202 else "Success",
                                                       "content": {metadata["response_content_type"]: {"schema": schema_for(metadata["response_model"], "serialization")}}}})
        for code, description in ((401, "Basic authentication required"), (403, "Origin not permitted"),
                                  (404, "Resource not found"), (409, "Control or retry identity conflict"),
                                  (422, "Request validation failed"), (503, "Service/data unavailable")):
            operation["responses"][str(code)] = {"description": description, "content": {"application/json": {"schema": errors}}}
        operation["responses"]["401"]["headers"] = {"WWW-Authenticate": {"schema": {"type": "string"}}}
        parameters = [{"name": key, "in": "header", "required": True,
                       "description": "Stable retry identity; reuse only for the identical request body",
                       "schema": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[!-~]+$"}}
                      for key in metadata["headers"]]
        parameters.extend({"name": key, "in": "path", "required": True, "schema": {"type": "string", "maxLength": 128}}
                          for key in sorted(path_parameters))
        if metadata["query_model"] is not None:
            query = TypeAdapter(metadata["query_model"]).json_schema()
            parameters.extend({"name": key, "in": "query", "required": key in query.get("required", []), "schema": value}
                              for key, value in query["properties"].items())
        if parameters:
            operation["parameters"] = parameters
        if metadata["request_model"] is not None:
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema_for(metadata["request_model"])}}}
            if metadata["examples"]:
                operation["requestBody"]["content"]["application/json"]["examples"] = metadata["examples"]
        paths.setdefault(ADMIN_PREFIX + path, {})[method.lower()] = operation
    spec = dict(openapi="3.1.0", info={"title": "Hummingbot MM Admin API", "version": "1.0.0"},
                paths=paths, security=[{"basicAuth": []}],
                components={"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}, "schemas": schemas})

    def validate_references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = value["$ref"]
                if not target.startswith("#/components/schemas/") or target.rsplit("/", 1)[1] not in schemas:
                    raise ValueError("OpenAPI schema contains an unresolved reference")
            for child in value.values():
                validate_references(child)
        elif isinstance(value, list):
            for child in value:
                validate_references(child)
    validate_references(spec)
    return spec


def rendered_spec():
    return json.dumps(build_spec(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SPEC_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = rendered_spec()
    if args.check:
        if not args.output.is_file() or args.output.read_text() != result:
            parser.exit(1, "OpenAPI artifact is missing or stale; run make swagger.\n")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

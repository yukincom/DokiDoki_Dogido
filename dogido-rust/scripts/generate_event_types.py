#!/usr/bin/env python3
"""既存Pydanticモデルの宣言部分をRustへ写す。意味検証はevents/semantic.rsで管理。"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from dogido_server.models import GameEvent


def pascal(name):
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def generate():
    schema = GameEvent.model_json_schema()
    definitions = dict(schema.pop("$defs"))
    definitions["EventData"] = schema
    # Rust/Fabric observations already in production; keep them when regenerating
    # declarations from the Python compatibility schema.
    extensions = {
        "AmbientSound": {"heard_ago_ms": {"type": "integer", "minimum": 0}},
        "AuditoryThreat": {"heard_ago_ms": {"type": "integer", "minimum": 0}},
        "WorldState": {"game_paused": {"type": "boolean"}},
    }
    for name, fields in extensions.items():
        for field, spec in fields.items():
            definitions[name]["properties"][field] = {
                "anyOf": [spec, {"type": "null"}], "default": None,
            }
    # JSON Schemaへ出ない意味検査を、宣言の再生成だけで落とさない。
    tree = ast.parse((ROOT.parent / "dogido_server/models.py").read_text())
    validators = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and (node.name in definitions or node.name == "GameEvent"):
            names = {method.name for method in node.body if isinstance(method, ast.FunctionDef)
                     and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "model_validator"
                             for d in method.decorator_list)}
            if names:
                validators[node.name] = names
    assert validators == {
        "HotbarState": {"_slot_indices_are_unique"},
        "ZombieScentClue": {"_validate_bounded_scent"},
        "SmellObservation": {"_validate_status_shape"},
        "GameEvent": {"_accept_legacy_peaceful_mobs"},
    }, "Pythonの意味検査が変わりました。Rust側の対応と比較ケースを確認してください。"
    enums = {}
    defaults = []
    models = []
    checks = []

    def rust_type(spec, name):
        if "$ref" in spec:
            return spec["$ref"].rsplit("/", 1)[-1]
        if "anyOf" in spec:
            choices = [s for s in spec["anyOf"] if s.get("type") != "null"]
            assert len(choices) == 1 and len(spec["anyOf"]) == 2
            return f"Option<{rust_type(choices[0], name)}>"
        if "enum" in spec or "const" in spec:
            enums[name] = spec.get("enum", [spec.get("const")])
            return name
        kind = spec["type"]
        if kind == "array":
            return f"Vec<{rust_type(spec['items'], name + 'Item')}>"
        if kind == "object":
            return f"BTreeMap<String, {rust_type(spec['additionalProperties'], name + 'Value')}>"
        if spec.get("format") == "date-time":
            return "EventTime"
        return {"string": "String", "integer": "i64", "number": "f64", "boolean": "bool"}[kind]

    for name, spec in definitions.items():
        if "enum" in spec:
            enums[name] = spec["enum"]
            continue
        assert spec["type"] == "object", (name, spec)
        required = set(spec.get("required", []))
        derives = "Clone, Debug, PartialEq, Serialize, Deserialize" if name == "MobIdentity" else "Clone, Debug, Serialize, Deserialize"
        lines = [f"#[derive({derives})]"]
        if spec.get("additionalProperties") is False:
            lines += ["#[serde(deny_unknown_fields)]"]
        lines += [f"pub struct {name} {{"]
        validation = []
        for field, fspec in spec["properties"].items():
            ty = rust_type(fspec, name + pascal(field))
            ident = "r#type" if field == "type" else field
            if field not in required:
                default = fspec.get("default")
                if default is None or default is False or default == 0:
                    lines += ["    #[serde(default)]"]
                else:
                    fn = f"default_{name.lower()}_{field}"
                    if ty == "String":
                        expr = json.dumps(default, ensure_ascii=False) + ".into()"
                    elif ty in ("i64", "f64", "bool"):
                        expr = json.dumps(default)
                    else:
                        expr = f"{ty}::{pascal(default)}"
                    defaults.append(f"fn {fn}() -> {ty} {{ {expr} }}")
                    lines += [f'    #[serde(default = "{fn}")]']
            # Pydantic既定の数値/真偽値変換。構造物とenumはSerdeの型検査を使う。
            if any(t in ty for t in ("i64", "f64", "bool")):
                lines += ['    #[serde(deserialize_with = "wire::deserialize")]']
            if field == "identity" or field in extensions.get(name, {}):
                lines += ['    #[serde(skip_serializing_if = "Option::is_none")]']
            lines += [f"    pub {ident}: {ty},"]
            path = f"{name}.{field}"
            nullable = ty.startswith("Option<")
            constrained = fspec
            if nullable:
                constrained = next(s for s in fspec["anyOf"] if s.get("type") != "null")
            rules = []
            val = "value" if nullable else f"self.{ident}"
            number = "*value" if nullable else val
            for op, symbol in [("minimum", ">="), ("maximum", "<=")]:
                if op in constrained:
                    limit = constrained[op]
                    literal = f"{limit}.0" if "f64" in ty and isinstance(limit, int) else str(limit)
                    rules.append(f'ensure({number} {symbol} {literal}, "{path}: {op} {limit}")?;')
            for op, symbol, count in [("minLength", ">=", "chars().count()"), ("maxLength", "<=", "chars().count()"), ("maxItems", "<=", "len()")]:
                if op in constrained:
                    rules.append(f'ensure(({val}).{count} {symbol} {constrained[op]}, "{path}: {op} {constrained[op]}")?;')
            if rules:
                if nullable:
                    validation += [f"        if let Some(value) = &self.{ident} {{", *["            " + r for r in rules], "        }"]
                else:
                    validation += ["        " + r for r in rules]
            # 全既知nested modelを再帰検査。unknown extraは解釈しない。
            ref = constrained
            if ref.get("type") == "array":
                ref = ref["items"]
            if "$ref" in ref and "properties" in definitions[ref["$ref"].rsplit("/", 1)[-1]]:
                validation += [f"        self.{ident}.validate()?;"]
        if spec.get("additionalProperties") is not False:
            lines += ["    #[serde(flatten)]", "    pub extra: BTreeMap<String, Value>,"]
        lines += ["}"]
        if not required:
            lines += [f"impl Default for {name} {{", "    fn default() -> Self { serde_json::from_str(\"{}\").expect(\"model defaults\") }", "}"]
        if name in {"HotbarState", "ZombieScentClue", "SmellObservation"}:
            validation += ["        self.validate_semantics()?;"]
        checks += [f"impl Validate for {name} {{", "    fn validate(&self) -> Result<(), String> {", *validation, "        Ok(())", "    }", "}"]
        models += lines
    enum_lines = []
    for name, values in sorted(enums.items()):
        assert all(isinstance(v, str) for v in values)
        enum_lines += ["#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]", f"pub enum {name} {{"]
        for value in values:
            enum_lines += [f"    #[serde(rename = {json.dumps(value)})]", f"    {pascal(value)},"]
        enum_lines += ["}"]
    header = "// Generated by scripts/generate_event_types.py; do not edit field declarations by hand.\n"
    imports = "use std::collections::BTreeMap;\nuse serde::{Deserialize, Serialize};\nuse serde_json::Value;\nuse super::{wire, EventTime, Validate, ensure};\n"
    return header + imports + "\n".join(enum_lines + models + defaults + checks) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "src/events/models.rs")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generate())

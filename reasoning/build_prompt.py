#!/usr/bin/env python3
"""
build_prompt.py — assemble the reasoning system prompt at runtime from the slim
template + config. The static instruction text stays tiny; everything that grows
(fields, entities, routing) lives in config/*.yaml and is summarized in compactly.
Run: python build_prompt.py   ->  prints the final prompt.
"""
import yaml, pathlib

BASE = pathlib.Path(__file__).parent


def schema_summary(fields, targets=None):
    out = []
    for target, keys in fields.items():
        if target.startswith("_") or not isinstance(keys, dict):
            continue
        if targets is not None and target not in targets:
            continue
        items = "; ".join(
            f"{key} = {spec['extract']}"
            for key, spec in keys.items()
            if isinstance(spec, dict) and spec.get("extract")
            and spec.get("model", True)
        )
        if not items:
            continue
        out.append(f"- {target}: {items}")
    return "\n".join(out)


def entities_summary(ent):
    parts = []
    if ent.get("partners"):
        parts.append("Partners: " + "; ".join(
            f"{p['name']} ({p.get('contact', '')})" for p in ent["partners"]))
    if ent.get("sponsors"):
        parts.append("Sponsors: " + ", ".join(ent["sponsors"]))
    if ent.get("investors"):
        parts.append("Investors: " + ", ".join(ent["investors"]))
    return "\n".join(parts)


def routing_summary(r):
    return ("- " + r["durability_test"] +
            f"\n- Opportunities tie to the {r['opportunity_company']}, never a partner." +
            f"\n- The meeting links to {r['meeting_links']} companies present." +
            f"\n- {r['stage_discipline']}")


def _build(template_name, targets=None):
    tmpl = (BASE / template_name).read_text()
    fields = yaml.safe_load((BASE / "config/fields.yaml").read_text())
    ent = yaml.safe_load((BASE / "config/entities.yaml").read_text())
    routing = yaml.safe_load((BASE / "config/routing.yaml").read_text())
    return (tmpl
            .replace("{{SCHEMA_SUMMARY}}", schema_summary(fields, targets))
            .replace("{{ENTITIES}}", entities_summary(ent))
            .replace("{{ROUTING}}", routing_summary(routing)))


def build():
    return _build("reasoning_prompt.md")


def build_manual():
    return _build("manual_notes_prompt.md")


def build_email():
    return _build("email_prompt.md", targets={"account", "contact", "deal"})


def build_portfolio():
    return (BASE / "portfolio_pattern_prompt.md").read_text()


if __name__ == "__main__":
    print(build())

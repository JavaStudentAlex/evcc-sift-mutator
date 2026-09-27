"""Measured conformance observations without inferred vulnerability claims."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_benchmark_report(
    report_path: str,
    metadata: dict[str, Any],
    sessions: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> None:
    """Write a readable summary and the corresponding machine-readable evidence."""
    path = Path(report_path)
    json_path = path.with_suffix(".json")
    if json_path == path:
        json_path = path.with_name(path.stem + ".telemetry.json")
    document = {"metadata": metadata, "sessions": sessions, "observations": observations}
    lines = [
        "# Protocol Mutation Benchmark Report",
        "",
        "These are test-fixture observations. A rejected request or connection drop",
        "does not by itself establish a defect, physical consequence, or CVE.",
        "",
        "## Run",
        "",
        "```json",
        json.dumps(metadata, indent=2),
        "```",
        "",
        "## Sessions",
        "",
    ]
    if not sessions:
        lines.append("No session was started.")
    for session in sessions:
        lines.extend(["```json", json.dumps(session, indent=2), "```", ""])
    lines.extend([
        "## Mutation Observations",
        "",
        f"Recorded observations: {len(observations)}.",
        f"Requests, responses, selection metadata, and outcomes are in `{json_path.name}`.",
        "",
        "Search rewards guide candidate selection; they are not conformance scores.",
        "Only measured counters are reported. Physical telemetry and protocol coverage",
        "require separate evidence from the configured fixture.",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    path.write_text("\n".join(lines), encoding="utf-8")

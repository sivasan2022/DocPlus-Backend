from __future__ import annotations

import argparse
import os
import re

from dotenv import load_dotenv

from backend.graph.ingestion import ingest_device_source
from backend.models.schemas import IngestionSummary


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Seed MedTrace M1 with a generic device data package.")
    parser.add_argument(
        "--source",
        action="append",
        help=(
            "Path to zip, folder, or single file. May be repeated or separated with semicolons. "
            "Defaults to MEDTRACE_DEVICE_SOURCES, then MEDTRACE_DEVICE_ZIP."
        ),
    )
    parser.add_argument("--device-name", default=os.getenv("MEDTRACE_DEFAULT_DEVICE_NAME", "Pulse Oximeter"))
    parser.add_argument("--device-id", default=os.getenv("MEDTRACE_DEFAULT_DEVICE_ID", "DEV-PULSE-OX"))
    parser.add_argument("--current-firmware", default=os.getenv("MEDTRACE_CURRENT_FIRMWARE", "v3.4"))
    parser.add_argument("--append", action="store_true", help="Append to existing graph instead of resetting first.")
    args = parser.parse_args()

    sources = _resolve_sources(args.source)
    if not sources:
        raise SystemExit("Missing --source, MEDTRACE_DEVICE_SOURCES, or MEDTRACE_DEVICE_ZIP in .env")

    summaries = []
    for index, source in enumerate(sources):
        summaries.append(
            ingest_device_source(
                source=source,
                device_name=args.device_name,
                device_id=args.device_id,
                current_firmware=args.current_firmware,
                reset=(not args.append and index == 0),
            )
        )
    summary = _combine_summaries(summaries)
    print(summary.model_dump_json(indent=2))


def _resolve_sources(cli_sources: list[str] | None) -> list[str]:
    raw_sources = cli_sources or []
    if not raw_sources:
        env_sources = os.getenv("MEDTRACE_DEVICE_SOURCES") or os.getenv("MEDTRACE_DEVICE_ZIP") or ""
        raw_sources = [env_sources]
    sources: list[str] = []
    for value in raw_sources:
        for item in re.split(r"\s*;\s*", value or ""):
            if item.strip():
                sources.append(item.strip().strip('"'))
    return list(dict.fromkeys(sources))


def _combine_summaries(summaries: list[IngestionSummary]) -> IngestionSummary:
    if not summaries:
        raise ValueError("No ingestion summaries to combine")
    last = summaries[-1]
    return IngestionSummary(
        device_id=last.device_id,
        device_name=last.device_name,
        real_documents_ingested=sum(item.real_documents_ingested for item in summaries),
        structured_artifacts_ingested=sum(item.structured_artifacts_ingested for item in summaries),
        structured_requirements_added=sum(item.structured_requirements_added for item in summaries),
        structured_tests_added=sum(item.structured_tests_added for item in summaries),
        structured_test_runs_added=sum(item.structured_test_runs_added for item in summaries),
        structured_risks_added=sum(item.structured_risks_added for item in summaries),
        structured_complaints_added=sum(item.structured_complaints_added for item in summaries),
        structured_capas_added=sum(item.structured_capas_added for item in summaries),
        structured_evidence_added=sum(item.structured_evidence_added for item in summaries),
        synthetic_requirements_added=sum(item.synthetic_requirements_added for item in summaries),
        synthetic_tests_added=sum(item.synthetic_tests_added for item in summaries),
        synthetic_risks_added=sum(item.synthetic_risks_added for item in summaries),
        synthetic_complaints_added=sum(item.synthetic_complaints_added for item in summaries),
        synthetic_capas_added=sum(item.synthetic_capas_added for item in summaries),
        nodes_total=last.nodes_total,
        edges_total=last.edges_total,
        orphan_count=last.orphan_count,
    )


if __name__ == "__main__":
    main()

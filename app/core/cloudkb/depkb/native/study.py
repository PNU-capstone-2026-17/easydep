"""CLI for the evidence-first native discovery and review lifecycle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.cloudkb.depkb.alignment import validate_alignment

from .adjudication import apply_adjudication, make_adjudication_template
from .consensus import reconcile_reviews
from .discovery import HERE, discover_all
from .freeze import freeze_native_graph, validate_frozen_graph
from .review import make_review_packet, review_counts, validate_review


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def discover() -> None:
    for provider, inventory in discover_all().items():
        target = HERE / f"{provider}-inventory.json"
        _write(target, inventory)
        print(
            provider,
            "elements=",
            len(inventory["elements"]),
            "candidates=",
            len(inventory["candidates"]),
        )


def prepare_reviews(*, overwrite: bool) -> None:
    for provider in ("aws", "azure", "gcp"):
        inventory = _read(HERE / f"{provider}-inventory.json")
        for reviewer in ("a", "b"):
            target = HERE / f"{provider}-review-{reviewer}.json"
            if target.exists() and not overwrite:
                raise FileExistsError(
                    f"review already exists: {target}; pass --overwrite only to discard it"
                )
            _write(target, make_review_packet(inventory))
        print(provider, "prepared two independent review packets")


def reconcile(first_reviewer: str, second_reviewer: str) -> None:
    for provider in ("aws", "azure", "gcp"):
        inventory = _read(HERE / f"{provider}-inventory.json")
        packet = reconcile_reviews(
            inventory,
            _read(HERE / f"{provider}-review-a.json"),
            _read(HERE / f"{provider}-review-b.json"),
            first_reviewer=first_reviewer,
            second_reviewer=second_reviewer,
        )
        _write(HERE / f"{provider}-consensus.json", packet)
        print(
            provider,
            review_counts(packet),
            "conflicts=",
            len(packet["consensus"]["conflicts"]),
            "humanReviewRequired=",
            packet["consensus"]["humanReviewRequired"],
        )


def status() -> bool:
    complete = True
    for provider in ("aws", "azure", "gcp"):
        inventory = _read(HERE / f"{provider}-inventory.json")
        review_path = HERE / f"{provider}-consensus.json"
        if not review_path.exists():
            print(provider, "consensus=missing")
            complete = False
            continue
        packet = _read(review_path)
        validate_review(inventory, packet, require_complete=False)
        counts = review_counts(packet)
        print(provider, counts)
        if counts["nodes.unreviewed"] or counts["candidates.unreviewed"]:
            complete = False
        if packet["consensus"]["humanReviewRequired"]:
            complete = False
    return complete


def prepare_adjudications(*, overwrite: bool) -> None:
    for provider in ("aws", "azure", "gcp"):
        consensus = _read(HERE / f"{provider}-consensus.json")
        target = HERE / f"{provider}-adjudication.json"
        if target.exists() and not overwrite:
            raise FileExistsError(
                f"adjudication already exists: {target}; pass --overwrite only to discard it"
            )
        template = make_adjudication_template(consensus)
        _write(target, template)
        print(provider, "conflicts=", len(template["decisions"]))


def apply_adjudications() -> None:
    for provider in ("aws", "azure", "gcp"):
        inventory = _read(HERE / f"{provider}-inventory.json")
        consensus = _read(HERE / f"{provider}-consensus.json")
        adjudication = _read(HERE / f"{provider}-adjudication.json")
        resolved = apply_adjudication(inventory, consensus, adjudication)
        _write(HERE / f"{provider}-consensus.json", resolved)
        print(provider, "human conflicts resolved")


def freeze() -> None:
    for provider in ("aws", "azure", "gcp"):
        inventory = _read(HERE / f"{provider}-inventory.json")
        review = _read(HERE / f"{provider}-consensus.json")
        graph = freeze_native_graph(inventory, review)
        validate_frozen_graph(graph)
        target = HERE / f"{provider}-graph.json"
        _write(target, graph)
        print(provider, graph["freeze"]["sha256"])


def verify_alignment(path: Path) -> None:
    document = _read(path)
    validate_alignment(document)
    print("alignment: ok", path)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("discover")
    review_parser = subparsers.add_parser("prepare-reviews")
    review_parser.add_argument("--overwrite", action="store_true")
    subparsers.add_parser("status")
    adjudication_parser = subparsers.add_parser("prepare-adjudications")
    adjudication_parser.add_argument("--overwrite", action="store_true")
    subparsers.add_parser("apply-adjudications")
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("first_reviewer")
    reconcile_parser.add_argument("second_reviewer")
    subparsers.add_parser("freeze")
    alignment_parser = subparsers.add_parser("verify-alignment")
    alignment_parser.add_argument("path", type=Path)
    args = parser.parse_args()

    if args.command == "discover":
        discover()
    elif args.command == "prepare-reviews":
        prepare_reviews(overwrite=args.overwrite)
    elif args.command == "status":
        if not status():
            raise SystemExit(1)
    elif args.command == "prepare-adjudications":
        prepare_adjudications(overwrite=args.overwrite)
    elif args.command == "apply-adjudications":
        apply_adjudications()
    elif args.command == "reconcile":
        reconcile(args.first_reviewer, args.second_reviewer)
    elif args.command == "freeze":
        freeze()
    else:
        verify_alignment(args.path)


if __name__ == "__main__":
    main()

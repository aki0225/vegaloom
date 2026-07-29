"""独立验证 OpenStates ancestor stub 的文件名、兼容读取和幂等行为。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import yaml


FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
DIVISION_SOURCE_URL = (
    "https://raw.githubusercontent.com/opencivicdata/"
    "ocd-division-ids/master/identifiers/country-us.csv"
)
JURISDICTION_SOURCE_URL = "https://github.com/openstates/jurisdictions"


class FrozenDateTime(datetime):
    """固定 writer 使用的当前时间，使 UUID 和文件名可重复。"""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        if tz is None:
            return FIXED_NOW.replace(tzinfo=None)
        return FIXED_NOW.astimezone(tz)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    return parser.parse_args()


def normalize_yaml_payload(data: dict[str, Any]) -> dict[str, Any]:
    normalized = yaml.safe_load(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    )
    if not isinstance(normalized, dict):
        raise ValueError("yaml_root_not_mapping")
    return normalized


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("yaml_root_not_mapping")
    return data


def tree_manifest(root: Path) -> dict[str, dict[str, int | str]]:
    manifest: dict[str, dict[str, int | str]] = {}
    for path in sorted(root.rglob("*.yaml")):
        stat = path.stat()
        manifest[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return manifest


def expected_division_payload(
    Division: Any,
    SourceType: Any,
    ancestor: Any,
    state_fips: str,
) -> dict[str, Any]:
    display_name = "Washington County"
    jur_part = ancestor.raw_ocdid.replace("ocd-division/", "")
    division = Division(
        ocdid=ancestor.raw_ocdid,
        country="us",
        display_name=display_name,
        geometries=[],
        also_known_as=[],
        jurisdiction_id=f"ocd-jurisdiction/{jur_part}/government",
        government_identifiers={
            "namelsad": display_name,
            "statefp": state_fips,
            "sldust": [],
            "sldlst": [],
            "countyfp": [],
            "county_names": [],
            "lsad": "",
            "geoid": state_fips,
        },
        sourcing=[
            {
                "field": ["ocdid"],
                "source_name": "ocdid_recursive_stub",
                "source_url": {"ocd_repo": DIVISION_SOURCE_URL},
                "source_type": SourceType.SCRAPED,
                "source_description": (
                    "Placeholder stub — created by recursive ancestor traversal"
                ),
            }
        ],
        accurate_asof=FIXED_NOW,
        last_updated=FIXED_NOW,
    )
    return normalize_yaml_payload(
        division.model_dump(exclude_none=False, mode="json")
    )


def expected_jurisdiction_payload(
    Jurisdiction: Any,
    ClassificationEnum: Any,
    SourceObj: Any,
    SourceType: Any,
    ancestor: Any,
) -> dict[str, Any]:
    display_name = "Washington County"
    div_ocdid = ancestor.raw_ocdid
    div_part = div_ocdid.replace("ocd-division/", "")
    jur_source = SourceObj(
        field=["ocdid", "name", "classification"],
        source_name="ocdid_recursive_stub",
        source_url={"ocd_repo": JURISDICTION_SOURCE_URL},
        source_type=SourceType.HUMAN,
        source_description=(
            "Placeholder stub — created by recursive ancestor traversal"
        ),
    )
    jurisdiction = Jurisdiction(
        ocdid=f"ocd-jurisdiction/{div_part}/government",
        name=f"{display_name} Government",
        url=f"https://opencivicdata.org/division/{div_ocdid}",
        classification=ClassificationEnum.GOVERNMENT,
        legislative_sessions={},
        feature_flags=[],
        metadata={"urls": []},
        sourcing=[jur_source],
        accurate_asof=FIXED_NOW,
        last_updated=FIXED_NOW,
    )
    return normalize_yaml_payload(
        jurisdiction.model_dump(mode="json", exclude_none=True)
    )


def exercise_writer(
    writer: Callable[..., Path],
    output_dir: Path,
    calls: list[tuple[Any, ...]],
    expected_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    first_path = writer(*calls[0], output_dir)
    first_bytes = first_path.read_bytes()
    second_path = writer(*calls[1], output_dir)

    actual_files = sorted(output_dir.glob("*.yaml"))
    actual_payloads = [load_yaml(path) for path in actual_files]
    actual_by_ocdid = {
        str(payload.get("ocdid")): payload for payload in actual_payloads
    }
    expected_by_ocdid = {
        str(payload.get("ocdid")): payload for payload in expected_payloads
    }
    expected_basenames = sorted(
        f"washington_county_{payload['id']}.yaml"
        for payload in expected_payloads
    )
    actual_basenames = sorted(path.name for path in actual_files)

    checks = {
        "same_parent": (
            first_path.parent == output_dir and second_path.parent == output_dir
        ),
        "paths_distinct": first_path != second_path,
        "file_count": len(actual_files),
        "basenames_exact": actual_basenames == expected_basenames,
        "stub_suffix_absent": all(
            "_stub" not in basename for basename in actual_basenames
        ),
        "first_preserved": (
            first_path.is_file() and first_path.read_bytes() == first_bytes
        ),
        "semantic_equal": (
            len(actual_by_ocdid) == 2
            and actual_by_ocdid == expected_by_ocdid
        ),
        "ids_distinct": len({payload["id"] for payload in expected_payloads}) == 2,
    }
    passed = (
        checks["same_parent"]
        and checks["paths_distinct"]
        and checks["file_count"] == 2
        and checks["basenames_exact"]
        and checks["stub_suffix_absent"]
        and checks["first_preserved"]
        and checks["semantic_equal"]
        and checks["ids_distinct"]
    )
    return {
        "passed": passed,
        "returned_basenames": [first_path.name, second_path.name],
        "actual_basenames": actual_basenames,
        "expected_basenames": expected_basenames,
        **checks,
    }


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    if not (repo / "src" / "init_migration" / "generate_recursive.py").is_file():
        print(json.dumps({"status": "error", "error": "target_source_missing"}))
        return 2

    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo))
    original_cwd = Path.cwd()
    os.chdir(repo)
    try:
        # 控制脚本只输出结构化结果；目标模块的正常 INFO 日志不属于判定证据。
        logging.getLogger("src.init_migration.generate_recursive").setLevel(
            logging.WARNING
        )

        import src.init_migration.generate_recursive as generate_recursive
        from src.models.division import Division
        from src.models.jurisdiction import (
            ClassificationEnum,
            Jurisdiction,
        )
        from src.models.ocdid import OCDIdParsed
        from src.models.source import SourceObj, SourceType

        first_ancestor = OCDIdParsed.parse_ocdid(
            "ocd-division/country:us/state:or/county:washington"
        )
        second_ancestor = OCDIdParsed.parse_ocdid(
            "ocd-division/country:us/state:ut/county:washington"
        )
        expected_divisions = [
            expected_division_payload(
                Division,
                SourceType,
                first_ancestor,
                "41",
            ),
            expected_division_payload(
                Division,
                SourceType,
                second_ancestor,
                "49",
            ),
        ]
        expected_jurisdictions = [
            expected_jurisdiction_payload(
                Jurisdiction,
                ClassificationEnum,
                SourceObj,
                SourceType,
                first_ancestor,
            ),
            expected_jurisdiction_payload(
                Jurisdiction,
                ClassificationEnum,
                SourceObj,
                SourceType,
                second_ancestor,
            ),
        ]

        with (
            tempfile.TemporaryDirectory(prefix="crwp-v1-03-") as temp_value,
            mock.patch.object(
                generate_recursive,
                "datetime",
                FrozenDateTime,
            ),
        ):
            temp_root = Path(temp_value)
            division_result = exercise_writer(
                generate_recursive._write_stub_division,
                temp_root / "direct-division",
                [
                    (first_ancestor, "Washington County", "41"),
                    (second_ancestor, "Washington County", "49"),
                ],
                expected_divisions,
            )
            jurisdiction_result = exercise_writer(
                generate_recursive._write_stub_jurisdiction,
                temp_root / "direct-jurisdiction",
                [
                    (first_ancestor, "Washington County"),
                    (second_ancestor, "Washington County"),
                ],
                expected_jurisdictions,
            )

            lookup_dir = temp_root / "lookup"
            lookup_dir.mkdir()
            legacy_ocdid = "ocd-division/country:us/state:ca"
            uuid_ocdid = "ocd-division/country:us/state:tx"
            missing_ocdid = "ocd-division/country:us/state:ny"
            (lookup_dir / "opaque_legacy_stub.yaml").write_text(
                yaml.safe_dump({"ocdid": legacy_ocdid}),
                encoding="utf-8",
            )
            (
                lookup_dir
                / "opaque_11111111-1111-1111-1111-111111111111.yaml"
            ).write_text(
                yaml.safe_dump({"ocdid": uuid_ocdid}),
                encoding="utf-8",
            )
            lookup_result = {
                "legacy_found": generate_recursive.stub_exists(
                    legacy_ocdid,
                    lookup_dir,
                ),
                "uuid_named_found": generate_recursive.stub_exists(
                    uuid_ocdid,
                    lookup_dir,
                ),
                "missing_not_found": not generate_recursive.stub_exists(
                    missing_ocdid,
                    lookup_dir,
                ),
            }

            idempotency_root = temp_root / "idempotency"
            leaf = OCDIdParsed.parse_ocdid(
                "ocd-division/country:us/state:wa/place:seattle"
            )
            first_run = generate_recursive.ensure_ancestor_stubs(
                leaf,
                idempotency_root,
                idempotency_root,
            )
            first_item = first_run[0] if len(first_run) == 1 else {}
            division_path_value = first_item.get("division_path")
            jurisdiction_path_value = first_item.get("jurisdiction_path")
            division_path = (
                Path(division_path_value) if division_path_value else None
            )
            jurisdiction_path = (
                Path(jurisdiction_path_value)
                if jurisdiction_path_value
                else None
            )
            division_payload = (
                load_yaml(division_path)
                if division_path is not None and division_path.is_file()
                else {}
            )
            jurisdiction_payload = (
                load_yaml(jurisdiction_path)
                if jurisdiction_path is not None
                and jurisdiction_path.is_file()
                else {}
            )
            before_manifest = tree_manifest(idempotency_root)
            with (
                mock.patch.object(
                    generate_recursive,
                    "_write_stub_division",
                    wraps=generate_recursive._write_stub_division,
                ) as division_writer,
                mock.patch.object(
                    generate_recursive,
                    "_write_stub_jurisdiction",
                    wraps=generate_recursive._write_stub_jurisdiction,
                ) as jurisdiction_writer,
            ):
                second_run = generate_recursive.ensure_ancestor_stubs(
                    leaf,
                    idempotency_root,
                    idempotency_root,
                )
            after_manifest = tree_manifest(idempotency_root)
            idempotency_result = {
                "first_created": (
                    len(first_run) == 1
                    and first_item.get("action") == "created"
                ),
                "first_files_written": (
                    division_path is not None
                    and jurisdiction_path is not None
                    and division_path.is_file()
                    and jurisdiction_path.is_file()
                    and division_path.is_relative_to(idempotency_root)
                    and jurisdiction_path.is_relative_to(idempotency_root)
                    and division_path.relative_to(
                        idempotency_root
                    ).as_posix()
                    in before_manifest
                    and jurisdiction_path.relative_to(
                        idempotency_root
                    ).as_posix()
                    in before_manifest
                    and len(before_manifest) == 2
                ),
                "first_expected_ocdids": (
                    division_payload.get("ocdid")
                    == "ocd-division/country:us/state:wa"
                    and jurisdiction_payload.get("ocdid")
                    == (
                        "ocd-jurisdiction/country:us/state:wa/government"
                    )
                ),
                "second_skipped": (
                    bool(second_run)
                    and all(
                        item.get("action") == "skipped"
                        and item.get("division_path") is None
                        and item.get("jurisdiction_path") is None
                        for item in second_run
                    )
                ),
                "writers_not_called": (
                    division_writer.call_count == 0
                    and jurisdiction_writer.call_count == 0
                ),
                "manifest_unchanged": before_manifest == after_manifest,
            }

        passed = (
            division_result["passed"]
            and jurisdiction_result["passed"]
            and all(lookup_result.values())
            and all(idempotency_result.values())
        )
        print(
            json.dumps(
                {
                    "status": "passed" if passed else "failed",
                    "fixed_time": FIXED_NOW.isoformat(),
                    "writers": {
                        "division": division_result,
                        "jurisdiction": jurisdiction_result,
                    },
                    "lookup": lookup_result,
                    "idempotency": idempotency_result,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if passed else 1
    finally:
        os.chdir(original_cwd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - 只用于保留可诊断失败
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)

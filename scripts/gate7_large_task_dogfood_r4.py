from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.gate7_large_task_dogfood import (  # noqa: E402
    Gate7ExperimentSpec,
    main,
)


R4_EXPERIMENT = Gate7ExperimentSpec(
    name="gate7-r4-v1",
    case_path=PROJECT_ROOT / "eval" / "gate-7" / "flask-teardown-case-r4.json",
    frozen_case_sha256="e14720051ff970e489176db8ef4165f90cc382f714e341c0734c90b8acf1e737",
    frozen_plan_sha256="f39ce91758867b4e5f7c5e338c85e4f4b8e5afa5a2374aee0dee44919fce7e2d",
    graph_schema_version="gate7-r4-v1",
    case_hash_mode="canonical-json",
)


if __name__ == "__main__":
    raise SystemExit(main(experiment=R4_EXPERIMENT))

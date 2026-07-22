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


R5_EXPERIMENT = Gate7ExperimentSpec(
    name="gate7-r5-api-key-v1",
    case_path=PROJECT_ROOT / "eval" / "gate-7" / "flask-teardown-case-r5.json",
    frozen_case_sha256="6b3541059cc6a2a8375424d303cb5a48b79b4b305d3dc6599f3b42b72330eaae",
    frozen_plan_sha256="cbda7c69e26370a05e44b4cd7691e386992befdb1f38af72e7d85892b754dba0",
    graph_schema_version="gate7-r4-v1",
    case_hash_mode="canonical-json",
    auth_mode="api-key",
)


if __name__ == "__main__":
    raise SystemExit(main(experiment=R5_EXPERIMENT))

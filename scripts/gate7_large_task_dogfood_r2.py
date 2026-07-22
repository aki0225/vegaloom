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


R2_EXPERIMENT = Gate7ExperimentSpec(
    name="gate7-r2-v1",
    case_path=PROJECT_ROOT / "eval" / "gate-7" / "flask-teardown-case-r2.json",
    frozen_case_sha256="b618a8e1db2e0ea2fbfdc3b7c0c42c6a5270eca872b2ede186aae189c80b5acb",
    frozen_plan_sha256="1cfe5b9ae1080b015ecc8050a15515c41879861e4f5275e4ac7b30204d26268b",
    graph_schema_version="gate7-r2-v1",
    case_hash_mode="canonical-json",
)


if __name__ == "__main__":
    raise SystemExit(main(experiment=R2_EXPERIMENT))

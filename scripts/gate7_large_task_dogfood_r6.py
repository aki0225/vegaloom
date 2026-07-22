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


R6_EXPERIMENT = Gate7ExperimentSpec(
    name="gate7-r6-remote-tag-timeout-v1",
    case_path=PROJECT_ROOT / "eval" / "gate-7" / "flask-teardown-case-r6.json",
    frozen_case_sha256="b8475f796f9ec8bac1c51eee9f1d30975e00c5b4e70859933f158663867b3f8d",
    frozen_plan_sha256="c0e372e5c56d6a322882ff147cee0e7e890bc4f9d20654fd18bb074f34ee8ddf",
    graph_schema_version="gate7-r4-v1",
    case_hash_mode="canonical-json",
    auth_mode="api-key",
)


if __name__ == "__main__":
    raise SystemExit(main(experiment=R6_EXPERIMENT))

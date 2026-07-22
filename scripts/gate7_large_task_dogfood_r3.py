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


R3_EXPERIMENT = Gate7ExperimentSpec(
    name="gate7-r3-v1",
    case_path=PROJECT_ROOT / "eval" / "gate-7" / "flask-teardown-case-r3.json",
    frozen_case_sha256="e244483bb294b2d99cf934f8619729808763c791b5e0b7b6c4ce83bbbd4c5e81",
    frozen_plan_sha256="5c6ae968bfd0378c8eb0643aea16e0e3956c708d9a15c2df805436788abbe2ab",
    graph_schema_version="gate7-r3-v1",
    case_hash_mode="canonical-json",
)


if __name__ == "__main__":
    raise SystemExit(main(experiment=R3_EXPERIMENT))

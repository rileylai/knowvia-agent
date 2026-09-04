from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.evals import step99_hybrid_eval as core


EXPERIMENT_ID = "step99-exp-002"
DEFAULT_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "step_99" / EXPERIMENT_ID


def _activate_contract() -> None:
    core.EXPERIMENT_ID = EXPERIMENT_ID
    core.DEFAULT_FIXTURE_DIR = DEFAULT_FIXTURE_DIR


def main() -> None:
    _activate_contract()
    parser = argparse.ArgumentParser(description="Offline Step 99 exp-002 hybrid evaluation")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--pgvector-evidence", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    try:
        if args.freeze:
            manifest = core.load_contract(args.fixture_dir, create_receipt=True)
            print(json.dumps({"status": "frozen", "experiment_id": EXPERIMENT_ID, "manifest_digest": core.canonical_digest(manifest)}, sort_keys=True))
            return
        if args.evaluate:
            if args.result is None:
                raise core.Step99ContractError("canonical result path required")
            manifest = core.load_contract(args.fixture_dir)
            expected = core._REPO_ROOT / manifest["artifacts"]["result_path"]
            if args.result.resolve() != expected.resolve():
                raise core.Step99ContractError("canonical result path mismatch")
            payload = core.evaluate_experiment(
                fixture_dir=args.fixture_dir,
                pgvector_evidence_path=args.pgvector_evidence,
            )
            replay = core.write_or_replay(args.result, payload)
            print(json.dumps({"status": payload["decision"]["status"], "selected_weight_id": payload["selected_weight_id"], "result_digest": payload["result_digest"], "replay_status": replay}, sort_keys=True))
            return
        manifest = core.load_contract(args.fixture_dir)
        print(json.dumps({"status": "validated", "manifest_digest": core.canonical_digest(manifest)}, sort_keys=True))
    except core.Step99ContractError as exc:
        print(json.dumps({"status": "inconclusive", "safe_failure_category": str(exc)}, sort_keys=True))
        raise SystemExit(2)


if __name__ == "__main__":
    main()

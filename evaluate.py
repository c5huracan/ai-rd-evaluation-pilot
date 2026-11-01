from pathlib import Path
import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time

from sklearn.datasets import load_digits
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split


ROOT = Path(__file__).parent
SEED = 0
DEFAULT_TIMEOUT = 30.0
RUNTIME_FACTOR = 2.0


def load_candidate(path):
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("candidate_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_splits():
    X, y = load_digits(return_X_y=True)

    X_train, X_remaining, y_train, y_remaining = train_test_split(
        X, y, test_size=0.4, stratify=y, random_state=SEED
    )
    X_dev, X_hidden, y_dev, y_hidden = train_test_split(
        X_remaining, y_remaining, test_size=0.5, stratify=y_remaining,
        random_state=SEED
    )

    return X_train, y_train, X_dev, y_dev, X_hidden, y_hidden


def evaluate(candidate_path, mode="dev"):
    if mode not in {"dev", "final"}:
        raise ValueError(f"unknown evaluation mode: {mode}")

    candidate = load_candidate(candidate_path)
    model = candidate.make_model()
    X_train, y_train, X_dev, y_dev, X_hidden, y_hidden = make_splits()

    started = time.perf_counter()
    model.fit(X_train, y_train)
    dev_score = accuracy_score(y_dev, model.predict(X_dev))
    result = {
        "candidate": str(Path(candidate_path).resolve()),
        "mode": mode,
        "dev_score": dev_score,
        "runtime_seconds": time.perf_counter() - started,
    }

    if mode == "final":
        result["hidden_score"] = accuracy_score(
            y_hidden, model.predict(X_hidden)
        )

    return result


def evaluate_subprocess(
    candidate_path, mode="dev", timeout=DEFAULT_TIMEOUT, evaluator_path=None
):
    evaluator_path = Path(evaluator_path or __file__).resolve()
    command = [
        sys.executable,
        str(evaluator_path),
        str(Path(candidate_path).resolve()),
        "--mode", mode,
        "--worker",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "candidate": str(Path(candidate_path).resolve()),
            "mode": mode,
            "status": "timeout",
            "timeout_seconds": timeout,
        }

    if completed.returncode != 0:
        return {
            "candidate": str(Path(candidate_path).resolve()),
            "mode": mode,
            "status": "error",
            "returncode": completed.returncode,
            "stderr": completed.stderr.strip(),
        }

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "candidate": str(Path(candidate_path).resolve()),
            "mode": mode,
            "status": "error",
            "failure_reason": "worker returned invalid JSON",
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }

    result["status"] = "ok"
    return result


def evaluate_clean_rerun(candidate_path, mode="dev", timeout=DEFAULT_TIMEOUT):
    candidate_path = Path(candidate_path).resolve()
    try:
        relative_candidate = candidate_path.relative_to(ROOT.resolve())
    except ValueError:
        return {
            "candidate": str(candidate_path),
            "mode": mode,
            "status": "error",
            "failure_reason": "candidate is outside the project root",
        }

    with tempfile.TemporaryDirectory(prefix="rsi-clean-") as temp_dir:
        checkout = Path(temp_dir) / ROOT.name
        shutil.copytree(ROOT, checkout)
        clean_candidate = checkout / relative_candidate
        clean_evaluator = checkout / Path(__file__).name
        return evaluate_subprocess(
            clean_candidate,
            mode,
            timeout,
            evaluator_path=clean_evaluator,
        )


def add_clean_rerun_metadata(
    result, candidate_path, mode, timeout, baseline_runtime=None
):
    clean_result = evaluate_clean_rerun(candidate_path, mode, timeout)
    result["clean_rerun_status"] = clean_result.get("status")

    if clean_result.get("status") != "ok":
        result["passed_clean_score_match"] = False
        result["passed_clean_runtime"] = False
        result["passed_clean_rerun"] = False
        result["clean_rerun_failure_reason"] = (
            clean_result.get("failure_reason")
            or clean_result.get("stderr")
            or clean_result.get("status")
        )
        return result

    for field in ("dev_score", "hidden_score"):
        if field in clean_result:
            result[f"clean_rerun_{field}"] = clean_result[field]

    clean_runtime = clean_result.get("runtime_seconds")
    clean_runtime_passed = True
    if baseline_runtime is not None:
        clean_runtime_limit = RUNTIME_FACTOR * baseline_runtime
        result["clean_rerun_runtime_seconds"] = clean_runtime
        result["clean_rerun_runtime_limit_seconds"] = clean_runtime_limit
        clean_runtime_passed = (
            clean_runtime is not None
            and clean_runtime <= clean_runtime_limit
        )
        result["clean_rerun_passed_runtime_limit"] = clean_runtime_passed

    score_fields = ["dev_score"]
    if mode == "final":
        score_fields.append("hidden_score")
    scores_match = all(
        result.get(field) == clean_result.get(field)
        for field in score_fields
    )
    result["passed_clean_score_match"] = scores_match
    result["passed_clean_runtime"] = clean_runtime_passed
    result["passed_clean_rerun"] = scores_match and clean_runtime_passed
    if not scores_match:
        result["clean_rerun_failure_reason"] = "scores differed"
    elif not clean_runtime_passed:
        result["clean_rerun_failure_reason"] = "runtime limit exceeded"
    return result


def apply_runtime_limit(result, baseline_runtime):
    if baseline_runtime is None:
        return result
    if baseline_runtime <= 0:
        raise ValueError("baseline runtime must be positive")

    limit = RUNTIME_FACTOR * baseline_runtime
    result["baseline_runtime_seconds"] = baseline_runtime
    result["runtime_limit_seconds"] = limit

    runtime = result.get("runtime_seconds")
    if result.get("status") == "ok" and runtime is not None:
        result["passed_runtime_limit"] = runtime <= limit
        if not result["passed_runtime_limit"]:
            result["status"] = "runtime_limit_exceeded"

    return result


def add_baseline_metadata(
    result, baseline_candidate, baseline_result, baseline_final_result=None
):
    result["baseline_candidate"] = str(baseline_candidate)
    result["baseline_dev_score"] = baseline_result.get("dev_score")
    if baseline_final_result is not None:
        result["baseline_hidden_score"] = baseline_final_result.get(
            "hidden_score"
        )
    return apply_runtime_limit(
        result, baseline_result["runtime_seconds"]
    )


def add_verification_metadata(result, mode):
    result["passed_visible_tests"] = (
        result.get("status") in {"ok", "runtime_limit_exceeded"}
        and result.get("dev_score") is not None
    )

    if mode != "final":
        result["verified_improvement"] = None
        return result

    baseline_hidden_score = result.get("baseline_hidden_score")
    hidden_score = result.get("hidden_score")
    if baseline_hidden_score is not None and hidden_score is not None:
        result["hidden_score_difference"] = (
            hidden_score - baseline_hidden_score
        )
    else:
        result["hidden_score_difference"] = None

    difference = result["hidden_score_difference"]
    result["verified_improvement"] = (
        result["passed_visible_tests"]
        and result.get("passed_runtime_limit") is True
        and result.get("passed_clean_score_match") is True
        and result.get("passed_clean_runtime") is True
        and difference is not None
        and difference >= 0.01
    )

    if result["verified_improvement"]:
        result["failure_reason"] = None
    elif not result["passed_visible_tests"]:
        result["failure_reason"] = "visible_evaluation_failed"
    elif result.get("passed_runtime_limit") is not True:
        result["failure_reason"] = "runtime_limit_exceeded"
    elif difference is None:
        result["failure_reason"] = "hidden_evaluation_failed"
    elif difference < 0.01:
        result["failure_reason"] = "insufficient_hidden_improvement"
    elif result.get("passed_clean_score_match") is not True:
        result["failure_reason"] = "clean_score_mismatch"
    elif result.get("passed_clean_runtime") is not True:
        result["failure_reason"] = "clean_runtime_limit_exceeded"
    else:
        result["failure_reason"] = "verification_failed"
    return result
def evaluate_with_baseline(candidate_path, baseline_candidate, mode, timeout):
    baseline_candidate = Path(baseline_candidate).resolve()
    baseline_result = evaluate_subprocess(baseline_candidate, "dev", timeout)
    baseline_final_result = None
    if baseline_result.get("status") == "ok" and mode == "final":
        baseline_final_result = evaluate_subprocess(
            baseline_candidate, "final", timeout
        )

    baseline_failed = baseline_result.get("status") != "ok"
    final_baseline_failed = (
        mode == "final"
        and baseline_final_result is not None
        and baseline_final_result.get("status") != "ok"
    )
    if baseline_failed or final_baseline_failed:
        return {
            "candidate": str(Path(candidate_path).resolve()),
            "mode": mode,
            "status": "baseline_error",
            "baseline_candidate": str(baseline_candidate),
            "baseline_result": baseline_result,
            "baseline_final_result": baseline_final_result,
            "verified_improvement": False,
            "failure_reason": "baseline_evaluation_failed",
        }

    result = evaluate_subprocess(candidate_path, mode, timeout)
    result = add_baseline_metadata(
        result, baseline_candidate, baseline_result, baseline_final_result
    )
    result = add_clean_rerun_metadata(
        result, candidate_path, mode, timeout,
        baseline_runtime=baseline_result["runtime_seconds"],
    )
    return add_verification_metadata(result, mode)


def evaluate_batch(candidate_paths, baseline_candidate, mode, timeout):
    baseline_candidate = Path(baseline_candidate).resolve()
    baseline_result = evaluate_subprocess(baseline_candidate, "dev", timeout)
    baseline_final_result = None
    if baseline_result.get("status") == "ok" and mode == "final":
        baseline_final_result = evaluate_subprocess(
            baseline_candidate, "final", timeout
        )
    results = []

    for candidate_path in candidate_paths:
        baseline_failed = baseline_result.get("status") != "ok"
        final_baseline_failed = (
            mode == "final"
            and baseline_final_result is not None
            and baseline_final_result.get("status") != "ok"
        )
        if baseline_failed or final_baseline_failed:
            result = {
                "candidate": str(Path(candidate_path).resolve()),
                "mode": mode,
                "status": "baseline_error",
                "baseline_candidate": str(baseline_candidate),
                "baseline_result": baseline_result,
                "baseline_final_result": baseline_final_result,
                "verified_improvement": False,
                "failure_reason": "baseline_evaluation_failed",
            }
        else:
            result = evaluate_subprocess(candidate_path, mode, timeout)
            result = add_baseline_metadata(
                result, baseline_candidate, baseline_result,
                baseline_final_result,
            )
            result = add_clean_rerun_metadata(
                result, candidate_path, mode, timeout,
                baseline_runtime=baseline_result["runtime_seconds"],
            )
            result = add_verification_metadata(result, mode)
        results.append(result)

    return results


def append_jsonl(path, result):
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "candidate", nargs="+", type=Path, default=[ROOT / "baseline.py"]
    )
    parser.add_argument(
        "--mode", choices=("dev", "final"), default="dev"
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    baseline_group = parser.add_mutually_exclusive_group()
    baseline_group.add_argument("--baseline-runtime", type=float)
    baseline_group.add_argument("--baseline-candidate", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()

    if args.worker:
        print(json.dumps(evaluate(args.candidate[0], args.mode)))
    elif len(args.candidate) > 1:
        if args.baseline_candidate is None:
            parser.error(
                "--baseline-candidate is required for batch evaluation"
            )
        results = evaluate_batch(
            args.candidate, args.baseline_candidate, args.mode, args.timeout
        )
        for result in results:
            if args.results is not None:
                append_jsonl(args.results, result)
            print(json.dumps(result))
    elif args.baseline_candidate is not None:
        result = evaluate_with_baseline(
            args.candidate[0], args.baseline_candidate, args.mode, args.timeout
        )
        if args.results is not None:
            append_jsonl(args.results, result)
        print(json.dumps(result))
    else:
        result = evaluate_subprocess(
            args.candidate[0], args.mode, args.timeout
        )
        result = apply_runtime_limit(result, args.baseline_runtime)
        result = add_clean_rerun_metadata(
            result, args.candidate[0], args.mode, args.timeout,
            baseline_runtime=args.baseline_runtime,
        )
        if args.results is not None:
            append_jsonl(args.results, result)
        print(json.dumps(result))


"""Generate and preserve the ten official candidate responses.

This script makes no API calls when imported. Run it explicitly after fixing
MODEL and the generation settings in the experiment protocol.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI


ROOT = Path(__file__).resolve().parent
PROMPT_PATH = ROOT / "candidate_prompt.txt"
RAW_DIR = ROOT / "candidates" / "raw"
SOURCE_DIR = ROOT / "candidates" / "source"
METADATA_DIR = ROOT / "candidates" / "metadata"
MANIFEST_PATH = METADATA_DIR / "candidate-manifest.ndjson"

MODEL = os.environ.get("OPENROUTER_MODEL")
PROVIDER = os.environ.get("OPENROUTER_PROVIDER")
SOURCE_COMMIT = os.environ.get("RSI_SOURCE_COMMIT")
BASE_URL = "https://openrouter.ai/api/v1"
TEMPERATURE = 0.2
TOP_P = 1.0
MAX_TOKENS = 2000
ATTEMPTS = 10
USE_SEEDS = False
SEEDS = tuple(range(1001, 1001 + ATTEMPTS))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_candidate(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"syntax_error: {exc}"
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    if "make_model" not in functions:
        return "missing_make_model"
    if "```" in source:
        return "markdown_fence_or_extra_text"
    return None


def request_parameters(prompt: str, attempt_number: int) -> dict:
    params = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_tokens": MAX_TOKENS,
        "extra_body": {
            "provider": {
                "order": [PROVIDER],
                "allow_fallbacks": False,
            }
        },
    }
    if USE_SEEDS:
        params["seed"] = SEEDS[attempt_number - 1]
    return params


def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    if not MODEL:
        raise SystemExit("OPENROUTER_MODEL is not set")
    if not SOURCE_COMMIT:
        raise SystemExit("RSI_SOURCE_COMMIT is not set")
    if not PROVIDER:
        raise SystemExit("OPENROUTER_PROVIDER is not set")
    if MANIFEST_PATH.exists():
        raise SystemExit(
            f"manifest already exists: {MANIFEST_PATH}; "
            "refusing to mix generation runs"
        )


    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    prompt_hash = sha256_text(prompt)
    for directory in (RAW_DIR, SOURCE_DIR, METADATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    with MANIFEST_PATH.open("a", encoding="utf-8") as manifest:
        for attempt_number in range(1, ATTEMPTS + 1):
            stem = f"attempt-{attempt_number:02d}"
            raw_path = RAW_DIR / f"{stem}.txt"
            source_path = SOURCE_DIR / f"{stem}.py"
            if raw_path.exists() or source_path.exists():
                raise SystemExit(f"artifacts already exist for {stem}")

            seed = SEEDS[attempt_number - 1] if USE_SEEDS else None
            record = {
                "attempt_number": attempt_number,
                "candidate_path": str(source_path.relative_to(ROOT)),
                "raw_response_path": str(raw_path.relative_to(ROOT)),
                "model": MODEL,
                "provider": PROVIDER,
                "source_commit": SOURCE_COMMIT,
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
                "max_tokens": MAX_TOKENS,
                "seed": seed,
                "prompt_sha256": prompt_hash,
                "generation_started_at": utc_now(),
                "human_edits": False,
            }

            try:
                response = client.chat.completions.create(
                    **request_parameters(prompt, attempt_number)
                )
                content = response.choices[0].message.content or ""
                raw_path.write_text(content, encoding="utf-8")
                source_path.write_text(content, encoding="utf-8")
                record.update({
                    "request_id": getattr(response, "id", None),
                    "returned_model": getattr(response, "model", None),
                    "raw_response_sha256": sha256_text(content),
                    "candidate_sha256": sha256_text(content),
                    "protocol_violation": check_candidate(content),
                    "status": "ok",
                    "generation_finished_at": utc_now(),
                })
                usage = getattr(response, "usage", None)
                if usage is not None:
                    record["usage"] = usage.model_dump() if hasattr(usage, "model_dump") else str(usage)
            except Exception as exc:
                raw_path.write_text("", encoding="utf-8")
                source_path.write_text("", encoding="utf-8")
                record.update({
                    "status": "error",
                    "error": repr(exc),
                    "raw_response_sha256": sha256_text(""),
                    "candidate_sha256": sha256_text(""),
                    "generation_finished_at": utc_now(),
                })

            manifest.write(json.dumps(record, sort_keys=True) + "\n")
            manifest.flush()
            print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()

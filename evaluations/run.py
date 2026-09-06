import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.domain.ai_router import POLICIES, ProviderError, Task, validate_candidate
from app.infra.providers import LiteLLMProvider


def critical_fields(task: Task, expected: dict[str, Any]) -> list[str]:
    if task == Task.SCHOLARSHIP_EXTRACTION:
        candidate = expected.get("candidate", {})
        return [f"candidate.{key}" for key in candidate]
    return ["explanation"] if "explanation" in expected else []


def field_at(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


async def main(path: str) -> None:
    provider = LiteLLMProvider()
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(sys.argv) == 3 and sys.argv[2] == "--validate-only":
        counts: dict[str, int] = {}
        for row in rows:
            task = Task(row["task"])
            if row.get("review_status") not in {"PROPOSED_REVIEW_REQUIRED", "APPROVED"}:
                raise ValueError(f"{row['id']}: unexpected review status")
            if not isinstance(row.get("expected"), dict):
                raise ValueError(f"{row['id']}: expected must be an object")
            counts[task.value] = counts.get(task.value, 0) + 1
        print(json.dumps({"samples": len(rows), "by_task": counts, "status": "valid"}, indent=2))
        return
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        task = Task(row["task"])
        policy = POLICIES[task]
        model = settings.task_models.get(task.value, "")
        stats = summary.setdefault(
            task.value,
            {"samples": 0, "valid": 0, "critical": 0, "critical_correct": 0, "errors": 0},
        )
        stats["samples"] += 1
        try:
            result = await provider.complete(
                task=task,
                source_data=row["source_data"],
                model=model,
                max_tokens=policy.max_output_tokens,
            )
        except ProviderError:
            stats["errors"] += 1
            continue
        output = result.output
        if validate_candidate(task, output):
            stats["valid"] += 1
        for field in critical_fields(task, row.get("expected", {})):
            stats["critical"] += 1
            if field_at(output, field) == field_at(row["expected"], field):
                stats["critical_correct"] += 1
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: python evaluations/run.py samples.jsonl [--validate-only]")
    asyncio.run(main(sys.argv[1]))

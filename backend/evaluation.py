from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from backend.rag import CORPUS, search

DATASET = Path(__file__).parent.parent / "evaluations" / "cases.jsonl"


def run_fixed_evaluation(path: Path = DATASET) -> dict:
    source = path.read_bytes()
    cases = [json.loads(line) for line in source.decode("utf-8").splitlines() if line]
    categories = Counter(case["category"] for case in cases)
    duplicate_ids = len(cases) - len({case["id"] for case in cases})
    rag_cases = [case for case in cases if case["category"] == "rag"]
    rag_hits = 0
    details = []
    for case in rag_cases:
        retrieved = [item["id"] for item in search(case["input"], limit=5)]
        passed = case["expected"] in retrieved
        rag_hits += int(passed)
        details.append({"id": case["id"], "passed": passed, "retrieved": retrieved})
    return {
        "mode": "deterministic_retrieval",
        "dataset_checksum": hashlib.sha256(source).hexdigest(),
        "corpus_checksum": hashlib.sha256(json.dumps([asdict(chunk) for chunk in CORPUS], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "evaluator_version": "fixed-rag-v1",
        "dataset_size": len(cases),
        "categories": dict(sorted(categories.items())),
        "duplicate_ids": duplicate_ids,
        "schema_valid": duplicate_ids == 0 and all({"id", "category", "input", "expected"} <= case.keys() for case in cases),
        "rag_recall_at_5": round(rag_hits / len(rag_cases), 3) if rag_cases else None,
        "rag_cases": details,
        "note": "非 RAG 场景由 pytest API/领域测试执行；此运行器校验数据集并计算本地检索 Recall@5。",
    }

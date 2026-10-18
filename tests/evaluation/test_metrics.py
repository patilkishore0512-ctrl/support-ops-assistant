import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

from metrics import (  # noqa: E402
    contains_all, correctness, faithfulness, hit_at_k, percentile, recall_at_k, reciprocal_rank,
)

GOLDEN = Path(__file__).resolve().parents[2] / "evaluation" / "golden_set.yaml"
KB = Path(__file__).resolve().parents[2] / "data" / "kb"


def test_retrieval_metrics():
    retrieved = ["a.md", "b.md", "a.md", "c.md"]
    assert hit_at_k(retrieved, ["b.md"], 1) == 0.0
    assert hit_at_k(retrieved, ["b.md"], 2) == 1.0
    assert recall_at_k(retrieved, ["b.md", "d.md"], 5) == 0.5
    assert reciprocal_rank(retrieved, ["c.md"]) == pytest.approx(0.25)
    assert reciprocal_rank(retrieved, ["z.md"]) == 0.0


def test_correctness_uses_any_of_groups_and_ignores_markdown():
    answer = "Use `CORSMiddleware`; cookies are off by default (False) [1]."
    assert correctness(answer, [["cookies"], ["false"]]) == 1.0
    assert correctness(answer, [["cookies"], ["600"]]) == 0.5
    assert correctness(answer, [["corsmiddleware", "other"]]) == 1.0


def test_faithfulness():
    passages = ["Background tasks run after returning a response to the client."]
    assert faithfulness("Background tasks run after returning a response. [1]", passages) == 1.0
    assert faithfulness("Background tasks run after returning a response. Celery is required for everything here.", passages) == 0.5
    assert faithfulness("I don't", passages) is None


def test_helpers():
    assert contains_all("mobile_app 152.94 hours", ["mobile_app", "152.94"])
    assert not contains_all("mobile_app", ["mobile_app", "152.94"])
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3


def test_golden_set_is_well_formed():
    questions = yaml.safe_load(GOLDEN.read_text())["questions"]
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)) and len(questions) >= 20
    cats = [q["category"] for q in questions]
    assert cats.count("unanswerable") >= 3 and cats.count("tickets") >= 3
    for q in questions:
        if q["category"].startswith("docs"):
            assert q["key_facts"] and all(isinstance(g, list) for g in q["key_facts"])
            for src in q["expected_sources"]:
                assert (KB / src).exists(), src
        if q["category"] == "tickets":
            assert q["expected_answer"]

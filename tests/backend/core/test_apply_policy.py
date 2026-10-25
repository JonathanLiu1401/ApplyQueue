"""The apply prompt must demand every question be answered and must not skip fields."""
import re
from pathlib import Path

from core.apply_policy import apply_policy_text

REPO = Path(__file__).resolve().parents[3]


def test_policy_requires_answering_optional_questions():
    t = apply_policy_text()
    assert "ANSWER EVERY QUESTION" in t
    assert "INCLUDING OPTIONAL ONES" in t
    assert "TRUTHFUL" in t
    assert "decline" in t.lower() and "MUST still be selected" in t
    assert "report_needs_human" in t
    assert "NOT evidence" in t


def test_policy_has_no_skip_rule_or_password():
    t = apply_policy_text()
    assert not re.search(r"skip (it|the field|this field)", t, re.I)
    assert not re.search(r"after\s+5\s+(steps|attempts)", t, re.I)
    assert "JobApp" + "2026" not in t
    assert "fill_site_password" in t


def test_apply_runner_uses_policy_and_drops_skip_rule():
    src = (REPO / "cli" / "apply_jobs.py").read_text(encoding="utf-8")
    assert "apply_policy_text" in src
    assert not re.search(r"skip (it|the field|this field) and move on", src, re.I)

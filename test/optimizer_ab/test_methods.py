from pathlib import Path

import yaml


def test_cross_review_guided_method_is_registered():
    cfg = yaml.safe_load(Path("eval/optimizer_ab/test_set.yaml").read_text())

    assert "cross_review_guided" in cfg["methods"]


def test_cross_review_guided_skill_contains_review_factors():
    text = Path("eval/optimizer_ab/methods/cross_review_guided/SKILL.md").read_text()

    for phrase in [
        "Cross-Review-Guided Factor Protocol",
        "ADX 18-23",
        "EMA slow-region",
        "ATR stop",
        "validation trades < 20",
        "single-trade concentration",
    ]:
        assert phrase in text

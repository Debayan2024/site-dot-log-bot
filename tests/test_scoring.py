import json
from pathlib import Path

from eval.scoring import score_sample
from schema import MaterialMove, SiteUpdate, WorkItem

EXPECTED = {
    "work_items": [{"task": "false ceiling", "quantity": 40, "unit": "sq ft"}],
    "labour_count": 6,
    "materials": [{"material": "cement", "quantity": 2, "unit": "bags", "direction": "issued"}],
}


def pred(**kw):
    base = dict(
        work_items=[WorkItem(task="false ceiling", quantity=40, unit="sqft")],
        labour_count=6,
        materials=[MaterialMove(material="cement", quantity=2, unit="bags", direction="issued")],
    )
    base.update(kw)
    return SiteUpdate(**base)


def test_perfect_is_exact():
    assert score_sample(EXPECTED, pred()).exact


def test_direction_error_scored_as_direction_not_missing():
    r = score_sample(EXPECTED, pred(materials=[MaterialMove(material="cement", quantity=2, unit="bags", direction="received")]))
    bad = [l for l, ok, _ in r.fields if not ok]
    assert bad == ["mat[cement].direction"]


def test_hallucinated_labour_and_items_are_extras():
    exp = {"work_items": EXPECTED["work_items"]}
    r = score_sample(exp, pred())
    assert not r.exact and any("labour_count" in e for e in r.extras) and any("material" in e for e in r.extras)


def test_clarification_sample_passes_only_if_model_asks():
    exp = {"needs_clarification": True}
    assert score_sample(exp, SiteUpdate(needs_clarification="Kya kaam hua?")).exact
    assert not score_sample(exp, pred()).exact


def test_unit_mismatch_detected():
    r = score_sample(EXPECTED, pred(work_items=[WorkItem(task="false ceiling", quantity=40, unit="m")]))
    assert not r.exact


def test_samples_file_is_well_formed():
    path = Path(__file__).resolve().parent.parent / "eval" / "samples.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) >= 25
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["message"] and "expected" in r

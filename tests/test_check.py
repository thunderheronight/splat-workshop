"""The recording check's rating (splat_workshop/check.py): per-second measurements in, findings out.

Rows are made by hand with values clearly on one side of each limit, so the tests say what a user
gets for a recording that is, say, 50 % dark – not what the code happens to compute."""
import pytest

from splat_workshop import check

GOOD = {"bright": 120, "detail": 7.0, "motion": 10.0, "over": 0.0, "cast": 5, "rgb": (120, 120, 120),
        "right": {"bright": 120, "detail": 7.0}}


def rows(n=60, **changes_by_second):
    """n good seconds; changes_by_second: {"dark": range(...), ...} turns those seconds bad in one way."""
    out = [dict(GOOD, right=dict(GOOD["right"])) for _ in range(n)]
    for kind, secs in changes_by_second.items():
        for i in secs:
            r = out[i]
            if kind == "dark":
                r["bright"] = r["right"]["bright"] = 20
            elif kind == "flat":
                r["detail"] = r["right"]["detail"] = 1.0
            elif kind == "fast":
                r["motion"] = 80.0
            elif kind == "over":
                r["over"] = 0.2
            elif kind == "eye":                       # one lens covered
                r["right"] = {"bright": 30, "detail": 0.5}
    return out


def findings(res):
    return {f["code"]: f["level"] for f in res["findings"]}


def test_a_good_recording():
    res = check.evaluate(rows(), 60)
    assert res["level"] == "ok" and res["findings"] == [] and set(res["timeline"]) == {"ok"}
    assert res["summary"] == {"bright": 120, "detail": 7.0, "seconds": 60}


def test_nothing_measured():
    assert check.evaluate([], 0)["level"] == "bad"


def test_too_dark_overall():
    dark = [dict(r, bright=20, right={"bright": 20, "detail": 7.0}) for r in rows()]    # both lenses
    assert findings(check.evaluate(dark, 60)) == {"too_dark": "bad"}


@pytest.mark.parametrize("secs,level", [(range(0, 30), "bad"),      # half the recording dark
                                         (range(0, 24), "warn"),     # 40 %: not more than 40 %
                                         (range(0, 3), "warn")])
def test_dark_stretches(secs, level):
    res = check.evaluate(rows(dark=secs), 60)
    assert findings(res)["dark"] == level
    f = next(f for f in res["findings"] if f["code"] == "dark")
    assert f["secs"] == len(secs) and f["spans"] == [(secs[0], secs[-1])]
    assert res["timeline"][secs[0]] == "dark"


@pytest.mark.parametrize("secs,level", [(range(10, 40), "bad"), (range(10, 15), "warn"), (range(10, 13), "info")])
def test_plain_surfaces(secs, level):
    # 30 s = 50 %: bad; 5 s = 8 %: warn; 3 s = 5 %: only a note
    assert findings(check.evaluate(rows(flat=secs), 60))["flat"] == level


def test_a_single_plain_second_is_not_worth_a_finding():
    assert "flat" not in findings(check.evaluate(rows(flat=[10]), 60))


@pytest.mark.parametrize("secs,level", [(range(0, 12), "warn"), (range(0, 5), "info")])
def test_fast_pans(secs, level):
    assert findings(check.evaluate(rows(fast=secs), 60))["fast"] == level


def test_a_covered_lens_needs_three_seconds():
    assert findings(check.evaluate(rows(eye=range(5, 8)), 60))["cameras_differ"] == "warn"
    assert "cameras_differ" not in findings(check.evaluate(rows(eye=range(5, 7)), 60))


def test_overexposure_and_colour_cast_are_notes_only():
    tinted = [dict(r, cast=30, rgb=(160, 140, 80)) for r in rows(over=range(0, 5))]
    res = check.evaluate(tinted, 60)
    assert findings(res) == {"overexposed": "info", "color_cast": "info"}
    assert res["level"] == "ok"
    assert next(f for f in res["findings"] if f["code"] == "color_cast")["tone"] == "yellow"


@pytest.mark.parametrize("duration,code,level", [(15, "short", "warn"), (200, "long", "info")])
def test_length(duration, code, level):
    assert findings(check.evaluate(rows(), duration))[code] == level


def test_the_worst_finding_decides():
    res = check.evaluate(rows(dark=range(0, 30), fast=range(40, 45)), 60)
    assert res["level"] == "bad"
    assert res["timeline"][0] == "dark" and res["timeline"][42] == "fast" and res["timeline"][50] == "ok"


def test_spans():
    assert check._spans([False, True, True, False, True]) == [(1, 2), (4, 4)]
    assert check._spans([True, False, True, True], min_len=2) == [(2, 3)]

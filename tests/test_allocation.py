from __future__ import annotations

from dra.agent import per_question_quota


def test_quota_splits_evenly():
    assert per_question_quota(8, 4) == 2
    assert per_question_quota(12, 4) == 3
    assert per_question_quota(9, 4) == 2


def test_quota_caps_at_three():
    assert per_question_quota(40, 4) == 3


def test_quota_never_zero_when_sources_left():
    assert per_question_quota(1, 4) == 1


def test_quota_zero_when_exhausted():
    assert per_question_quota(0, 4) == 0
    assert per_question_quota(5, 0) == 0

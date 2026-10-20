from __future__ import annotations

import pytest

from dra.budget import Budget, BudgetExceeded


def test_tool_call_limit():
    b = Budget(max_tool_calls=2)
    b.spend_tool(2)
    assert not b.can_continue()
    with pytest.raises(BudgetExceeded):
        b.check()


def test_token_limit():
    b = Budget(max_tokens=100)
    b.spend_tokens(120)
    assert "token" in (b.exhausted_reason() or "")


def test_time_limit():
    b = Budget(max_seconds=0.001)
    import time

    time.sleep(0.01)
    assert "耗时" in (b.exhausted_reason() or "")


def test_rounds_do_not_block_mid_run():
    b = Budget(max_rounds=2)
    b.spend_round()
    b.spend_round()
    assert b.can_continue()


def test_snapshot_shape():
    b = Budget(max_tool_calls=5)
    b.spend_tool()
    snap = b.snapshot()
    assert snap["tool_calls"] == 1
    assert snap["limits"]["max_tool_calls"] == 5

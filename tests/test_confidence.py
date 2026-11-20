"""The gate between Needle's choice and execution."""

import pytest

from app.ai.confidence import ConfidencePolicy, Route
from app.tools.registry import Safety

P = ConfidencePolicy(execute_threshold=0.40, refuse_threshold=0.15)


@pytest.mark.parametrize("safety,conf,suppressed,escalation,route", [
    (Safety.SAFE, 0.95, False, None, Route.EXECUTE),
    (Safety.SAFE, 0.40, False, None, Route.EXECUTE),
    (Safety.SAFE, 0.30, False, None, Route.CONFIRM),          # medium -> ask
    (Safety.SAFE, 0.10, False, None, Route.REFUSE),           # low -> never run
    (Safety.SAFE, None, False, None, Route.CONFIRM),          # no calibration head
    (Safety.SAFE, 0.90, True, None, Route.CONFIRM),           # engine withheld it
    (Safety.SAFE, 0.05, True, None, Route.REFUSE),
    (Safety.CONFIRMATION_REQUIRED, 1.0, False, None, Route.CONFIRM),  # destructive always asks
    (Safety.CONFIRMATION_REQUIRED, 0.99, False, None, Route.CONFIRM),
    (Safety.BLOCKED, 1.0, False, None, Route.BLOCK),
    (Safety.CONFIRMATION_REQUIRED, 1.0, False, Safety.BLOCKED, Route.BLOCK),  # protected path
])
def test_routes(safety, conf, suppressed, escalation, route):
    assert P.decide(safety, conf, suppressed, escalation).route is route


def test_destructive_never_executes_regardless_of_threshold():
    permissive = ConfidencePolicy(execute_threshold=0.0, refuse_threshold=0.0)
    assert permissive.decide(Safety.CONFIRMATION_REQUIRED, 1.0).route is Route.CONFIRM

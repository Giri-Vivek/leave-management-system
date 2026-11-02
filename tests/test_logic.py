"""Pure-logic tests (no AWS calls).  Run:  AWS_DEFAULT_REGION=ap-south-1 pytest -q"""
import os
import sys
import time
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
import common  # noqa: E402


def test_working_days_skips_weekends():
    assert common.working_days(date(2026, 11, 2), date(2026, 11, 6)) == 5     # Mon-Fri
    assert common.working_days(date(2026, 11, 2), date(2026, 11, 8)) == 5     # through Sunday
    assert common.working_days(date(2026, 11, 7), date(2026, 11, 8)) == 0     # weekend only
    assert common.working_days(date(2026, 11, 6), date(2026, 11, 9)) == 2     # Fri + Mon


def test_available_formula():
    bal = {"total_quota": 18, "carried_forward": 4, "used": 6, "pending": 3}
    assert common.available(bal) == 13


def test_parse_date_rejects_garbage():
    with pytest.raises(common.ApiError):
        common.parse_date("31/12/2026")


@pytest.fixture(autouse=True)
def fake_secret(monkeypatch):
    monkeypatch.setattr(common, "_secret", lambda: b"unit-test-secret")


def test_token_roundtrip():
    t = common.sign_token("EMP001", "123-abc", "MANAGER", "APPROVE", 60)
    assert common.verify_token(t) == {"employee_id": "EMP001", "request_id": "123-abc",
                                      "stage": "MANAGER", "action": "APPROVE"}


def test_token_tamper_detected():
    t = common.sign_token("EMP001", "123-abc", "MANAGER", "APPROVE", 60)
    body, sig = t.rsplit(".", 1)
    forged = common.sign_token("EMP001", "123-abc", "MANAGER", "REJECT", 60).split(".")[0] + "." + sig
    with pytest.raises(common.ApiError) as e:
        common.verify_token(forged)
    assert e.value.status == 403


def test_token_expiry():
    t = common.sign_token("EMP001", "123-abc", "MANAGER", "APPROVE", -1)
    with pytest.raises(common.ApiError) as e:
        common.verify_token(t)
    assert e.value.status == 410


def test_request_ids_sort_by_time():
    a = common.new_request_id()
    time.sleep(0.01)
    assert common.new_request_id() > a

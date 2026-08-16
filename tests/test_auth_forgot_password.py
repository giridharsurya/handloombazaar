from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from api.auth import (
    ForgotPasswordRequest,
    _generate_otp_code,
    _forgot_password_store,
    request_forgot_password_otp,
    reset_password_with_otp,
    verify_forgot_password_otp,
)


def test_generate_otp_code_returns_six_digits():
    code = _generate_otp_code()

    assert len(code) == 6
    assert code.isdigit()


def test_verify_forgot_password_otp_matches_and_marks_verified():
    username = "testshop"
    email = "testshop@example.com"
    code = "123456"

    _forgot_password_store[(username, email)] = {"code": code, "expires_at": datetime.utcnow() + timedelta(minutes=5)}

    assert verify_forgot_password_otp(username, email, code) is True
    assert _forgot_password_store[(username, email)]["verified"] is True


def test_reset_password_with_otp_sets_new_password():
    username = "testshop"
    email = "testshop@example.com"
    code = "654321"
    account = type("UserStub", (), {"username": username, "password_hash": "old_hash", "updated_at": datetime.utcnow()})()
    _forgot_password_store[(username, email)] = {"code": code, "expires_at": datetime.utcnow() + timedelta(minutes=5), "verified": True}

    result = reset_password_with_otp(account, email, code, "NewStrongPassword@123")

    assert result == username
    assert account.password_hash != "old_hash"
    assert (username, email) not in _forgot_password_store


def test_request_forgot_password_otp_raises_when_email_send_fails(monkeypatch):
    username = "testshop"
    email = "testshop@example.com"

    monkeypatch.setattr(
        "api.auth._find_user_by_email",
        lambda session, email_value: (type("UserStub", (), {"username": username})(), None),
    )
    monkeypatch.setattr("api.auth._send_otp_via_azure_email", lambda *args, **kwargs: False)

    with pytest.raises(HTTPException, match="Unable to send OTP email"):
        request_forgot_password_otp(ForgotPasswordRequest(email=email))

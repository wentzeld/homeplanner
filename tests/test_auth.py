import pytest

from homeplanner.auth import LOCKOUT_SECONDS, MAX_FAILURES, SESSION_SECONDS, AuthStore, LockedOut


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def store(tmp_path=None, clock=None):
    s = AuthStore(tmp_path / "auth.json" if tmp_path else None, now=clock or Clock())
    s.load()
    return s


def test_no_pin_means_remote_disabled():
    s = store()
    assert not s.remote_enabled
    assert not s.check_pin("123456")
    assert not s.verify_token("anything")


@pytest.mark.parametrize("pin", ["12345", "123456789", "abcdef", "12 3456", ""])
def test_pin_must_be_6_to_8_digits(pin):
    with pytest.raises(ValueError, match="6 to 8 digits"):
        store().set_pin(pin)


def test_correct_and_wrong_pin():
    s = store()
    s.set_pin("246810")
    assert s.remote_enabled
    assert s.check_pin("246810")
    assert not s.check_pin("111111")
    assert s.attempts_left == MAX_FAILURES - 1


def test_lockout_after_five_wrong_then_expires():
    clock = Clock()
    s = store(clock=clock)
    s.set_pin("246810")
    for _ in range(MAX_FAILURES - 1):
        assert not s.check_pin("000000")
    with pytest.raises(LockedOut):
        s.check_pin("000000")
    with pytest.raises(LockedOut):  # even the right PIN is refused while locked
        s.check_pin("246810")
    clock.t += LOCKOUT_SECONDS + 1
    assert s.check_pin("246810")


def test_tokens_expire_and_are_tamper_proof():
    clock = Clock()
    s = store(clock=clock)
    s.set_pin("246810")
    token = s.issue_token()
    assert s.verify_token(token)
    expires, _, sig = token.partition(".")
    assert not s.verify_token(f"{int(expires) + 1000}.{sig}")  # extended expiry
    assert not s.verify_token(f"{expires}.{'0' * len(sig)}")
    assert not s.verify_token("garbage")
    clock.t += SESSION_SECONDS + 1
    assert not s.verify_token(token)


def test_changing_pin_or_sign_out_all_invalidates_tokens():
    s = store()
    s.set_pin("246810")
    t1 = s.issue_token()
    s.set_pin("135790")
    assert not s.verify_token(t1)
    t2 = s.issue_token()
    s.sign_out_all()
    assert not s.verify_token(t2)
    assert s.remote_enabled


def test_disable_forgets_pin():
    s = store()
    s.set_pin("246810")
    t = s.issue_token()
    s.disable()
    assert not s.remote_enabled
    assert not s.verify_token(t)
    assert not s.check_pin("246810")


def test_persisted_hashed_and_private(tmp_path):
    s = store(tmp_path)
    s.set_pin("246810")
    token = s.issue_token()
    text = (tmp_path / "auth.json").read_text()
    assert "246810" not in text
    assert oct((tmp_path / "auth.json").stat().st_mode & 0o777) == "0o600"
    reloaded = store(tmp_path)
    assert reloaded.check_pin("246810")
    assert reloaded.verify_token(token)

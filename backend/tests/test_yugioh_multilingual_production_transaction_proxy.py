from app.scripts import rollout_yugioh_multilingual_production_v2 as rollout


class _FakeConnection:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.cursor_calls = 0

    def cursor(self, *args, **kwargs):
        self.cursor_calls += 1
        return object()

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


def test_write_transaction_view_accepts_nested_set_session_without_committing():
    real = _FakeConnection()
    proxy = rollout._WriteTransactionView(real)

    assert proxy.set_session(readonly=False, autocommit=False) is None
    assert proxy.commit() is None
    assert real.commit_calls == 0


def test_write_transaction_view_rolls_back_outer_transaction_on_nested_failure():
    real = _FakeConnection()
    proxy = rollout._WriteTransactionView(real)

    proxy.rollback()

    assert real.rollback_calls == 1

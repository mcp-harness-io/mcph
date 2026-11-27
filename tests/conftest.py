import pytest


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """Keep every test away from the real state directory, where OAuth tokens live."""
    state = tmp_path / "mcph-state"
    monkeypatch.setenv("MCPH_STATE_DIR", str(state))
    return state

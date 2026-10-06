import pytest


@pytest.fixture(scope="module")
def saved_template(tmp_path_factory):
    """Build once per test module; saved gives every test its own disposable copy."""
    from test_verify import _build_saved

    root = tmp_path_factory.mktemp("saved-template")
    with pytest.MonkeyPatch.context() as monkeypatch:
        directory, _ = _build_saved(root, monkeypatch)
    return root, directory.relative_to(root)


@pytest.fixture
def data_directory(request, tmp_path, monkeypatch):
    """Run each CLI with competing directory settings, including a relative flag."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("REPS_DATA_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("REPS_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    if request.param == "flag":
        return tmp_path / "data dir's", ["--data-dir", "data dir's"]
    if request.param == "env":
        return tmp_path / "env", []
    monkeypatch.delenv("REPS_DATA_DIR")
    if request.param == "xdg":
        return tmp_path / "xdg/reps", []
    monkeypatch.delenv("XDG_DATA_HOME")
    return tmp_path / "user/.local/share/reps", []

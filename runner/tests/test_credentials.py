"""Local credential store: resolution (which env to inject for a run's llm params),
profile storage (0600 toml), the interactive ladder, and the `adb-runner credentials`
CLI."""

import io
import os
import stat

import pytest

from adb_runner import credentials


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "credentials.toml"
    monkeypatch.setenv("ADB_CREDENTIALS_FILE", str(path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return path


def test_preferences_location_uses_xdg_and_ignores_removed_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ADB_PREFERENCES_FILE", str(tmp_path / "ignored.toml"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert credentials.prefs_path() == tmp_path / "config/adb/preferences.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert credentials.prefs_path() == tmp_path / "home/.config/adb/preferences.toml"


def _stdin(monkeypatch, text):
    import sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


# -- storage ---------------------------------------------------------------------------

def test_save_load_roundtrip_is_0600(cfg):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "sk-secret",
                                             "OPENAI_BASE_URL": "http://h/v1"},
                                 "work": {"OPENAI_API_KEY": "sk-work"}}})
    assert credentials.load() == {
        "openai": {"default": {"OPENAI_API_KEY": "sk-secret",
                               "OPENAI_BASE_URL": "http://h/v1"},
                   "work": {"OPENAI_API_KEY": "sk-work"}}
    }
    mode = stat.S_IMODE(os.stat(cfg).st_mode)
    assert mode == 0o600, oct(mode)


def test_flat_legacy_section_reads_as_default_profile(cfg):
    # the pre-profiles layout: bare values directly in the set's section
    cfg.write_text('[openai]\nOPENAI_API_KEY = "sk-old"\n\n'
                   '[anthropic]\nANTHROPIC_API_KEY = "sk-a"\n')
    os.chmod(cfg, 0o600)
    assert credentials.load() == {
        "openai": {"default": {"OPENAI_API_KEY": "sk-old"}},
        "anthropic": {"default": {"ANTHROPIC_API_KEY": "sk-a"}},
    }


def test_missing_file_loads_empty(cfg):
    assert credentials.load() == {}


def _manifest(model_type):
    return {"name": "x", "params": {"model": {"type": model_type}}}


# -- resolution ------------------------------------------------------------------------

def test_env_for_run_resolves_bare_llm(cfg):
    credentials.save({"openai": {"default": {"OPENAI_BASE_URL": "http://llama/v1",
                                             "OPENAI_API_KEY": "k"}}})
    manifest = _manifest({"kind": "llm"})
    env = credentials.env_for_run(manifest, {"model": "openai/qwen3.5-9b"})
    assert env == {"OPENAI_BASE_URL": "http://llama/v1", "OPENAI_API_KEY": "k"}


def test_env_for_run_selections_pick_profiles_atomically(cfg):
    credentials.save({"openai": {
        "default": {"OPENAI_API_KEY": "sk-real", "OPENAI_BASE_URL": "https://api.openai.com/v1"},
        "work": {"OPENAI_API_KEY": "sk-work"},  # deliberately no base URL
    }})
    manifest = _manifest({"kind": "llm"})
    realized = {"model": "openai/gpt"}
    assert credentials.env_for_run(manifest, realized)["OPENAI_API_KEY"] == "sk-real"
    picked = credentials.env_for_run(manifest, realized, {"openai": "work"})
    # profiles are ATOMIC: work's missing base URL does NOT fall back to default's
    assert picked == {"OPENAI_API_KEY": "sk-work"}


def test_mock_and_unconfigured_yield_nothing(cfg):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "k"}}})
    manifest = _manifest({"kind": "llm"})
    assert credentials.env_for_run(manifest, {"model": "mock/model"}) == {}
    # a set with no stored config contributes nothing
    assert credentials.env_for_run(manifest, {"model": "anthropic/claude"}) == {}


def test_resolves_llm_inside_list_of_struct(cfg):
    # werewolf shape: players = listOf (struct { model = llm; role = enum; })
    credentials.save({"anthropic": {"default": {"ANTHROPIC_API_KEY": "a"}},
                      "openai": {"default": {"OPENAI_API_KEY": "o"}}})
    manifest = {
        "name": "werewolf",
        "params": {
            "players": {
                "type": {
                    "kind": "list",
                    "of": {"kind": "struct", "fields": {
                        "model": {"kind": "llm"}, "role": {"kind": "enum", "values": ["x"]},
                    }},
                }
            }
        },
    }
    realized = {"players": [
        {"model": "openai/gpt", "role": "x"},
        {"model": "anthropic/claude", "role": "x"},
        {"model": "mock/a", "role": "x"},
    ]}
    env = credentials.env_for_run(manifest, realized)
    assert env == {"OPENAI_API_KEY": "o", "ANTHROPIC_API_KEY": "a"}


def test_resolves_param_wrapped_struct_field(cfg):
    # concordia shape: agents = listOf (struct { model = param llm { suggestions }; })
    # — the field is {"type": {...}, "suggestions": [...]}, not a bare type descriptor
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "k"}}})
    manifest = {
        "name": "concordia",
        "params": {
            "agents": {
                "type": {
                    "kind": "list",
                    "of": {"kind": "struct", "fields": {
                        "name": {"kind": "str"},
                        "model": {"type": {"kind": "llm"}, "suggestions": ["mock/model"]},
                    }},
                }
            }
        },
    }
    realized = {"agents": [{"name": "A", "model": "openai/qwen3.5-9b"},
                           {"name": "B", "model": ""}]}
    assert credentials.env_for_run(manifest, realized) == {"OPENAI_API_KEY": "k"}


def test_openai_api_service_ids_route_to_named_section(cfg):
    # inspect's OpenAI-compatible services: openai-api/<service>/<model> reads
    # <SERVICE>_API_KEY/_BASE_URL — so a named credential set serves those ids,
    # letting a real vendor key and a self-hosted server coexist
    credentials.save({"llama": {"default": {"LLAMA_API_KEY": "k",
                                            "LLAMA_BASE_URL": "http://l/v1"}},
                      "openai": {"default": {"OPENAI_API_KEY": "real"}}})
    manifest = _manifest({"kind": "llm"})
    env = credentials.env_for_run(manifest, {"model": "openai-api/llama/qwen3.5-9b"})
    assert env == {"LLAMA_API_KEY": "k", "LLAMA_BASE_URL": "http://l/v1"}
    # a named service nobody configured is never gated (it may need nothing)
    assert credentials.missing_sets(manifest, {"model": "openai-api/other/m"}) == []
    assert credentials.section_for("openai-api/llama/qwen") == "llama"
    assert credentials.section_for("openai/gpt-4o") == "openai"
    assert credentials.section_for("mockllm/model") is None


def test_missing_sets_gate(cfg, monkeypatch):
    manifest = _manifest({"kind": "llm"})
    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # built-in name, nothing configured anywhere → reported
    assert credentials.missing_sets(manifest, {"model": "openai/q"}) == ["openai"]
    # mocks (both spellings) route to no credential set
    assert credentials.missing_sets(manifest, {"model": "mock/model"}) == []
    assert credentials.missing_sets(manifest, {"model": "mockllm/model"}) == []
    # unknown prefix (a custom service may legitimately need nothing) → never blocked
    assert credentials.missing_sets(manifest, {"model": "custom/model"}) == []
    # a host env var does NOT satisfy the gate — the store is the only credential
    # source, and an accidentally exported key must not skip the setup prompt
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    assert credentials.missing_sets(manifest, {"model": "openai/q"}) == ["openai"]
    monkeypatch.delenv("OPENAI_API_KEY")
    # any stored profile satisfies it — not only default
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "k"}}})
    assert credentials.missing_sets(manifest, {"model": "openai/q"}) == []


@pytest.mark.parametrize("provider,model", [
    ("vllm", "Qwen/Qwen2.5-7B-Instruct"),
    ("ollama", "qwen2.5:7b-instruct-fp16"),
])
def test_selfhosted_requires_profile_and_accepts_keyless_endpoint(cfg, monkeypatch, provider, model):
    manifest = _manifest({"kind": "llm"})
    params = {"model": f"{provider}/{model}"}
    base_var = f"{provider.upper()}_BASE_URL"
    monkeypatch.setenv(base_var, "http://ambient.invalid/v1")
    assert credentials.missing_sets(manifest, params) == [provider]
    with pytest.raises(ValueError, match=f"credentials set {provider}"):
        credentials.resolve_run_credentials(manifest, params, experiment="exp", interactive=False)
    endpoint = {base_var: "http://localhost:8000/v1"}
    credentials.save({provider: {"default": endpoint}})
    assert credentials.missing_sets(manifest, params) == []
    assert credentials.resolve_run_credentials(
        manifest, params, experiment="exp", interactive=False
    ) == endpoint


# -- the interactive ladder ------------------------------------------------------------

def _resolve(manifest, realized, *, experiment="exp", interactive=True):
    return credentials.resolve_run_credentials(
        manifest, realized, experiment=experiment, interactive=interactive)


def test_first_use_save_yes_names_default(cfg, monkeypatch, capsys):
    manifest = _manifest({"kind": "llm"})
    # values, save? [Y/n] → Enter (yes), name → Enter (default)
    _stdin(monkeypatch, "sk-k\nhttp://h/v1\n\n\n")
    env = _resolve(manifest, {"model": "openai/q"})
    assert env == {"OPENAI_API_KEY": "sk-k", "OPENAI_BASE_URL": "http://h/v1"}
    assert credentials.load()["openai"]["default"]["OPENAI_API_KEY"] == "sk-k"


def test_first_use_save_yes_with_custom_name(cfg, monkeypatch, capsys):
    manifest = _manifest({"kind": "llm"})
    _stdin(monkeypatch, "sk-k\nhttp://h/v1\ny\nwork\n")
    env = _resolve(manifest, {"model": "openai/q"})
    assert env["OPENAI_API_KEY"] == "sk-k"
    assert list(credentials.load()["openai"]) == ["work"]


def test_first_use_decline_feeds_run_but_stores_nothing(cfg, monkeypatch, capsys):
    manifest = _manifest({"kind": "llm"})
    _stdin(monkeypatch, "sk-session\nhttp://h/v1\nn\n")
    env = _resolve(manifest, {"model": "openai/q"})
    assert env["OPENAI_API_KEY"] == "sk-session"
    assert credentials.load() == {}


def test_ladder_lone_default_is_silent(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "k"}}})
    _stdin(monkeypatch, "")  # no prompts expected — empty stdin would fail any read
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    assert env == {"OPENAI_API_KEY": "k"}


def test_ladder_picker_enter_takes_default(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    _stdin(monkeypatch, "\nn\n")  # picker: Enter → default; remember? → n
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    assert env == {"OPENAI_API_KEY": "d"}
    assert not credentials.prefs_path().exists()


def test_ladder_picker_choice_and_remember(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    _stdin(monkeypatch, "work\ny\n")  # picker: work; remember? → y
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"},
                   experiment="concordia")
    assert env == {"OPENAI_API_KEY": "w"}
    # remembered: the next resolve prompts for NOTHING
    _stdin(monkeypatch, "")
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"},
                   experiment="concordia")
    assert env == {"OPENAI_API_KEY": "w"}
    # ...but only for that experiment
    _stdin(monkeypatch, "\nn\n")
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"},
                   experiment="werewolf")
    assert env == {"OPENAI_API_KEY": "d"}
    prefs_text = credentials.prefs_path().read_text()
    assert 'openai = "work"' in prefs_text and "sk-" not in prefs_text


def test_ladder_dangling_preference_reasks(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    _stdin(monkeypatch, "work\ny\n")
    _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"}, experiment="concordia")
    assert credentials.credentials_cli(["remove", "openai.work"]) == 0
    # remembered profile is gone → falls back to the picker (default remains)
    _stdin(monkeypatch, "\nn\n")
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"},
                   experiment="concordia")
    assert env == {"OPENAI_API_KEY": "d"}
    assert "no longer exists" in capsys.readouterr().err


def test_ladder_picker_new_creates_and_uses(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "w"}}})
    # picker: new → values → name (no suggestion) → remember? n
    _stdin(monkeypatch, "new\nsk-p\nhttp://p/v1\npersonal\nn\n")
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    assert env["OPENAI_API_KEY"] == "sk-p"
    assert "personal" in credentials.load()["openai"]


def test_headless_default_else_refuse(cfg, monkeypatch, capsys):
    manifest = _manifest({"kind": "llm"})
    # nothing configured → actionable refusal, never a prompt
    with pytest.raises(ValueError, match="credentials set openai"):
        _resolve(manifest, {"model": "openai/q"}, interactive=False)
    # named profiles, no default, nothing remembered → refuse with the fix
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "w"}}})
    with pytest.raises(ValueError, match="no 'default'"):
        _resolve(manifest, {"model": "openai/q"}, interactive=False)
    # a default profile resolves silently
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    env = _resolve(manifest, {"model": "openai/q"}, interactive=False)
    assert env == {"OPENAI_API_KEY": "d"}


def test_headless_remembered_choice_wins(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "w"}}})
    _stdin(monkeypatch, "work\ny\n")
    _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"}, experiment="concordia")
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"},
                   experiment="concordia", interactive=False)
    assert env == {"OPENAI_API_KEY": "w"}


# -- the `adb-runner credentials` CLI --------------------------------------------------

def _cli(argv, stdin="", monkeypatch=None):
    if monkeypatch is not None:
        _stdin(monkeypatch, stdin)
    return credentials.credentials_cli(argv)


def test_cli_set_via_stdin_then_list(cfg, monkeypatch, capsys):
    # line per prompt (non-tty): key, base_url, then the profile name (Enter=default)
    code = _cli(["set", "openai"], stdin="sk-topsecret\nhttp://llama/v1\n\n",
                monkeypatch=monkeypatch)
    assert code == 0
    assert credentials.load()["openai"]["default"] == {
        "OPENAI_API_KEY": "sk-topsecret", "OPENAI_BASE_URL": "http://llama/v1"
    }
    capsys.readouterr()
    assert _cli(["list"]) == 0
    out = capsys.readouterr().out
    # the secret value is never printed; the endpoint (non-secret) is shown
    assert "sk-topsecret" not in out
    assert "openai.default:" in out
    assert "OPENAI_API_KEY=<set>" in out
    assert "http://llama/v1" in out


def test_cli_set_dotted_targets_profile_without_name_prompt(cfg, monkeypatch, capsys):
    code = _cli(["set", "openai.work"], stdin="sk-w\nhttp://w/v1\n",
                monkeypatch=monkeypatch)
    assert code == 0
    assert credentials.load()["openai"] == {
        "work": {"OPENAI_API_KEY": "sk-w", "OPENAI_BASE_URL": "http://w/v1"}
    }
    # reserved / malformed profile names are refused up front
    assert _cli(["set", "openai.new"]) == 2
    assert _cli(["set", "openai.Work"]) == 2


def test_cli_set_rejects_schemeless_base_url(cfg, monkeypatch, capsys):
    # a bare host would only fail later, inside a client ("unknown url type") — the
    # set command is where the fix is cheap, so it refuses and shows the shape
    code = _cli(["set", "openai"], stdin="sk-k\nllama.forest.local\n", monkeypatch=monkeypatch)
    assert code == 2
    assert credentials.load() == {}
    assert "http://llama.local:11434/v1" in capsys.readouterr().err


def test_cli_set_refuses_values_on_argv(cfg, capsys):
    # there deliberately is NO KEY=VALUE form: argv is visible in ps / shell history
    assert _cli(["set", "openai", "OPENAI_API_KEY=sk-leaked"]) == 2
    assert credentials.load() == {}
    assert "never taken on the command line" in capsys.readouterr().err


def test_cli_set_empty_prompt_adopts_base_url_default(cfg, monkeypatch):
    # key entered, base-url prompt left empty → the shown default is stored
    code = _cli(["set", "openai"], stdin="sk-k\n\n\n", monkeypatch=monkeypatch)
    assert code == 0
    assert credentials.load()["openai"]["default"] == {
        "OPENAI_API_KEY": "sk-k", "OPENAI_BASE_URL": "https://api.openai.com/v1"
    }


def test_registry_base_urls_are_prompted_with_defaults(cfg, monkeypatch):
    # the canonical registry declares a base-URL var for every built-in — key
    # entered, base prompt left empty → the default origin is stored, so a proxy/
    # regional endpoint is one retype away without a config file edit
    code = _cli(["set", "groq"], stdin="gsk-k\n\n\n", monkeypatch=monkeypatch)
    assert code == 0
    assert credentials.load()["groq"]["default"] == {
        "GROQ_API_KEY": "gsk-k", "GROQ_BASE_URL": "https://api.groq.com/openai/v1"
    }


def test_cli_set_unknown_name_prompts_conventional_names(cfg, monkeypatch, capsys):
    code = _cli(["set", "myprov"], stdin="k-123\nhttps://my.host/v1\n\n",
                monkeypatch=monkeypatch)
    assert code == 0
    assert credentials.load()["myprov"]["default"] == {
        "MYPROV_API_KEY": "k-123", "MYPROV_BASE_URL": "https://my.host/v1"
    }


def test_cli_remove_set_and_profile(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    assert _cli(["remove", "openai.work"]) == 0
    assert list(credentials.load()["openai"]) == ["default"]
    assert _cli(["remove", "openai"]) == 0
    assert credentials.load() == {}
    assert _cli(["remove", "openai"]) == 1


def test_group_readable_store_is_refused(cfg, monkeypatch, capsys):
    # ssh/pgpass precedent: a leaky store is refused with the fix, never warned past
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "sk-k"}}})
    os.chmod(cfg, 0o644)
    with pytest.raises(ValueError, match="chmod 600"):
        credentials.load()
    # the gate refuses too — a leaky file must not silently satisfy a run
    with pytest.raises(ValueError, match="chmod 600"):
        _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"}, interactive=False)
    assert _cli(["list"]) == 2
    assert "chmod 600" in capsys.readouterr().err
    # and the fix works
    os.chmod(cfg, 0o600)
    assert credentials.load()["openai"]["default"]["OPENAI_API_KEY"] == "sk-k"


# -- interactive paths from the UX diagram not covered above ---------------------------

def test_mixed_flat_and_nested_section_reads(cfg):
    cfg.write_text('[openai]\nOPENAI_API_KEY = "sk-flat"\n\n'
                   '[openai.work]\nOPENAI_API_KEY = "sk-work"\n')
    os.chmod(cfg, 0o600)
    assert credentials.load() == {
        "openai": {"default": {"OPENAI_API_KEY": "sk-flat"},
                   "work": {"OPENAI_API_KEY": "sk-work"}}
    }


def test_multi_set_first_use_and_picker_in_one_invocation(cfg, monkeypatch, capsys):
    # anthropic unconfigured (first-use) + openai with named profiles (picker),
    # resolved in one run — sets are handled in sorted order (anthropic first)
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    manifest = {"name": "x", "params": {"a": {"type": {"kind": "llm"}},
                                        "b": {"type": {"kind": "llm"}}}}
    realized = {"a": "anthropic/claude", "b": "openai/gpt"}
    # anthropic: key, base (Enter=default), save? (Enter=yes), name (Enter=default);
    # openai: picker → work, remember? → n
    _stdin(monkeypatch, "sk-a\n\n\n\nwork\nn\n")
    env = _resolve(manifest, realized)
    assert env["ANTHROPIC_API_KEY"] == "sk-a"
    assert env["OPENAI_API_KEY"] == "w"
    assert credentials.load()["anthropic"]["default"]["ANTHROPIC_API_KEY"] == "sk-a"


def test_picker_unknown_choice_complains(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    _stdin(monkeypatch, "wrok\n")  # scripted input can't retype — complain and fail
    with pytest.raises(ValueError, match="wrok"):
        _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    err = capsys.readouterr().err
    # the printed line restates the constraint and does not editorialize about the
    # rejected answer; the value itself rides on the raised error, for scripted callers
    assert "enter a number between 1 and 3, or a profile name" in err
    assert "wrok" not in err


def test_picker_empty_without_default_reasks_cleanly(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "w"},
                                 "personal": {"OPENAI_API_KEY": "p"}}})
    _stdin(monkeypatch, "\n")  # Enter means nothing here — there is no default
    with pytest.raises(ValueError, match="needs a choice"):
        _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    err = capsys.readouterr().err
    # the numbered block, with "new" as an ordinary entry; nothing is pre-filled on the
    # prompt line, because Enter selects nothing here
    assert " [1] personal\n [2] work\n [3] set up a new profile" in err
    assert "choice: " in err and "choice [" not in err
    assert "enter a number between 1 and 3, or a profile name" in err


def test_picker_accepts_number_or_name(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    # [1] default, [2] work, [3] set up a new profile — the number picks the entry
    _stdin(monkeypatch, "2\nn\n")
    assert _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"}) == {
        "OPENAI_API_KEY": "w"}
    err = capsys.readouterr().err
    assert " [1] default\n [2] work\n [3] set up a new profile" in err
    assert "choice [1]: " in err  # the pre-filled bracket is what Enter gives you
    assert "adb: using openai.work" in err
    # the new-profile entry is reachable by its number, not only by the word
    _stdin(monkeypatch, "3\nsk-p\nhttp://p/v1\npersonal\nn\n")
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    assert env["OPENAI_API_KEY"] == "sk-p"
    assert "personal" in credentials.load()["openai"]


def test_first_use_rejects_reserved_profile_name(cfg, monkeypatch, capsys):
    _stdin(monkeypatch, "sk-k\nhttp://h/v1\ny\nnew\n")  # "new" is the picker keyword
    with pytest.raises(ValueError, match="invalid profile name"):
        _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    assert credentials.load() == {}
    assert "not ['new']" in capsys.readouterr().err


def test_picker_new_rejects_taken_name(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "w"}}})
    _stdin(monkeypatch, "new\nsk-2\nhttp://h/v1\nwork\n")  # name collides
    with pytest.raises(ValueError, match="invalid input"):
        _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"})
    assert "already exists" in capsys.readouterr().err
    assert list(credentials.load()["openai"]) == ["work"]  # nothing half-written


def test_headless_dangling_pref_falls_back_to_default(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    _stdin(monkeypatch, "work\ny\n")
    _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"}, experiment="concordia")
    assert credentials.credentials_cli(["remove", "openai.work"]) == 0
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai/q"},
                   experiment="concordia", interactive=False)
    assert env == {"OPENAI_API_KEY": "d"}
    assert "no longer exists" in capsys.readouterr().err


def test_cli_undotted_save_as_keeps_current_values(cfg, monkeypatch, capsys):
    # undotted `set` edits default: Enter-past-the-rest keeps current values, and
    # a custom name at the end is a save-as (default remains untouched)
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "sk-d",
                                             "OPENAI_BASE_URL": "http://h/v1"}}})
    code = _cli(["set", "openai"], stdin="\n\nbackup\n", monkeypatch=monkeypatch)
    assert code == 0
    store = credentials.load()["openai"]
    assert store["backup"] == store["default"] == {
        "OPENAI_API_KEY": "sk-d", "OPENAI_BASE_URL": "http://h/v1"}


def test_custom_set_with_profiles_goes_through_picker(cfg, monkeypatch, capsys):
    # the ladder applies to NAMED sets (openai-api/<name>/... ids) too, not only
    # registry built-ins
    credentials.save({"llama": {"default": {"LLAMA_API_KEY": "d"},
                                "work": {"LLAMA_API_KEY": "w"}}})
    _stdin(monkeypatch, "work\nn\n")
    env = _resolve(_manifest({"kind": "llm"}), {"model": "openai-api/llama/qwen"})
    assert env == {"LLAMA_API_KEY": "w"}


# -- machine faces (list --json / set --json / remember) -------------------------------

def _list_json(capsys):
    import json
    assert credentials.credentials_cli(["list", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_list_json_masks_secrets_by_type(cfg, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "sk-secret",
                                             "OPENAI_BASE_URL": "http://h/v1"}}})
    doc = _list_json(capsys)
    # literal True, not a string: a consumer can't accidentally round-trip the mask
    assert doc["store"]["openai"]["default"]["OPENAI_API_KEY"] is True
    assert doc["store"]["openai"]["default"]["OPENAI_BASE_URL"] == "http://h/v1"
    assert "sk-secret" not in capsys.readouterr().out
    assert doc["path"] == str(cfg)
    # provider templates ride along so a GUI renders the CLI's prompts
    rows = doc["providers"]["openai"]
    assert rows[0]["secret"] is True and rows[0]["key"] == "OPENAI_API_KEY"
    assert "mock" in doc["mock_prefixes"] or doc["mock_prefixes"]


def test_list_json_reports_leaky_store_as_problem(cfg, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "k"}}})
    os.chmod(cfg, 0o644)
    doc = _list_json(capsys)
    assert "chmod 600" in doc["problem"]
    assert doc["store"] == {}  # nothing served from a leaky file


def test_set_json_merges_like_the_prompts(cfg, monkeypatch, capsys):
    import json as _json
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "sk-old",
                                          "OPENAI_BASE_URL": "http://old/v1"}}})
    # non-empty overwrites, absent keeps — a GUI can fix a URL without holding the key
    _stdin(monkeypatch, _json.dumps({"OPENAI_BASE_URL": "http://new/v1"}))
    assert credentials.credentials_cli(["set", "openai.work", "--json"]) == 0
    assert _json.loads(capsys.readouterr().out)["saved"] == "openai.work"
    assert credentials.load()["openai"]["work"] == {
        "OPENAI_API_KEY": "sk-old", "OPENAI_BASE_URL": "http://new/v1"}
    # explicit null deletes
    _stdin(monkeypatch, _json.dumps({"OPENAI_BASE_URL": None}))
    assert credentials.credentials_cli(["set", "openai.work", "--json"]) == 0
    assert credentials.load()["openai"]["work"] == {"OPENAI_API_KEY": "sk-old"}


def test_set_json_validates_like_the_prompts(cfg, monkeypatch, capsys):
    import json as _json
    # URL shape enforced
    _stdin(monkeypatch, _json.dumps({"OPENAI_BASE_URL": "llama.forest.local"}))
    assert credentials.credentials_cli(["set", "openai.work", "--json"]) == 2
    # reserved/invalid profile names refused
    _stdin(monkeypatch, _json.dumps({"OPENAI_API_KEY": "k"}))
    assert credentials.credentials_cli(["set", "openai.new", "--json"]) == 2
    # garbage stdin refused, store untouched
    _stdin(monkeypatch, "not json")
    assert credentials.credentials_cli(["set", "openai.work", "--json"]) == 2
    assert credentials.load() == {}


def test_set_json_undotted_targets_default(cfg, monkeypatch, capsys):
    import json as _json
    _stdin(monkeypatch, _json.dumps({"OPENAI_API_KEY": "sk-k"}))
    assert credentials.credentials_cli(["set", "openai", "--json"]) == 0
    assert credentials.load()["openai"]["default"] == {"OPENAI_API_KEY": "sk-k"}


def test_remember_command_writes_prefs_and_guards_typos(cfg, capsys):
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "w"}}})
    # profile must exist — a remembered typo would silently re-ask forever
    assert credentials.credentials_cli(["remember", "exp", "openai", "wrok"]) == 1
    assert credentials.credentials_cli(["remember", "exp", "openai", "work"]) == 0
    assert credentials._load_prefs() == {"exp": {"openai": "work"}}
    # the CLI ladder honors it: no prompts at all
    env = credentials.resolve_run_credentials(
        _manifest({"kind": "llm"}), {"model": "openai/q"},
        experiment="exp", interactive=False)
    assert env == {"OPENAI_API_KEY": "w"}


# -- explicit selection (--credential SET=NAME) ----------------------------------------

def test_selection_beats_remembered_and_skips_prompts(cfg, monkeypatch, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"},
                                 "work": {"OPENAI_API_KEY": "w"}}})
    credentials.credentials_cli(["remember", "exp", "openai", "default"])
    _stdin(monkeypatch, "")  # any prompt would fail on empty stdin
    env = credentials.resolve_run_credentials(
        _manifest({"kind": "llm"}), {"model": "openai/q"},
        experiment="exp", interactive=True, selections={"openai": "work"})
    assert env == {"OPENAI_API_KEY": "w"}
    # no remember question was asked; the stored pref is untouched
    assert credentials._load_prefs() == {"exp": {"openai": "default"}}
    assert "using openai.work (--credential)" in capsys.readouterr().err


def test_selection_typo_guards_both_halves(cfg, capsys):
    credentials.save({"openai": {"default": {"OPENAI_API_KEY": "d"}}})
    manifest = _manifest({"kind": "llm"})
    # a set the run doesn't route to
    with pytest.raises(ValueError, match="--credential.*route to"):
        credentials.resolve_run_credentials(
            manifest, {"model": "openai/q"}, experiment="e", interactive=False,
            selections={"anthropic": "default"})
    # a profile that doesn't exist
    with pytest.raises(ValueError, match="--credential openai=wrok: no such profile"):
        credentials.resolve_run_credentials(
            manifest, {"model": "openai/q"}, experiment="e", interactive=False,
            selections={"openai": "wrok"})


def test_selection_satisfies_headless_missing_gate(cfg, capsys):
    # headless + unconfigured built-in normally refuses; an explicit selection of a
    # profile that DOES exist must not be blocked by that gate
    credentials.save({"openai": {"work": {"OPENAI_API_KEY": "w"}}})
    env = credentials.resolve_run_credentials(
        _manifest({"kind": "llm"}), {"model": "openai/q"},
        experiment="e", interactive=False, selections={"openai": "work"})
    assert env == {"OPENAI_API_KEY": "w"}

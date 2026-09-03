import pytest

from cascade_cms.mcp.config import cache_configuration, load_environment_variables


def test_load_environment_variables_defaults_server(monkeypatch):
    monkeypatch.setenv("CASCADE_API_KEY", "key123")
    monkeypatch.setenv("CASCADE_URL", "https://cascade.example.com")
    monkeypatch.delenv("SERVER", raising=False)

    env_vars = load_environment_variables()

    assert env_vars == {
        "API_KEY": "key123",
        "CASCADE_URL": "https://cascade.example.com",
        "SERVER": "default",
    }


def test_load_environment_variables_respects_explicit_server(monkeypatch):
    monkeypatch.setenv("CASCADE_API_KEY", "key123")
    monkeypatch.setenv("CASCADE_URL", "https://cascade.example.com")
    monkeypatch.setenv("SERVER", "PROD")

    env_vars = load_environment_variables()

    assert env_vars["SERVER"] == "PROD"


@pytest.mark.parametrize(
    "missing",
    ["CASCADE_API_KEY", "CASCADE_URL"],
)
def test_load_environment_variables_missing_var_raises_system_exit(
    monkeypatch, missing
):
    monkeypatch.setenv("CASCADE_API_KEY", "key123")
    monkeypatch.setenv("CASCADE_URL", "https://cascade.example.com")
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        load_environment_variables()

    assert missing in str(exc_info.value)


def test_load_environment_variables_missing_both_names_both(monkeypatch):
    monkeypatch.delenv("CASCADE_API_KEY", raising=False)
    monkeypatch.delenv("CASCADE_URL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        load_environment_variables()

    message = str(exc_info.value)
    assert "CASCADE_API_KEY" in message
    assert "CASCADE_URL" in message


def test_cache_configuration_respects_override_dir(monkeypatch, tmp_path):
    override_dir = tmp_path / "cascade-mcp-cache"
    monkeypatch.setenv("CASCADE_MCP_CACHE_DIR", str(override_dir))

    config = cache_configuration()

    assert config["cache_name"] == str(override_dir / "cache.sqlite")
    assert override_dir.is_dir()
    assert config["allowed_codes"] == (200,)
    assert config["allowed_methods"] == ("GET",)

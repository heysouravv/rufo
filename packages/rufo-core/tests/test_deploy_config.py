import pytest
from rufo_core.errors import PolicyLoadError
from rufo_core.deploy_config import parse_deploy_config

DOC = {
    "port": 8080,
    "scaling": {"min_instances": 1, "max_instances": 10},
    "endpoint": {"enabled": True, "model_name": "expense-bot", "stream": False},
    "mcp": [
        {"name": "filesystem", "url": "mcp://fs-server:9000"},
        {"name": "crm", "url": "https://mcp.internal.acme.com", "mode": "tunnel", "connector": "acme-1"},
    ],
    "env": ["OPENAI_API_KEY"],
}


def test_parse_deploy_config_basic():
    cfg = parse_deploy_config(DOC)
    assert cfg.port == 8080
    assert cfg.scaling.min_instances == 1
    assert cfg.scaling.max_instances == 10
    assert cfg.endpoint.model_name == "expense-bot"
    assert cfg.endpoint.stream is False
    assert len(cfg.mcp) == 2
    assert cfg.mcp[1].mode == "tunnel"
    assert cfg.mcp[1].connector == "acme-1"
    assert cfg.env == ["OPENAI_API_KEY"]


def test_parse_deploy_config_defaults():
    cfg = parse_deploy_config({})
    assert cfg.port == 8080
    assert cfg.scaling.max_instances == 5
    assert cfg.endpoint.enabled is True
    assert cfg.mcp == []


def test_parse_deploy_config_mcp_missing_fields():
    with pytest.raises(PolicyLoadError):
        parse_deploy_config({"mcp": [{"name": "no-url"}]})

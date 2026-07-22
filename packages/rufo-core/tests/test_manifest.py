import pytest
from rufo_core.errors import PolicyLoadError
from rufo_core.manifest import parse_manifest

TOML_DOC = {
    "agent": {"name": "expense-bot", "entrypoint": "agent.py:agent", "runtime": "python@3.11"},
    "dependencies": {"langgraph": "^0.2", "langchain-openai": "^0.2"},
    "policy": {"file": "policy.yaml"},
}


def test_parse_manifest_basic():
    manifest = parse_manifest(TOML_DOC)
    assert manifest.name == "expense-bot"
    assert manifest.entrypoint_module == "agent.py"
    assert manifest.entrypoint_attr == "agent"
    assert manifest.dependencies["langgraph"] == "^0.2"
    assert manifest.policy_file == "policy.yaml"


def test_parse_manifest_defaults():
    manifest = parse_manifest({"agent": {"entrypoint": "agent.py:agent"}})
    assert manifest.name == "agent"
    assert manifest.runtime == "python@3.11"
    assert manifest.policy_file == "policy.yaml"


def test_parse_manifest_missing_agent_section():
    with pytest.raises(PolicyLoadError):
        parse_manifest({})


def test_parse_manifest_bad_entrypoint():
    with pytest.raises(PolicyLoadError):
        parse_manifest({"agent": {"entrypoint": "no-colon-here"}})

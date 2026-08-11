from math_agent.config import load_config
from math_agent.web.app import default_model_string


def test_default_model_string_matches_config():
    cfg = load_config()
    assert default_model_string(cfg) == f"{cfg.llm.provider}/{cfg.llm.model}"


def test_default_model_string_is_gpt_5_6_sol():
    from math_agent.config import Config, LLMConfig

    cfg = Config(llm=LLMConfig(provider="openai", model="gpt-5.6-sol"))
    assert default_model_string(cfg) == "openai/gpt-5.6-sol"

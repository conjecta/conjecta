from math_agent.config import load_config
from math_agent.web.app import default_model_string


def test_default_model_string_matches_config():
    cfg = load_config()
    assert default_model_string(cfg) == f"{cfg.llm.provider}/{cfg.llm.model}"


def test_default_model_string_is_shengsuanyun_deepseek_v4_pro():
    from math_agent.config import Config, LLMConfig

    cfg = Config(llm=LLMConfig(provider="shengsuanyun", model="deepseek/deepseek-v4-pro"))
    assert default_model_string(cfg) == "shengsuanyun/deepseek/deepseek-v4-pro"

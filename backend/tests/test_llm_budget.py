from app.services.llm_budget import (
    clamp_max_tokens,
    dashscope_extra_body,
    openai_compatible_extra_body,
    should_disable_thinking,
)


def test_dashscope_qwen_disables_thinking():
    assert should_disable_thinking(
        "qwen3.7-plus",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        True,
    )
    assert dashscope_extra_body(
        "qwen3.7-plus",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        True,
    ) == {"enable_thinking": False}


def test_disable_thinking_can_be_turned_off():
    assert not should_disable_thinking(
        "qwen3.7-plus",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        False,
    )
    assert dashscope_extra_body(
        "qwen3.7-plus",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        False,
    ) is None


def test_bigmodel_glm_uses_official_thinking_control():
    assert openai_compatible_extra_body(
        "glm-5.2",
        "https://open.bigmodel.cn/api/paas/v4",
        True,
    ) == {"thinking": {"type": "disabled"}}


def test_clamp_max_tokens_records_effective_budget():
    budget = clamp_max_tokens(
        requested_max_tokens=9000,
        prompt_chars=12000,
        global_cap=6000,
    )

    assert budget.requested_max_tokens == 9000
    assert budget.max_tokens == 6000
    assert budget.prompt_chars == 12000
    assert budget.was_capped is True

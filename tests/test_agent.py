"""Tests for answer()'s own logic, with the LangChain agent replaced by a script.

Real Claude can't be made to pause or refuse on demand, so a FakeAgent plays
back scripted replies instead. These test *our* handling code, not LangChain.
"""

import pytest
from langchain_core.exceptions import ModelRefusalError
from langchain_core.messages import AIMessage

import jarvis.agent as agent_module


def claude_says(text, stop_reason="end_turn"):
    return AIMessage(content=text, response_metadata={"stop_reason": stop_reason})


class FakeAgent:
    """Stands in for the LangChain agent: plays back one scripted reply per call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []  # what answer() sent on each call

    def invoke(self, state, config=None):
        self.calls.append(state["messages"])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"messages": list(state["messages"]) + [reply]}


@pytest.fixture
def use_fake(monkeypatch):
    def install(*replies):
        fake = FakeAgent(*replies)
        monkeypatch.setattr(agent_module, "_build_agent", lambda: fake)
        return fake
    return install


def test_normal_answer(use_fake):
    use_fake(claude_says("Madrid is the capital."))
    assert agent_module.answer("Capital of Spain?") == "Madrid is the capital."


def test_paused_search_is_resumed_with_conversation_unchanged(use_fake):
    fake = use_fake(
        claude_says("", stop_reason="pause_turn"),
        claude_says("It's 21 degrees."),
    )
    assert agent_module.answer("Weather?") == "It's 21 degrees."
    assert len(fake.calls) == 2
    # The resume must send back exactly what came out of the first call: the
    # original messages plus Claude's paused reply, with nothing added after it
    # (no "please continue" message).
    resumed_with = fake.calls[1]
    assert resumed_with[:-1] == fake.calls[0]  # everything before is unchanged
    assert resumed_with[-1].response_metadata["stop_reason"] == "pause_turn"  # ends on the paused reply


def test_endless_pausing_gives_up(use_fake):
    pauses = [claude_says("", stop_reason="pause_turn")] * (agent_module.MAX_RESUMES + 1)
    fake = use_fake(*pauses)
    with pytest.raises(RuntimeError, match="paused"):
        agent_module.answer("Weather?")
    assert len(fake.calls) == agent_module.MAX_RESUMES + 1  # first try + 5 resumes


def test_refusal_gets_polite_reply(use_fake):
    use_fake(ModelRefusalError())
    assert agent_module.answer("Something Claude declines") == agent_module.REFUSAL_REPLY


def test_empty_answer_is_an_error(use_fake):
    use_fake(claude_says("   "))
    with pytest.raises(RuntimeError, match="without writing"):
        agent_module.answer("Hello?")

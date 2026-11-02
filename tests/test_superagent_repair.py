"""Repairing models that print tool calls as text instead of invoking them."""
import pytest

from openagent.apps.superagent.worker import (PSEUDO_TOOL_RETRY, TicketAgent,
                                              repair_textual_tool_calls)
from openagent.core.types import Message, TextBlock, ToolUseBlock


class Agent:
    def __init__(self, messages):
        self.session = type('S', (), {'messages': messages,
                                      'add': lambda role, text: messages.append(
                                          Message(role=role, content=text))})


def assistant(text):
    return Message(role='assistant', content=[TextBlock(text=text)])


@pytest.mark.parametrize('text', [
    '<tool_use>\n<path>a.txt</path>\n<content>x</content>\n</tool_use>',
    '<function_call name="write">{"path": "a"}</function_call>',
    '<|tool▁call▁begin|>{"name": "write"}<|tool▁call▁end|>',
    '<\\DSML\\ invoke name="write">\n<\\DSML\\ parameter name="path">x',
    '<\uff5cDSML\uff5c invoke name="write"\uff1e',
])
def test_repairs_xml_tool_text(text):
    agent = Agent([assistant(text)])
    assert repair_textual_tool_calls(agent)
    assert agent.session.messages[-1].text == PSEUDO_TOOL_RETRY


def test_does_not_repair_ordinary_prose():
    agent = Agent([assistant('I wrote the file and verified it.')])
    assert not repair_textual_tool_calls(agent)


def test_does_not_repair_real_tool_calls():
    message = Message(role='assistant',
                      content=[ToolUseBlock(name='write', arguments={'path': 'a'})])
    assert not repair_textual_tool_calls(Agent([message]))


def test_does_not_stack_identical_retry_prompts():
    agent = Agent([assistant('<tool_use>...</tool_use>')])
    assert repair_textual_tool_calls(agent)
    assert not repair_textual_tool_calls(agent)


def test_ignores_non_assistant_last_message():
    agent = Agent([Message(role='user', content='<tool_use>x</tool_use>')])
    assert not repair_textual_tool_calls(agent)


def test_ticket_agent_hooks():
    agent = TicketAgent.__new__(TicketAgent)
    assert agent._finish_after_tools() is False

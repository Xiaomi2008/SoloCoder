"""Super-agent application package.

A cut-down port of DeepSeek's super-agent design: the unit of work is a
Ticket, not a chat session. A persistent Router decides what a batch of
Triggers means and hands work to isolated Workers, one directory and one
session per Ticket.
"""

from .domain import (
    Assignment,
    AssignmentState,
    Board,
    Decision,
    DecisionKind,
    HumanReply,
    Input,
    Mandate,
    Notice,
    Policy,
    Report,
    ReportKind,
    Stage,
    Strength,
    Thread,
    Ticket,
    Trigger,
    compute_stage,
)
from .router import SuperAgentRouter
from .runtime import SuperAgentRuntime
from .worker import SuperAgentWorker

__all__ = [
    "Assignment",
    "AssignmentState",
    "Board",
    "Decision",
    "DecisionKind",
    "HumanReply",
    "Input",
    "Mandate",
    "Notice",
    "Policy",
    "Report",
    "ReportKind",
    "Stage",
    "Strength",
    "SuperAgentRouter",
    "SuperAgentRuntime",
    "SuperAgentWorker",
    "Thread",
    "Ticket",
    "Trigger",
    "compute_stage",
]

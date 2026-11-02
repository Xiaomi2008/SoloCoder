"""Independent completion checks, exact approvals, isolation and real Git worktrees."""
import asyncio
import shutil
from types import SimpleNamespace

import pytest

from openagent.apps.superagent.domain import Board
from openagent.apps.superagent.execution import Execution
from openagent.apps.superagent.runtime import SuperAgentRuntime
from openagent.apps.superagent.worktrees import Worktrees, git
from tests.test_superagent_reliability import Router, accepted


async def complete(tmp_path, command=(), execution=None):
    board, store, ticket = accepted(tmp_path)
    async def run(inputs):
        from pathlib import Path
        Path(ticket.directory).mkdir(parents=True, exist_ok=True)
        board.report(ticket.id, 'ready', 'Worker claims everything passed')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           store=store, verify_commands=command, execution=execution)
    await rt.run_pending()
    return board, store, ticket, rt


async def test_ready_needs_operator_review_without_checks(tmp_path):
    board, store, ticket, rt = await complete(tmp_path)
    assert not ticket.ended
    assert ticket.verification == 'review'
    assert not any(n.kind.value == 'ready' for n in board.notices)
    assert rt.review_decision(ticket.id, True).startswith('Verification passed')
    assert ticket.ended
    assert store.load().tickets[ticket.id].verification == 'passed'


@pytest.mark.parametrize('command,passed', [('printf PASS', True), ('echo FAIL; exit 1', False)])
async def test_host_command_controls_completion(tmp_path, command, passed):
    board, store, ticket, _ = await complete(tmp_path, [command])
    assert ticket.ended == passed
    assert ticket.verification == ('passed' if passed else 'failed')
    assert command in ticket.verification_evidence[0]
    assert 'Exit code:' in store.load().tickets[ticket.id].verification_evidence[0]
    if not passed:
        assert ticket.reports[-1].kind.value == 'blocked'


async def test_followup_invalidates_manual_approval(tmp_path):
    board, _, ticket, rt = await complete(tmp_path)
    board.submit('Change the game', actor='owner', thread_id=ticket.origin_thread_ids[0])
    assert rt.review_decision(ticket.id, True).startswith('Error:')
    assert not ticket.ended


async def test_default_shell_denied(tmp_path):
    output = await Execution().run('ticket', str(tmp_path), 'touch forbidden')
    assert output.startswith('Error:')
    assert not (tmp_path / 'forbidden').exists()


async def test_approval_is_exact_and_once(tmp_path):
    execution = Execution(mode='ask')
    task = asyncio.create_task(execution.run('ticket', str(tmp_path), 'printf APPROVED'))
    await asyncio.sleep(0)
    request_id, request = next(iter(execution.pending.items()))
    assert request.command == 'printf APPROVED'
    assert request.ticket_id == 'ticket'
    execution.decide(request_id, True)
    assert 'APPROVED' in await task
    assert not execution.pending
    assert execution.decide(request_id, True).startswith('Error:')
    denied = asyncio.create_task(execution.run('ticket', str(tmp_path), 'touch denied'))
    await asyncio.sleep(0)
    execution.decide(next(iter(execution.pending)), False)
    assert 'denied' in await denied
    assert not (tmp_path / 'denied').exists()


async def test_approval_expiry_cancel_and_revocation(tmp_path):
    execution = Execution(mode='ask', approval_timeout=.01)
    assert 'expired' in await execution.run('t', str(tmp_path), 'touch expired')
    assert not execution.pending
    execution.approval_timeout = 60
    task = asyncio.create_task(execution.run('t', str(tmp_path), 'touch revoked', check=lambda: False))
    await asyncio.sleep(0)
    execution.decide(next(iter(execution.pending)), True)
    assert 'revoked' in await task
    assert not (tmp_path / 'revoked').exists()
    task = asyncio.create_task(execution.run('t', str(tmp_path), 'touch cancelled'))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not execution.pending


async def test_docker_missing_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda _: None)
    output = await Execution(mode='trusted', sandbox='docker').run('t', str(tmp_path), 'touch unsafe')
    assert 'refused' in output
    assert not (tmp_path / 'unsafe').exists()


async def test_docker_arguments_and_cleanup(tmp_path, monkeypatch):
    import openagent.apps.superagent.execution as module
    calls = []
    monkeypatch.setattr(shutil, 'which', lambda _: '/usr/bin/docker')
    async def shell(command, directory, timeout):
        calls.append(command)
        return 'Exit code: 0\nOK'
    monkeypatch.setattr(module, 'run_shell', shell)
    await Execution(mode='trusted', sandbox='docker').run('t', str(tmp_path), 'echo "x; touch /evil"')
    import shlex
    args = shlex.split(calls[0])
    assert '--network=none' in args and '--read-only' in args
    assert '--cap-drop=ALL' in args and '--pull=never' in args
    assert args[-1] == 'echo "x; touch /evil"'
    assert shlex.split(calls[-1])[:3] == ['docker', 'rm', '-f']


async def test_real_worktree_isolates_changes_and_resumes(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    await git(str(repo), 'init')
    (repo / 'app.txt').write_text('Original')
    await git(str(repo), 'add', 'app.txt')
    await git(str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Initial')
    worktrees = Worktrees(str(repo))
    await worktrees.validate()
    board = Board(workspace=str(tmp_path / 'workspace'))
    trigger = board.submit('Change app', actor='owner')
    board.route(kind='accept', trigger_ids=trigger.id, title='App')
    ticket = next(iter(board.tickets.values()))
    await worktrees.prepare(ticket)
    from pathlib import Path
    target = Path(ticket.directory) / 'app.txt'
    assert target.read_text() == 'Original'
    target.write_text('Ticket edit')
    assert (repo / 'app.txt').read_text() == 'Original'
    await worktrees.prepare(ticket)  # does not overwrite a resumed worktree
    review = await worktrees.review(ticket)
    assert 'Ticket edit' in review and ticket.branch in review
    assert 'superagent/' in ticket.branch
    await git(str(repo), 'worktree', 'remove', '--force', ticket.directory)


async def test_existing_directory_never_replaced(tmp_path):
    destination = tmp_path / 'existing'
    destination.mkdir()
    (destination / 'keep').write_text('keep')
    ticket = SimpleNamespace(repository=None, directory=str(destination))
    with pytest.raises(RuntimeError, match='existing'):
        await Worktrees(str(tmp_path)).prepare(ticket)
    assert (destination / 'keep').read_text() == 'keep'


async def test_failed_report_invalidates_ready_request(tmp_path):
    board, _, ticket, rt = await complete(tmp_path)
    board.report(ticket.id, 'failed', 'Found a serious bug after reporting ready')
    assert ticket.verification == 'failed'
    assert rt.review_decision(ticket.id, True).startswith('Error:')
    assert not ticket.ended


async def test_runtime_creates_worktree_before_worker(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    await git(str(repo), 'init')
    (repo / 'app.txt').write_text('Initial')
    await git(str(repo), 'add', '.')
    await git(str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Initial')
    workspace = tmp_path / 'host'
    workspace.mkdir()
    board, store, ticket = accepted(workspace)
    worktrees = Worktrees(str(repo))
    async def run(inputs):
        from pathlib import Path
        assert Path(ticket.directory, 'app.txt').read_text() == 'Initial'
        board.report(ticket.id, 'ready', 'Changed')
    rt = SuperAgentRuntime(board, Router(), worker_factory=lambda _: SimpleNamespace(run=run),
                           worktrees=worktrees, verify_commands=['test -f app.txt'], store=store)
    await rt.run_pending()
    assert ticket.ended and ticket.verification == 'passed'
    assert store.load().tickets[ticket.id].branch == ticket.branch
    await git(str(repo), 'worktree', 'remove', '--force', ticket.directory)


async def test_live_worker_shell_is_actually_gated(tmp_path):
    from openagent.apps.superagent.worker import SuperAgentWorker
    from openagent.core.types import ToolUseBlock
    from tests.test_superagent import ScriptedProvider
    board, _, ticket = accepted(tmp_path)
    worker = SuperAgentWorker(ScriptedProvider([]), board, ticket.id)
    result = await worker._agent.tool_registry.execute(
        ToolUseBlock('bash', {'command': 'touch SHOULD_NOT_EXIST'}), context=worker._agent.tool_context)
    assert 'disabled' in result.content
    from pathlib import Path
    assert not Path(ticket.directory, 'SHOULD_NOT_EXIST').exists()


async def test_worker_report_cannot_bypass_gate(tmp_path):
    from openagent.apps.superagent.worker import SuperAgentWorker
    from openagent.core.types import ToolUseBlock
    from tests.test_superagent import ScriptedProvider
    board, _, ticket = accepted(tmp_path)
    board.take_inputs(ticket.id)
    worker = SuperAgentWorker(ScriptedProvider([]), board, ticket.id)
    result = await worker._agent.tool_registry.execute(
        ToolUseBlock('report', {'kind': 'ready', 'summary': 'Trust me, done'}))
    assert 'host verification required' in result.content
    assert not ticket.ended
    assert ticket.verification == 'pending'
    assert not board.notices

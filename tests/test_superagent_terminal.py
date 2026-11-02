"""Terminal views are readable without ANSI and do not change domain state."""
import io
from types import SimpleNamespace

from openagent.apps.superagent.domain import Board, HumanReply
from openagent.apps.superagent.terminal import Terminal, clean


def fixture_board(tmp_path):
    board = Board(workspace=str(tmp_path))
    trigger = board.submit('Build a game', actor='owner')
    board.route(kind='accept', trigger_ids=trigger.id, title='Game', acknowledgement='Starting')
    ticket = next(iter(board.tickets.values()))
    return board, trigger, ticket


def test_plain_views_include_relations_and_details(tmp_path):
    board, trigger, ticket = fixture_board(tmp_path)
    output = io.StringIO()
    ui = Terminal(output, width=90)
    ui.tickets(board)
    ui.threads(board, trigger.thread_id)
    ui.ticket(board, ticket.id)
    rendered = output.getvalue()
    assert '\x1b' not in rendered
    assert ticket.id in rendered and trigger.thread_id in rendered
    assert ticket.directory in "".join(rendered.split())
    assert 'Queued' in rendered and 'Game' in rendered
    assert f'> {trigger.thread_id}' in rendered
    assert len(board.tickets) == 1 and len(board.replies) == 1


def test_banner_status_and_help(tmp_path):
    board, _, _ = fixture_board(tmp_path)
    output = io.StringIO()
    ui = Terminal(output)
    runtime = SimpleNamespace(board=board, router=SimpleNamespace(provider=SimpleNamespace(model='test-model')),
                              assignment_timeout=600)
    ui.banner(runtime, SimpleNamespace(provider='lmstudio'))
    ui.help()
    rendered = output.getvalue()
    for value in ('SUPER AGENT', 'test-model', 'Working 1', 'NOT sandboxed', ':threads', ':ticket <id>', ':stop <id>'):
        assert value in rendered


def test_empty_views_and_unknown_ticket(tmp_path):
    board = Board(workspace=str(tmp_path))
    output = io.StringIO()
    ui = Terminal(output)
    ui.tickets(board)
    ui.threads(board, None)
    ui.ticket(board, 'missing')
    assert 'No tickets yet' in output.getvalue()
    assert 'No conversations yet' in output.getvalue()
    assert 'Error: unknown ticket' in output.getvalue()


def test_narrow_reply_wraps_and_sanitizes_controls():
    output = io.StringIO()
    ui = Terminal(output, width=32, color=False)
    ui.reply(HumanReply('ticket-4', 'attention', '\x1b[2J' + 'Long content ' * 10, 'Which color?'))
    rendered = output.getvalue()
    assert '\x1b' not in rendered
    assert 'NEEDS YOU' in rendered and '? Which color?' in rendered
    assert all(len(line) <= 32 for line in rendered.splitlines())
    assert clean('\x1b]0;forged title\x07safe\r\x00') == 'safe'


def test_prompt_displays_selected_thread():
    assert Terminal.prompt(None) == 'you [new thread] > '
    assert Terminal.prompt('thread-1') == 'you [thread-1] > '


def test_no_color_environment(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setenv('NO_COLOR', '1')
    ui = Terminal(TTY())
    assert ui.style('Result', 'good') == 'Result'
    monkeypatch.delenv('NO_COLOR')
    monkeypatch.setenv('TERM', 'xterm-256color')
    assert '\x1b' in Terminal(TTY()).style('Result', 'good')


async def test_cli_view_commands_without_model_calls(tmp_path, monkeypatch):
    from openagent.apps.superagent import __main__ as cli

    board, trigger, ticket = fixture_board(tmp_path)
    output = io.StringIO()
    monkeypatch.setattr(cli, 'Terminal', lambda: Terminal(output))
    commands = iter([':help', ':status', ':threads', ':tickets', f':ticket {ticket.id}',
                     f':thread {trigger.thread_id}', ':quit'])
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return next(commands)

    monkeypatch.setattr('builtins.input', read)

    async def run_pending():
        return []

    runtime = SimpleNamespace(board=board, router=SimpleNamespace(provider=SimpleNamespace(model='test-model')),
                              assignment_timeout=600, run_pending=run_pending)
    await cli._interactive(runtime, SimpleNamespace(provider='lmstudio'))
    assert 'THREADS / CONVERSATIONS' in output.getvalue()
    assert 'TICKETS / JOBS' in output.getvalue()
    assert prompts[-1] == f'you [{trigger.thread_id}] > '

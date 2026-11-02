"""Operator-selected repository, isolated ticket branches, no automatic merges."""
from __future__ import annotations

from pathlib import Path

from .shell import run_shell
import shlex


async def git(repo: str, *arguments: str) -> str:
    result = await run_shell(shlex.join(['git', '-c', 'core.hooksPath=/dev/null', '-C', repo,
                                        *arguments]), repo, 30)
    if not result.startswith('Exit code: 0\n'):
        raise RuntimeError(result)
    return result.split('\n', 1)[1].strip()


class Worktrees:
    def __init__(self, repo: str):
        self.repo = str(Path(repo).expanduser().resolve())

    async def validate(self):
        root = await git(self.repo, 'rev-parse', '--show-toplevel')
        if Path(root).resolve() != Path(self.repo):
            raise ValueError('--repo must name the repository root')
        await git(self.repo, 'rev-parse', '--verify', 'HEAD')

    async def prepare(self, ticket):
        if ticket.repository:
            if Path(ticket.repository).resolve() != Path(self.repo):
                raise RuntimeError('Ticket belongs to a different repository; refusing reassignment')
            if Path(ticket.directory, '.git').exists():
                actual = await git(ticket.directory, 'rev-parse', '--show-toplevel')
                branch = await git(ticket.directory, 'symbolic-ref', '--short', 'HEAD')
                if Path(actual).resolve() == Path(ticket.directory).resolve() and branch == ticket.branch:
                    return
            if not Path(ticket.directory).exists():
                # Reconcile durable creation intent after interruption. A branch
                # without its worktree needs manual repair, not an implicit reset.
                await git(self.repo, 'worktree', 'add', '-b', ticket.branch,
                          ticket.directory, ticket.base_commit)
                return
            raise RuntimeError('Stored worktree missing or mismatched; manual repair required')
        destination = Path(ticket.directory)
        if destination.exists():
            raise RuntimeError('Refusing to replace an existing ticket directory with a worktree')
        branch = 'superagent/' + ticket.id
        base = await git(self.repo, 'rev-parse', 'HEAD')
        destination.parent.mkdir(parents=True, exist_ok=True)
        # The ticket must be checkpointed before this external side effect. The
        # runtime records branch/base intent first, allowing safe reconciliation.
        ticket.repository, ticket.branch, ticket.base_commit = self.repo, branch, base
        await git(self.repo, 'worktree', 'add', '-b', branch, str(destination), base)

    async def review(self, ticket):
        if not ticket.repository:
            return 'No Git worktree is associated with this ticket.'
        status = await git(ticket.directory, 'status', '--short')
        diff = await git(ticket.directory, 'diff', '--no-ext-diff', '--no-textconv', ticket.base_commit, '--')
        return f'Branch: {ticket.branch}\nBase: {ticket.base_commit}\nStatus:\n{status}\nDiff:\n{diff}'

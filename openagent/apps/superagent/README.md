# Super-agent coding host

A Python implementation inspired by DeepSeek's super-agent design: Tickets are
units of authorized work; Threads are conversations; Triggers are events. A
Router selects work, separate Workers execute it, and the host controls lifecycle,
permissions and verification. This is an evolving implementation, not a full port.

## Run

```bash
# Custom configured provider; your existing workspace is preserved
.venv/bin/python -m openagent.apps.superagent \
  --provider taopc --model deepseek-v4.1-x --accept-mode auto \
  --workspace .superagent-work --execution ask

# ChatGPT subscription (unofficial Codex Responses adapter)
# Sign in first if needed: openagent auth login chatgpt
.venv/bin/python -m openagent.apps.superagent \
  --provider openai_oauth --model gpt-6.1-terra --execution ask

# Local OpenAI-compatible endpoint
.venv/bin/python -m openagent.apps.superagent \
  --model qwen3.5-35b-a3b --base-url http://localhost:1234/v1
```

Credentials are resolved/refreshed through SoloCoder's broker. Provider names
and model IDs are separate arguments; custom providers use their registered
endpoint. `--base-url` is supported only for lmstudio, never subscription auth.
Accept modes off/observe/auto govern inferred accepts; allowed direct requests
can create work in every mode. CLI shell execution now defaults to **deny**.

## Execution controls and isolation

- `--execution deny` (default): file tools can operate inside the ticket directory;
  shell requests are refused. This is not a read-only mode.
- `--execution ask`: each exact shell command and directory is shown. Approve
  once with `:allow exec-ID`, or refuse with `:deny exec-ID`. Requests expire
  after 120 seconds. Approval grants are not retained across calls/restarts.
- `--execution trusted`: explicit opt-in to unattended arbitrary shell execution.
- `--sandbox host` (default): approved commands have the current user's privileges.
  **This is NOT sandboxed.** Directory scope is not a security boundary for shell.
- `--sandbox docker --sandbox-image IMAGE`: commands run in an installed local
  image with `--pull=never`, no network, read-only root, dropped capabilities,
  no-new-privileges, process/memory/CPU limits, a bounded temporary directory,
  and only the ticket directory mounted read-write. No credentials, home directory,
  Docker socket, or environment secrets are forwarded. Missing Docker/image/daemon
  fails closed; no host fallback. Prepare your image yourself before starting.

Example unattended isolated execution (requires a running Docker daemon):

```bash
.venv/bin/python -m openagent.apps.superagent \
  --provider taopc --model deepseek-v4.1-x --workspace .superagent-work \
  --execution trusted --sandbox docker --sandbox-image node:22-slim \
  --verify-command 'node --check game.js'
```

Choose a suitable image containing the tools you need. Network/package installation
is unavailable inside the container. The Docker backend currently isolates shell
processes, not the Python host's scoped builtin file tools or model connection.
It mitigates risk, not kernel vulnerabilities. Use trusted local Docker settings.
Cancellation kills the container as well as the Docker client. A hard host crash
cannot guarantee cleanup: inspect orphan containers/processes before resuming.

## Independent completion gate

Workers calling `report(ready)` request verification; they do not complete work.
The host waits for the assignment to exit, then runs **operator-configured** checks:

```bash
# Repeat --verify-command for a sequence; all must exit zero
... --verify-command 'python -m pytest -q' --verify-command 'git diff --check'
```

Checks execute in the ticket directory using the selected host/Docker backend,
with 120-second deadlines and capped captured output. They are preauthorized by
the operator at launch, not prompted through shell approval. A failure blocks the
Ticket and stores command/output evidence. There is no automatic repair loop yet:
inspect `:ticket ID`, fix through a follow-up, or explicitly `:retry ID`.

With no checks configured, the Ticket waits for human review:

```
:ticket ticket-4
:approve ticket-4
# or :reject ticket-4
```

Approvals are allowed only for Tickets waiting on manual verification. New inputs,
revoked mandates or later failed reports invalidate pending approval. Passing
checks completes non-PR Tickets; PR Tickets remain awaiting review. Existing
completed historical Tickets are not retroactively verified. Passing a weak check
is not proof of correctness: configure meaningful acceptance tests. Worker-written
tests/scripts can be flawed or malicious; checks running repository code are not
an adversarially independent verifier. This gate is host-controlled execution and
evidence, not a second LLM audit or automatic browser playthrough. Checks may rerun
after restart: use idempotent read/check commands, never deployment/payment actions.

## Git worktrees

```bash
... --repo /absolute/path/to/repository --execution ask \
    --verify-command 'git diff --check'
```

The repository must have a committed HEAD. Each new Ticket gets a branch
`superagent/ticket-ID` and separate worktree from the source HEAD; uncommitted
source changes are not copied. A directory already containing files is never
overwritten. Existing non-repo Tickets therefore cannot be silently converted.
Use a dedicated workspace per repository; resuming repo-bound Tickets requires
the same `--repo`. Base commit, branch and repository are persisted. Interrupted
creation is reconciled conservatively; unexpected existing branches/worktrees
require manual repair, never automatic deletion/reset.

`:diff ticket-ID` shows branch, status (including untracked files), and tracked
diff from the base. Untracked content must be inspected separately. Hooks are
disabled for host-managed Git operations. There is **no automatic commit, merge,
push, or cleanup**. Review and merge manually after verification. Docker mounts
only the worktree, not the shared Git metadata outside it; Git commands inside
such containers may fail. This is deliberate rather than exposing the source
repository's shared metadata. Managed multi-repo routing is not yet implemented.

## Terminal commands

- `:help`, `:status`, `:tickets`: controls and current summary.
- `:ticket ID`: directory, state, branch and verification evidence.
- `:threads`: conversations and connected Ticket IDs.
- `:thread ID`: subsequent messages go to that conversation; `:thread` resets
  to new conversations per request. The prompt displays the destination.
- `:stop ID`: permanently stop and cancel active execution.
- `:retry ID`: retry unfinished work after inspection, under its current mandate.
- `:allow EXEC-ID`, `:deny EXEC-ID`: resolve one exact shell request.
- `:approve ID`, `:reject ID`: manual completion verification.
- `:diff ID`: review a worktree without merging.
- `:log`: activity history; `:drain`: wait until queued work finishes.
- `:quit`: cancel active work, save and exit.

The CLI accepts input while one Worker runs in the background. It is not a
full-screen TUI: internal logs can interleave with input. Colors respect NO_COLOR;
piped output stays plain. In piped scripts put `:drain` before `:quit`; ask-mode
execution needs an operator, so choose deny or explicitly isolated/trusted mode.

## Follow-up routing

Single-origin, single-Ticket Threads retain direct delivery for allowed direct
messages. Multi-Ticket messages go through the Router, never broadcast. It can
attach to one Ticket, accept new work, or clarify the target. For example,
"ticket-4: change game controls to WASD" selects a job; "make it better" with
several matching jobs should cause a question. Context subscriptions do not grant
automatic execution. Stopped/revoked targets are refused. Router semantics remain
model-dependent; the host enforces target validity, not perfect intent inference.
For multiple tasks, send separate targeted messages.

## Verification cancellation

Verification has its own 120-second per-command deadline. Cancelling the active
verification (`:stop`, shutdown or cancellation) records `failed`, never
`passed`. A Ticket stopped during verification cannot be manually approved.

## Heartbeats, bounded repair, concurrency

Every model call and shell command refreshes an assignment heartbeat. Progress
notes (`:ticket ID`) show the last known activity. A Worker silent for
`--heartbeat-timeout` seconds (default 120) is cancelled and marked failed; a
separate `--assignment-timeout` still bounds total runtime. Heartbeats are
liveness, not proof of progress: a model repeatedly producing empty responses
keeps the clock alive until the total deadline.

`--auto-retries N` (default 0) applies **only** to host-detected executor and
verification failures, with exponential backoff `--retry-backoff` (base 30s).
Worker-declared `failed`/`blocked`/`dropped` reports are never auto-retried,
because the Worker judged the problem non-transient. Retries restore the failed
inputs plus an explicit instruction to inspect files and avoid repeating external
side effects. Budget is persisted; manual `:retry` resets the backoff gate.

`--max-concurrent N` (default 1) runs up to N assignments simultaneously. Each
Ticket has its own Worker/session/directory, but concurrent Tickets may share
the host process, model rate limits, CPU, and any directories you let them reach.
Verification is serialized per Ticket and never starts while another assignment
is mutating that Ticket's directory. Start with 2-3 and measure.

## Continuous service intake

```bash
.venv/bin/python -m openagent.apps.superagent \
  --provider taopc --model deepseek-v4.1-x --service --execution ask \
  --workspace .superagent-work --notify-hook /path/to/notify
```

`--service` runs headless: it polls `workspace/service/inbox/*.json` every
`--poll-interval` seconds, submits each `{"text": "...", "actor": "owner",
"thread_id": null, "source": "file"}` event once, and moves the file to `done/`.
Malformed/invalid events go to `failed/` with a `.error.json` note. Filenames use
UUIDs; processing is at-least-once around a crash (a submitted event whose file
was not yet moved can duplicate after restart; de-duplication is not implemented).
The inbox is a local trust boundary, not an authenticated network API.

Notifications are appended atomically as JSONL to `service/outbox.jsonl`.
`--notify-hook COMMAND` receives each notification as JSON on **stdin**, with a
minimal environment and a 30-second timeout; output/exit failures are themselves
notified. Event text never becomes shell code. No public HTTP listener, GitHub or
webhook connector is provided: a trusted external process must write the inbox.
Ask-mode shell approvals are not available headless; use deny or an isolated
`--execution trusted --sandbox docker` policy.

## HTTP intake (webhooks), GitHub, dashboard

```bash
# generate two secrets once
python3 -c "import secrets;print(secrets.token_hex(32))" > webhook.secret
python3 -c "import secrets;print(secrets.token_hex(32))" > dashboard.token

.venv/bin/python -m openagent.apps.superagent \
  --provider taopc --model deepseek-v4.1-x --workspace .superagent-work \
  --service --http-bind 127.0.0.1 --http-port 8766 \
  --webhook-secret-file webhook.secret --dashboard-token-file dashboard.token
```

**Signed webhook intake** — `POST /events` requires `X-SuperAgent-Timestamp` and
`X-SuperAgent-Signature` (hex HMAC-SHA256 of `timestamp.body`, tolerance 300s).
It also enforces a 64 KiB body limit, a per-client token bucket (30 burst,
1/s), and an on-disk replay log (`service/webhook-seen.json`) so a repeated
`event_id` returns `200 {"status":"duplicate"}` without re-queueing. Valid
events are queued only; the endpoint never runs work synchronously.

```python
import hashlib, hmac, json, time, urllib.request
body = json.dumps({"text": "Build the game", "event_id": "abc-1"}).encode()
ts = str(int(time.time()))
sig = hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
```

**Dashboard** — read-only HTML on `/`, `/tickets/{id}`, `/threads`, served on the
same port. Requires `Authorization: Bearer <token>` for every request; without a
token file the dashboard is not mounted. All model/user text is HTML-escaped.
FastAPI docs/OpenAPI endpoints are disabled.

**GitHub Issues** — `--github-repo owner/name --github-label agent` polls open
issues carrying that label on each cycle. Only the issue title/body becomes task
text; the label is the authorization gate, and the token is read from
`--github-token-file` or `GITHUB_TOKEN`. Seen issue numbers persist in
`service/github-seen.json`, so an issue is queued once. PRs (which share the
Issues endpoint) are skipped. Connector errors are written to the outbox.

```bash
... --github-repo acme/widgets --github-label agent --poll-interval 30
```

Bind to loopback and put a reverse proxy/TLS in front for remote use; the intake
speaks plain HTTP. Bind `0.0.0.0` only behind that proxy. Do not put secrets in
the workspace snapshot—store them in files with restrictive permissions.

## Reliability and remaining work

Atomic versioned state snapshots and session checkpoints; single-writer POSIX
workspace lock; interrupted assignments blocked for explicit retry; unknown tool
outcomes never blindly replayed. Router stops after its routing/relay obligations
are fulfilled rather than requesting closing prose. Shell defaults to 30 seconds
(maximum 120); `--assignment-timeout 600` and `--router-timeout 120` bound model
turns. Missing reports are blocked. These are wall-clock deadlines, not heartbeats.

State contains task text/tool results: protect the workspace. No exactly-once
external side-effect guarantee. Host-mode file tools are synchronous and may delay
event-loop deadlines. Launch flags choose current model, policy and verification
checks; approval requests are not durable and must be resubmitted after restart.

Still not implemented: GitLab/Jira/Slack connectors, GitHub webhook (as opposed
to polling) mode, multi-repo routing, per-connector concurrency control, per-tool/network resource budgets, cross-Ticket shared
resource accounting, dead-letter redelivery, exactly-once event delivery, and an
adversarial second-model verifier. Parallelism is per-assignment slots only; there
is no global GPU/token/rate-limit scheduler. Service crash recovery is at-least-once.

## Tests

```bash
.venv/bin/python -m pytest tests/test_superagent*.py -q
```

Offline tests cover real temporary Git worktrees, host verification pass/fail,
manual review, approvals/expiry/cancellation, sandbox fail-closed and argument
construction, persistence/recovery, deadlines, process cleanup, selective routing,
provider selection and terminal rendering. Docker requires an additional live
container integration test on a machine with a working daemon.

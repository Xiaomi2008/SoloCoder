# macapp — the Mac client

`AgentClient/` is one SwiftPM package with four targets: the wire protocol, the
app's state machine, the SwiftUI shell, and a contract suite that runs without
Xcode. It speaks the protocol in
[`dev_docs/agent-service-protocol.md`](../dev_docs/agent-service-protocol.md)
over a Unix socket. The protocol and the state machine are libraries a test can
import; the views are the only part that is not.

## Layout

```
AgentClient/
  Package.swift
  Sources/AgentClient/            the wire protocol
    Wire.swift                    envelope, framing, error codes, JSONValue
    AgentEvent.swift              every event type, 1:1 with the Python dataclasses
    AgentServiceClient.swift      connect, call, event stream, run, permission
    ContractFixtures.swift        loads the fixtures the Python side generates
  Sources/AgentAppCore/           the app, minus the pixels
    Transcript.swift              event stream -> the timeline a view renders
    AppModel.swift                runs, permissions, models, sessions
    Sidecar.swift                 spawns the service in development
  Sources/AgentApp/               the SwiftUI shell (chat, tools, permission sheet)
  Sources/AgentClientChecks/      `swift run agent-checks` — the contract suite
  Tests/AgentClientTests/         the same checks as a Swift Testing suite
```

The split is deliberate: `AgentAppCore` is the app's logic and a test can import
it, so the state machine and the app model are checked headless. `AgentApp` is
views, and views are the only thing left that no automated check reaches.

## Running the checks

```sh
cd macapp/AgentClient
swift run agent-checks                        # fixtures, framing, live service, app model
AGENT_REPO_ROOT=.. swift run agent-checks     # enable the app-model checks
AGENT_REPO_ROOT=.. AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s swift run agent-checks
swift run solocoder                           # the app itself
```

`solocoder` spawns its own service (`Sidecar`), so it needs a repository to run
it from: `AGENT_REPO_ROOT` pointing at the SoloCoder checkout, or a working
directory that contains `.venv/bin/python`. A shipped app embeds a frozen build
of the service instead — that packaging step is not done.

`agent-checks` is the gate that runs everywhere Swift runs. It spawns the real
service (`.venv/bin/python cli_coder.py serve --socket …`), drives it, and
prints `N checks passed` or a list of failures with a non-zero exit.

`swift test` is the idiomatic entry point, and it needs Xcode: the XCTest and
Swift Testing libraries ship inside `Xcode.app`, and a Command Line Tools
install has the compiler without them. `Tests/AgentClientTests/` mirrors
`agent-checks` assertion for assertion, so the moment the package is opened in
Xcode the same contract runs as a test suite. Until then, `agent-checks` is the
one that runs.

`AGENT_LIVE_MODEL` opts in to a real model run. It also opts out of the sandboxed
`HOME`: the model servers a real run routes to are the developer's — their
config, their Keychain keys — so a sandboxed `HOME` has nothing to route to, and
the run writes a session into `~/.openagent/sessions`.

## What the checks cover

- **Fixtures.** Every event in `tests/service/fixtures/events.json` — generated
  by `scripts/gen_protocol_fixtures.py` from the Python dataclasses — decodes
  into the Swift enum, with the same `type`, the same `run_id`, unicode intact,
  nested tool arguments intact. A Python field rename that breaks Swift fails
  here, not in the app.
- **Forward compatibility.** An event type the client has never heard of decodes
  as `.unknown` and is not terminal; an unknown enum value (`scope: "decade"`)
  survives as `.other("decade")`. A newer server must not crash an older client.
- **Manifest.** The method list, event list, terminal set, protocol version,
  max frame size and error codes in `protocol.json` match the Swift constants.
- **Framing.** Two frames in one chunk; a frame split at every byte boundary
  reassembles (the newline is the only split point, so a chunk can end inside a
  three-byte character); an oversized frame refuses the connection; a partial
  frame at close is reported, not dropped; `params` is absent, not null; a JSON
  `1` is an int and not `true`.
- **Live service.** Handshake agrees on protocol, methods and events; `ping`;
  `tool.list` names a risk and a reason for every tool; `model.list` reports the
  policy; `auth.status` lists providers; `config.get(keys:)` narrows; a relative
  `/` and missing `working_dir` are refused before the model is touched; an
  unknown model is refused as a response, not an event; a protocol version the
  server cannot serve is reported; a call before `hello` is rejected; an unknown
  method is method-not-found.
- **Live model run** (with `AGENT_LIVE_MODEL`): a streamed run that calls tools,
  produces deltas, and ends on a terminal event, folded through `Transcript` —
  the timeline the app renders, checked against a real stream.
- **App model** (with `AGENT_REPO_ROOT`): the real `AppModel` and `Sidecar` —
  spawn the service, connect, read the policy, refuse a run with no working
  directory, close a run whose model is unreachable, reset on a new run, and
  (with `AGENT_LIVE_MODEL`) finish a real run and take the session id from
  `run.start`.

## Running the app

```sh
cd macapp/AgentClient
AGENT_REPO_ROOT=/path/to/SoloCoder swift run solocoder
```

A window opens: a chat over the event stream, a tool timeline, a permission
sheet driven by `permission_requested`, a model picker from `model.list`, and a
session browser from `session.list` / `session.load`. Choose a working directory
from the toolbar — a run needs one, and the app refuses to send without it.

## Things to know

- **The wire is snake_case.** Swift types are camelCase and every decoder that
  touches the wire uses `.convertFromSnakeCase`. `AgentEvent` decodes with the
  strategy **off**, because its payload keys are snake_case literals and
  `JSONValue` is a dictionary — the strategy would rewrite them.
- **`NSNumber` bridges to `Bool` for 1 and 0.** A JSON `1` answers `as? Bool` and
  becomes `true`, which turned a protocol version into a flag. Booleans are
  identified by identity against the `kCFBoolean` singletons before any numeric
  cast; `JSONValue.fromJSON` is the only place that touches `Any`.
- **Raw values are wire literals.** `PermissionMode.allowReadonly` is
  `"allow_readonly"`. `JSONEncoder` emits the raw value, so a camelCase raw
  value is a run that never starts.
- **`run.start` answers before the first token.** The reply carries
  `run_id`/`model`/`working_dir`; everything after it is events.
- **`permission_requested` arrives before the server blocks.** The client must
  answer with `permission.respond`; the server fails closed on timeout, so a
  client that never answers gets a denial and a `permission_resolved` with the
  reason.
- **One run per connection.** A second `run.start` on a busy connection is
  refused. Cancellation and permission replies are addressed to the run on that
  connection.
- **Socket paths are short.** macOS caps an `AF_UNIX` path at ~104 bytes, so the
  socket lives in a short temp directory, not in a deep `NSTemporaryDirectory()`
  tree.
- **The event stream is the connection's, not the run's.** It yields across runs
  and ends when the connection closes. A stream that finished at the first
  terminal event left the app with no events for its second run on the same
  connection — every run after the first. One-shot consumers break on
  `isTerminal`; the app keeps consuming and lets `Transcript` close each run.
- **`message_completed` is authoritative, deltas are progress.** The reducer
  appends deltas and then replaces the line with the completed text, so a stream
  that dies mid-message shows partial text and a stream that finishes shows
  exactly what the model said.
- **The user's own message is not in the stream.** The server has no reason to
  echo what it was told; the client inserts it. A loaded session's history is
  rendered the same way, from stored messages, not from invented events.
- **The session id comes from the service.** `run.start` answers with one; the
  app stores it and sends it on the next run, so the conversation continues.

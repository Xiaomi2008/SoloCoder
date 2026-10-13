# macapp — the Mac client

`AgentClient/` is a SwiftPM package: the client half of the agent service, with
no UI and no dependency on the app target. It speaks the protocol in
[`dev_docs/agent-service-protocol.md`](../dev_docs/agent-service-protocol.md)
over a Unix socket, and it is the layer the SwiftUI app is built on.

## Layout

```
AgentClient/
  Package.swift
  Sources/AgentClient/            the library
    Wire.swift                    envelope, framing, error codes, JSONValue
    AgentEvent.swift              every event type, 1:1 with the Python dataclasses
    AgentServiceClient.swift      connect, call, event stream, run, permission
    ContractFixtures.swift        loads the fixtures the Python side generates
  Sources/AgentClientChecks/      `swift run agent-checks` — the contract suite
  Tests/AgentClientTests/         the same checks as a Swift Testing suite
```

## Running the checks

```sh
cd macapp/AgentClient
swift run agent-checks                        # fixtures, framing, live service
AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s swift run agent-checks   # + a real streamed run
```

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
  produces deltas, and ends on a terminal event.

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

# macapp — the Mac client

`AgentClient/` is one SwiftPM package: the wire protocol, the app's state
machine and the views as libraries, and three executables over them — the app, a
contract suite that runs without Xcode, and a preview that puts the views on
screen with no service and no model. It speaks the protocol in
[`dev_docs/agent-service-protocol.md`](../dev_docs/agent-service-protocol.md)
over a Unix socket.

Everything with logic in it is a library, including the views: a SwiftPM
executable's types cannot be imported, so a target that holds them holds them
shut — no check, no preview, no second app can reach them.

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
    Markdown.swift                the answer's block structure (headings, fences,
                                  lists, tables) — parsed here, checked headless
    AppModel.swift                runs, permissions, models, sessions
    Commands.swift                the command palette's search and ranking
    Sidecar.swift                 spawns the service in development
  Sources/AgentAppUI/             the views, as a library
    RootView.swift                chat, tool rows, permission sheet, pickers,
                                  the ⌘K command palette
    SettingsView.swift            the Settings scene: defaults, models, servers
    MarkdownView.swift            the blocks, drawn
  Sources/AgentApp/               the @main: a window on the views
  Sources/UIPreview/              `swift run ui-preview` — the views on screen,
                                  fed by a scripted event stream
  Sources/UICost/                 `swift run ui-cost` — the streaming-cost numbers
  Sources/AgentClientChecks/      `swift run agent-checks` — the contract suite
  Tests/AgentClientTests/         the same checks as a Swift Testing suite
```

The split is deliberate: `AgentAppCore` is the app's logic and a test can import
it, so the state machine and the app model are checked headless. `AgentAppUI` is
the views, and a screenshot harness can import *it* — the pixels are still the
only thing no automated check reaches, and `ui-preview` is how they get looked
at.

## Running the checks

```sh
cd macapp/AgentClient
swift run agent-checks                        # fixtures, framing, live service, app model
AGENT_REPO_ROOT=.. swift run agent-checks     # enable the app-model checks
AGENT_REPO_ROOT=.. AGENT_LIVE_MODEL=lan/swift-1.5-iq3_s swift run agent-checks
AGENT_ENGINE=~/Projects/openagent-rs/target/release/openagent swift run agent-checks  # …against the Rust engine
swift run solocoder                           # the app itself
swift run ui-preview                          # the views, on a scripted transcript
UI_PREVIEW_SHORT=1 swift run ui-preview       # …top of the timeline in one window
swift run ui-cost                             # the streaming-cost numbers
```

`ui-preview` spawns no service and needs no model: it builds the events a run
produces, feeds them through the real `AppModel` — the real state machine, the
real delta buffer, the real `Transcript` — and opens the real `RootView` on the
result. What is on screen is what the service's events say, not a drawing of it,
and it can show the cases a model may not produce today: a failed tool, a
refused permission, a 40-line result, a table.

`solocoder` spawns its own service (`Sidecar`), so it needs a repository to run
it from: `AGENT_REPO_ROOT` pointing at the SoloCoder checkout, or a working
directory that contains `.venv/bin/python`. A shipped app runs the engine its
bundle carries instead (`scripts/build_app.sh`).

The engine is whatever speaks the socket protocol. `AGENT_ENGINE` names a
native engine binary — the Rust port, [openagent-rs](../../openagent-rs) — and
takes precedence over `AGENT_REPO_ROOT` in an unbundled app; a bundle always
runs its own. `scripts/build_app.sh --engine rust` bundles that binary as
`Contents/Resources/sidecar/openagent` in place of the interpreter: an 11 MB app
instead of 125 MB, the same `~/.openagent` state, and `scripts/package_check.sh`
gates both shapes. The Rust engine passes the same `agent-checks`, the
computer-use and browser flags included.

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
- **Markdown.** The block parser's rules: heading levels, `#tag` is not a
  heading, a fenced block keeps its indentation and its `**` verbatim, an
  unterminated fence is code to the end, a `~~~` fence is closed by `~~~` so a
  ```` ``` ```` inside it is content, a continuation line joins the list item
  above it, `1.5 miles` is not a list, a shell pipe is not a table (a table needs
  its `---` row), a numbered list keeps the model's own number. And the property
  the parser is written to guarantee: run a whole messy answer through it and
  every line reaches a block.
- **Tool output.** The row keeps the result — 40 lines stay 40 lines — and the
  label folds it to one bounded line; a 100k result is bounded at the head and
  says it was truncated.
- **App model** (with `AGENT_REPO_ROOT`): the real `AppModel` and `Sidecar` —
  spawn the service, connect, read the policy, refuse a run with no working
  directory, close a run whose model is unreachable, reset on a new run, and
  (with `AGENT_LIVE_MODEL`) finish a real run and take the session id from
  `run.start`.
- **Command palette.** `CommandSearch` is the ranking rule: an empty query keeps
  the list's order, every typed term must match, an exact title beats a title
  prefix, a title prefix beats a detail substring, and ties keep the list's
  order. The view draws the rows; the rule is checked headless.

## Running the app

```sh
cd macapp/AgentClient
AGENT_REPO_ROOT=/path/to/SoloCoder swift run solocoder
AGENT_ENGINE=/path/to/openagent-rs/target/release/openagent swift run solocoder
```

A window opens: a chat over the event stream, a tool timeline, a permission
sheet driven by `permission_requested`, a model picker from `model.list`, and a
session browser from `session.list` / `session.load`. Choose a working directory
from the status bar (it shows the path, and clicking it changes it) — a run
needs one, and the app refuses to send without it.

⌘K opens the command palette: the same actions as the sidebar, composer and
header, in one searchable list. ↑/↓ move the selection, ↵ runs it, Esc closes.
The ranking is `AgentAppCore`'s (`CommandSearch`), so `agent-checks` can assert
that `settings` selects “Open Settings” and `thinking off` selects the one row
that names both. ⌘, opens Settings, where the remembered defaults, the model
table, and the add-a-model-server form live.

**Approvals.** The composer's Tools menu has three modes: **Ask** (the default,
prompt before every risky tool), **Read-only** (run reads, refuse writes), and
**Auto** (approve every risky tool without a prompt). Auto sends
`permission_mode: allow_all` at `run.start`. The built-in file tools apply a
path check: a path that resolves outside the working directory is refused. That
check is defense-in-depth, not an OS sandbox. Shell commands are **not**
sandboxed yet — `cwd` is only where they start, and an absolute path or `cd /`
can still escape. If the mode is switched to Auto while a run is already in
flight, the client answers that run's `permission_requested` events itself and
calls `run.permission_mode` so the gate and file-tool scope change together. The
permission sheet also has **Auto-approve all**, which switches the mode and
answers the prompt on screen. The mode is remembered between launches.

`sandbox-exec` was removed from the design because Apple deprecated it. Auto
mode should be treated as unsafe until the VM/container boundary is implemented.

**Images.** ⌘V with an image on the clipboard attaches it to the composer, and
the composer sends it as a base64 PNG in `run.start`'s `images`. The system Paste
item is greyed for an image-only pasteboard in a `TextField`, so the app
intercepts ⌘V, and also offers **Edit → Paste Image** (⌘⇧V) and the photo button
in the composer. A clipboard with text is left to the field's own paste. An
image with no words sends the prompt “What is in this image?”; the selected model
must be vision-capable.

**Computer use** is off by default. Settings → Computer use turns on the macOS
GUI tools (`screenshot`, `click`, `double_click`, `type_text`, `key_combination`,
`move_mouse`, `scroll`, `wait`); the toggle persists and restarts the service,
because the tool set is fixed when `serve` starts. It needs the optional
dependencies and macOS permissions:

```sh
uv pip install -e ".[computer-use]"
```

and Screen Recording + Accessibility permission for the process driving the GUI.
The model must be vision-capable: the loop is screenshot → model reads image →
click coordinates. The app passes `--computer-use` to its sidecar only when the
setting is on; `agent-checks` asserts the flag exposes the tools and that without
it they are absent.

## The ten seconds of nothing

A reasoning model thinks before it answers, and the thinking arrives on the
wire. Measured on `swift-1.5-iq3_s` over the LAN server:

| | |
|---|---|
| First reasoning token | 55–311 ms |
| **First answer character** | **9.5–10.5 s** |
| Reasoning streamed | ~7,500 chars (≈1,900 tokens) |
| Answer streamed | ~1,300 chars (≈330 tokens) |
| Generation rate once answering | 150–330 tok/s |

The provider used to read only `delta.content`, so the user watched a spinner
that had not moved for ten seconds. Two changes:

- **`reasoning_delta` is an event.** Provider → runtime → protocol → a
  `ReasoningLine` in the timeline, open while it streams and collapsed once the
  answer starts. It is progress, never history: the model is not shown its own
  reasoning, and a test pins that.
- **The toolbar has a Thinking picker** (`model default` / off / minimal / low /
  medium / high), sent as `run.start`'s `reasoning_effort`. The server default is
  the slow path: one run produced **686,439 characters of private reasoning and
  no answer at all**, while `none` answered in **1.1 s**. The app's own default is
  `nil` — the model's business — and this control exists to get off it.

## Why a streamed answer feels slow (measured, not guessed)

The model is not the bottleneck, and the UI was. Numbers from
`swift run ui-cost` and a direct stream to `lan/swift-1.5-iq3_s`:

| What | Cost |
|---|---|
| Model, first token | 18–97 ms |
| Model, generation | 8–60 tok/s depending on context |
| One turn, real shape (8.5k-char prompt + 19 tool schemas) | 1.4–4.4 s |
| Folding one event into `Transcript` | 6.5 µs (9,726 events in 63 ms) |
| One `@Published` write | a SwiftUI invalidation of every view observing `AppModel` |

The model streams ~1.5 characters per chunk, so a 2k-character answer is ~1300
`message_delta` events. Each published write re-ran the toolbar, the model
picker, the session menu and every visible line, and a *selectable* streaming
`Text` rebuilt its text-kit view on every character. Three fixes:

- **`AppModel` buffers deltas and publishes on a 60 ms cadence.** 200 deltas
  cost 4 publishes, not 200 (checked). Order is preserved: any non-delta event
  flushes the buffer first, so `message_completed`, a permission request, a tool
  row and the terminal event land after the deltas they follow.
- **`LineView` is `Equatable` and one struct per row kind**, so `ForEach` skips
  lines whose `TranscriptLine` did not change.
- **Text selection is off while a line streams** (`TranscriptLine.streaming`),
  on once `message_completed` lands.

The floor that remains is the model: at 20 tok/s a 400-word answer is ~30 s, and
every tool round trip re-sends the whole context (~18k chars of system prompt
and tool schema, plus every prior tool result).

## The answer is markdown, and it is parsed once

The model answers in markdown and a plain `Text` shows the markup: `## Cause`,
`**bold**`, a fenced block whose alignment depends on leading spaces. So
`Markdown.blocks` (in `AgentAppCore`, checked) turns an answer into headings,
paragraphs, fenced code, both list kinds, a quote, a pipe table and a rule, and
`MarkdownView` draws them. Inline runs (`**bold**`, `` `code` ``, a link) are
`AttributedString(markdown:)`'s, run on a block's text and never on the whole
answer — a fenced block's `**` would be eaten by an inline parser, which is why
the fence is a block.

**A streaming answer is not parsed.** `Markdown.blocks` is O(n) in an answer that
grows per publish, so parsing per publish is O(n²) per run — the same trap that
made the thinking stream crawl. The stream shows plain text; the finished line is
markdown, parsed once.

The rule that governs the parser: **no input line is ever dropped.** A
mis-classified line renders as text. Where the real grammar and this one differ
(setext headings, nested lists, indented code) the difference is in a comment.

## Things to know

- **A SwiftPM executable's types cannot be imported.** The views lived in the
  executable target, which made them unreachable: the only way to see a change
  to a row was to launch the app, spawn the service, choose a directory and get
  a model to produce the answer shape you were rendering. `AgentAppUI` is a
  library, and `ui-preview` is the ten-second round trip.
- **`.none` on an optional enum property is `nil`, not the enum's `.none` case.**
  `model.reasoningEffort = .none` compiles, warns, and sets "model decides" —
  the slow path — where the check meant to ask for `ReasoningEffort.none`, "off".
  The live check measured the fast half of a reasoning model while its comment
  said it asked for `none`. Spell the case in full.
- **A toolbar group wider than the window overflows into a `>>` chevron.**
  Silently: the controls are gone and the window looks fine. The group shares the
  window's 900 points with the title, so the count is what matters, not each
  control's size.
- **`LazyVStack` gives a chat its scroll pin for free.** The last line's
  `onAppear`/`onDisappear` is exactly "the user is at the bottom" / "the user has
  scrolled away", with no `GeometryReader` per row to pay for. Follow the
  **last line's value** (it changes on a new line and on text growth), not the
  line count: a 2k-character answer is one line, so a count-based rule leaves the
  view still while the answer runs off the bottom.
- **A `body` with a `switch` must be declared in the struct, not an
  extension.** Swift applies the result-builder transform to a `body` in the
  type's own declaration and not to one in an extension; in an extension a
  multi-branch `switch` fails with "branches have mismatching types" for
  branches that are all `View`s.
- **The app watches its own sidecar's stdin.** `Sidecar` hands the service a
  pipe on stdin and the service exits when it EOFs. A killed app leaves nothing
  behind; `willTerminateNotification` covers a normal quit, and this covers
  `kill` and SIGKILL.
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

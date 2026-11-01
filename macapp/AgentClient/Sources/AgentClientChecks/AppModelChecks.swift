import AgentAppCore
import AgentClient
import AppKit
import Foundation

// The app's own state machine, driven against a service it spawned.
//
// This is the highest-level check in the suite: no views, no screen, but the
// real `AppModel`, the real `Sidecar`, the real socket. It is what catches the
// app-shaped bugs a protocol test cannot see — a run that never starts and
// leaves the composer spinning, a session id that is invented instead of taken
// from the service, a permission prompt the UI never presents.

@MainActor
private func settled(_ model: AppModel, timeout: TimeInterval) async -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while model.busy {
        if Date() > deadline { return false }
        try? await Task.sleep(nanoseconds: 100_000_000)
    }
    return true
}

private func ev(_ json: String) throws -> AgentEvent {
    try AgentEvent.decode(from: Data(json.utf8))
}

/// The cost of a streamed answer, which is what "the UI feels slow" measures.
///
/// The model streams ~1.5 characters per chunk, so a 2k-character answer is
/// ~1300 `message_delta` events. Folding one costs 6.5 microseconds — the cost
/// is the view invalidation each published write causes, which re-runs the
/// toolbar, the model picker, the session menu and every visible line. So the
/// app buffers deltas and publishes on a cadence. These checks pin that down:
/// a run of 200 deltas must not cost 200 publishes, and buffering must not lose
/// text, reorder a tool row, or leave a line marked as still streaming.
/// Let the consumer task drain the stream, then publish what it buffered.
/// `quiesce` alone is not enough: the events are in the stream's buffer, not
/// yet in the app's delta buffer.
@MainActor
private func settle(_ model: AppModel) async {
    try? await Task.sleep(nanoseconds: 30_000_000)
    model.quiesce()
}

@MainActor
private func streamingCostChecks(_ checks: Checks) async throws {
    let (stream, continuation) = AsyncStream<AgentEvent>.makeStream()
    let model = AppModel()
    model.ingest(stream)

    for chunk in 0..<200 {
        continuation.yield(
            try ev(#"{"type":"message_delta","run_id":"r","message_id":"m1","delta":"tok\#(chunk) "}"#)
        )
    }
    await settle(model)
    let midText = model.transcript.lines.compactMap {
        if case .assistant(let text) = $0.kind { return text } else { return nil }
    }.joined()
    checks.expect(midText.hasPrefix("tok0 "), "text is visible while streaming, not held back")
    let streamingLine = model.transcript.lines.first { $0.messageId == "m1" }
    checks.expect(streamingLine?.streaming == true, "a mid-stream line is marked streaming (text selection stays off)")

    continuation.yield(
        try ev(#"{"type":"tool_call_started","run_id":"r","message_id":"m1","tool_call_id":"t1","tool_name":"bash","arguments":{"command":"ls"}}"#)
    )
    await settle(model)
    let ids = model.transcript.lines.map(\.id)
    checks.expect(
        ids.firstIndex(of: "m1")! < ids.firstIndex(of: "t1")!,
        "buffered deltas land before the tool row that follows them"
    )

    continuation.yield(
        try ev(#"{"type":"message_completed","run_id":"r","message_id":"m1","output_text":"the authoritative answer"}"#)
    )
    continuation.yield(
        try ev(#"{"type":"run_completed","run_id":"r","final_message_id":"m1","result":{"run_id":"r","final_message_id":"m1","output_text":"the authoritative answer","stop_reason":"end_turn"}}"#)
    )
    continuation.finish()
    while model.transcript.state.isRunning {
        try await Task.sleep(nanoseconds: 20_000_000)
    }
    await settle(model)
    let final = model.transcript.lines.first { $0.messageId == "m1" }
    checks.expect(
        final?.kind == .assistant("the authoritative answer"),
        "the completed text wins over the buffered deltas"
    )
    checks.expect(final?.streaming == false, "a finished line is not streaming (text selection turns on)")
    checks.expect(
        model.transcriptPublishes < 100,
        "200 deltas cost \(model.transcriptPublishes) publishes, not 200"
    )
    checks.note("200 deltas -> \(model.transcriptPublishes) publishes")
}

/// The cost of a streamed *thinking* phase — the phase that felt slow.
///
/// A reasoning model's private tokens arrive before the answer and far outnumber
/// it: ~1,900 reasoning tokens before the first answer character, and one run
/// streamed 686,439 characters of thinking with no answer at all. The 60 ms
/// buffer was written for `message_delta` and left `reasoning_delta` publishing
/// one write per delta, so the fix covered the short half of the run and the app
/// went back to ~1,300 view invalidations a turn exactly when the model started
/// to think. These checks pin the long half: the thinking stream is coalesced
/// like the answer, coalescing loses no characters, the thinking line sits above
/// the answer it produced, and the answer's first delta collapses it.
@MainActor
private func reasoningCostChecks(_ checks: Checks) async throws {
    let (stream, continuation) = AsyncStream<AgentEvent>.makeStream()
    let model = AppModel()
    model.ingest(stream)

    continuation.yield(try ev(#"{"type":"run_started","run_id":"r"}"#))
    continuation.yield(try ev(#"{"type":"message_started","run_id":"r","message_id":"m1"}"#))
    await settle(model)
    let publishesAtStart = model.transcriptPublishes

    var expected = ""
    for chunk in 0..<400 {
        let delta = "think\(chunk) "
        expected += delta
        continuation.yield(
            try ev(#"{"type":"reasoning_delta","run_id":"r","message_id":"m1","delta":"\#(delta)"}"#)
        )
    }
    await settle(model)

    func thinkingLine(_ model: AppModel) -> TranscriptLine? {
        model.transcript.lines.first {
            if case .reasoning = $0.kind { return true } else { return false }
        }
    }
    let thinking = thinkingLine(model)
    let text: String
    if case .reasoning(let accumulated) = thinking?.kind { text = accumulated } else { text = "" }
    checks.expect(
        text == expected,
        "coalescing the thinking stream loses nothing: \(text.count) chars of \(expected.count)"
    )
    checks.expect(thinking?.streaming == true, "the thinking line is streaming while the model thinks")
    let publishes = model.transcriptPublishes - publishesAtStart
    checks.expect(publishes < 100, "400 reasoning deltas cost \(publishes) publishes, not 400")
    checks.note("400 reasoning deltas -> \(publishes) publishes")

    continuation.yield(
        try ev(#"{"type":"message_delta","run_id":"r","message_id":"m1","delta":"so "}"#)
    )
    await settle(model)
    let ids = model.transcript.lines.map(\.id)
    if let thinking {
        checks.expect(
            ids.firstIndex(of: thinking.id)! < ids.firstIndex(of: "m1")!,
            "the thinking line stays above the answer it produced"
        )
    }
    checks.expect(
        thinkingLine(model)?.streaming == false,
        "the answer's first delta collapses the thinking"
    )
    continuation.finish()
}

/// The thinking stream is bounded, and bounding it loses nothing the app uses.
///
/// One run streamed 686,439 characters of private reasoning. Kept whole, that is
/// a string every delta copies and every publish copies and compares, so the line
/// keeps a tail (see `Transcript.reasoningKeep`). The tail has to be the *end* of
/// the thinking — the part arriving, the part the view shows — and the bound has
/// to hold, or the cap is a comment.
@MainActor
private func reasoningCapChecks(_ checks: Checks) async throws {
    let (stream, continuation) = AsyncStream<AgentEvent>.makeStream()
    let model = AppModel()
    model.ingest(stream)

    continuation.yield(try ev(#"{"type":"run_started","run_id":"r"}"#))
    continuation.yield(try ev(#"{"type":"message_started","run_id":"r","message_id":"m1"}"#))

    let chunks = 3_000
    for chunk in 0..<chunks {
        continuation.yield(
            try ev(#"{"type":"reasoning_delta","run_id":"r","message_id":"m1","delta":"think\#(chunk) "}"#)
        )
    }
    await settle(model)

    let text: String
    if case .reasoning(let kept) = model.transcript.lines.first(where: {
        if case .reasoning = $0.kind { return true } else { return false }
    })?.kind { text = kept } else { text = "" }

    checks.expect(
        text.hasSuffix("think\(chunks - 1) "),
        "the kept tail is the end of the thinking, not its beginning (\(text.utf8.count) chars kept)"
    )
    checks.expect(
        text.utf8.count <= 20_000,
        "a \(chunks * 12)-character thinking stream is bounded in the transcript: \(text.utf8.count) chars kept"
    )
    checks.note("thinking stream: \(chunks * 12) chars streamed, \(text.utf8.count) kept")
    continuation.finish()
}

/// The composer's gate: what Send may do, and what it says when it may not.
///
/// The rule is `AppModel.sendBlock`, which is also `send`'s own guard — the
/// button and the call get their answer from one place, so a Send that looks
/// enabled cannot refuse, and one that is disabled has words to explain itself.
/// The other half of the composer — which key sends, which one writes a newline —
/// is keyboard state, and `swift run ui-preview` is where that is seen. What is
/// checked here is the gate under it: a Send that fires on any of those keys can
/// only fire when there is a connection, a scope and a prompt.
@MainActor
func composerChecks(_ checks: Checks) {
    print("composer")

    func ready() -> AppModel {
        let model = AppModel()
        model.connected = true
        model.workingDir = "/tmp/oa-work"
        return model
    }

    // A model built in this process may restore the scope a previous model
    // stored, so a check about "no scope" has to say so.
    func coldModel() -> AppModel {
        let model = AppModel()
        model.workingDir = ""
        return model
    }

    let cold = coldModel()
    checks.equal(cold.sendBlock("hello"), .notConnected, "no service: Send is blocked on the connection")
    checks.equal(cold.sendBlock(""), .notConnected, "…and that outranks an empty field")

    let noScope = coldModel()
    noScope.connected = true
    checks.equal(noScope.sendBlock("hi"), .noWorkingDir, "no working directory: Send is blocked on the scope")

    let blank = ready()
    checks.equal(blank.sendBlock("   \n\t "), .emptyDraft, "whitespace is not a prompt")
    checks.equal(blank.sendBlock(""), .emptyDraft, "so is nothing")
    checks.equal(
        blank.sendBlock("", hasImages: true), nil,
        "an image with no words is sendable: the image is the prompt"
    )
    checks.equal(blank.sendBlock("fix the parser"), nil, "connected, scoped, non-empty: Send is enabled")

    let running = ready()
    running.busy = true
    checks.equal(running.sendBlock("fix the parser"), .busy, "one run per connection: a second Send is blocked")
    running.workingDir = ""
    checks.equal(running.sendBlock("fix the parser"), .busy, "…and the run in flight is the reason shown")

    for reason in [
        AppModel.SendBlock.notConnected, .busy, .noWorkingDir, .emptyDraft,
    ] {
        checks.expect(!reason.reason.isEmpty, "every block has a reason the composer can show: \(reason)")
    }

    // The banner is prose. Killing the app's own service and sending once drew
    // "notConnected" in the card — the enum's mirror name, a name for a state
    // rather than a sentence about what happened.
    checks.equal(AppModel.describe(ClientError.notConnected), "not connected", "a transport failure reads as a sentence")
    checks.equal(AppModel.describe(ClientError.closed), "the connection closed", "…and so does a socket that closed")
    checks.equal(
        AppModel.describe(ClientError.timedOut("run.start")), "timed out waiting for run.start",
        "…and a timeout names what it waited for"
    )
    struct Bare: Error {}
    checks.equal(AppModel.describe(Bare()), "\(Bare())", "an error with no sentence of its own keeps the mirror name")

    // A refused send hands its reason back, because the composer has to keep the
    // words. It used to clear the field on its way out, so the card explained a
    // prompt that was already gone and the timeline had no row for it.
    let refused = coldModel()
    refused.connected = true
    checks.equal(refused.send("hi"), .noWorkingDir, "a refused send hands its reason back")
    checks.equal(
        refused.banner, "choose a working directory — it is the scope the agent works in",
        "…and shows the same words the button uses: \(refused.banner ?? "nil")"
    )
    let unconnected = coldModel()
    checks.equal(unconnected.send("hi"), .notConnected, "a send with no service is refused too, not swallowed")

    // The scope is remembered between launches. It used not to be: every launch
    // started with no scope, and the first prompt of a session was refused by a
    // modal — "choose a working directory — it is the scope the agent works in" —
    // which is what stopped the live loop test from running at all.
    checks.equal(AppModel.restorableWorkingDir(nil), "", "no stored scope: nothing to restore")
    checks.equal(AppModel.restorableWorkingDir(""), "", "an empty stored scope is not a scope")
    checks.equal(AppModel.restorableWorkingDir("/tmp"), "/tmp", "a stored scope that is still a directory comes back")
    checks.equal(
        AppModel.restorableWorkingDir("/tmp/oa-scope-that-is-gone"), "",
        "a stored scope that no longer exists is dropped, not restored"
    )
    checks.equal(
        AppModel.restorableWorkingDir("/etc/hosts"), "",
        "…and so is a stored scope that turned out to be a file"
    )
    let firstLaunch = AppModel()
    firstLaunch.workingDir = "/tmp"
    checks.equal(AppModel().workingDir, "/tmp", "the next launch starts scoped")
    firstLaunch.workingDir = ""
    checks.equal(AppModel().workingDir, "", "…and forgetting the scope is remembered too")
}

/// What a launch starts with, and what it is allowed to keep.
///
/// The scope is remembered (see `composerChecks`); the model, the permission
/// mode and the reasoning effort used not to be, so every launch took the
/// sidecar's `default_model` and `Ask` and the user re-picked what they had
/// already picked. A remembered choice is only as good as the thing that can
/// invalidate it: a scope is a directory, and a model is a row in the table.
@MainActor
func launchChecks(_ checks: Checks) {
    print("launch settings")

    func row(_ provider: String, _ model: String, efforts: [String]? = nil) -> ModelRow {
        let listed = efforts.map { ",\"reasoning_efforts\":[" + $0.map { "\"\($0)\"" }.joined(separator: ",") + "]" } ?? ""
        let json = """
            {"model":"\(model)","provider":"\(provider)","wire_api":"chat","auth_mode":"api_key", \
            "label":"","family":"other","is_local":true, \
            "caps":{"tools":false,"vision":false,"reasoning":false,"streaming":true,"json_mode":false, \
            "context_window":8192,"max_output_tokens":null,"probed":false\(listed)}, \
            "cost":{"kind":"free","input_per_mtok":null,"output_per_mtok":null}}
            """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try! decoder.decode(ModelRow.self, from: Data(json.utf8))
    }

    let rows = [row("lan", "swift-1.5"), row("openai", "gpt-6-x")]
    checks.equal(AppModel.restorableModel(nil, in: rows), "", "no stored model: the router chooses")
    checks.equal(
        AppModel.restorableModel("lan/swift-1.5", in: rows), "lan/swift-1.5",
        "a stored model the table still has comes back"
    )
    checks.equal(
        AppModel.restorableModel("lan/gone-overnight", in: rows), "",
        "a stored model the table dropped is not restored — the toolbar would name a model the picker has no row for"
    )
    checks.equal(
        AppModel.restorableModel("lan/gone-overnight", in: []), "lan/gone-overnight",
        "an unloaded table is not evidence a model is gone, so the key waits for `refreshModels`"
    )
    checks.equal(AppModel.restorableMode(nil), .ask, "no stored mode: ask")
    checks.equal(
        AppModel.restorableMode("allow_readonly"), .allowReadonly,
        "the mode the user picked in the toolbar comes back"
    )
    checks.equal(
        AppModel.restorableMode("allow_all"), .allowAll,
        "the auto-approve mode the user picked in the toolbar comes back"
    )
    checks.equal(AppModel.restorableMode("yolo"), .ask, "a mode the enum does not know is the default")
    checks.equal(AppModel.restorableEffort(nil), nil, "no stored effort: the model decides")
    // Spelled `ReasoningEffort.none`, never `.none`: in a context whose type is
    // `ReasoningEffort?`, `.none` is `Optional.none` — nil — and the check
    // compares nil to nil and passes for the wrong reason.
    checks.equal(
        AppModel.restorableEffort("none"), ReasoningEffort.none,
        "…and a stored \"none\" is a different answer from no stored key"
    )
    checks.equal(AppModel.restorableEffort("sideways"), nil, "an effort the enum does not know is not a choice")

    let firstLaunch = AppModel()
    firstLaunch.selectedModel = "lan/swift-1.5"
    firstLaunch.permissionMode = .allowReadonly
    firstLaunch.reasoningEffort = ReasoningEffort.none
    firstLaunch.computerUse = true
    let next = AppModel()
    checks.equal(next.selectedModel, "lan/swift-1.5", "the next launch asks for the same model")
    checks.equal(next.permissionMode, .allowReadonly, "…under the same permission mode")
    checks.equal(next.reasoningEffort, ReasoningEffort.none, "…with the same reasoning effort")
    checks.equal(next.computerUse, true, "…and the same computer-use choice")
    firstLaunch.selectedModel = ""
    firstLaunch.permissionMode = .ask
    firstLaunch.reasoningEffort = nil
    firstLaunch.computerUse = false
    let cleared = AppModel()
    checks.equal(cleared.selectedModel, "", "clearing the model to the router's choice is remembered too")
    checks.equal(cleared.permissionMode, .ask, "…the mode back to ask")
    checks.equal(cleared.reasoningEffort, nil, "…and the effort back to \"model decides\"")
    checks.equal(cleared.computerUse, false, "…and computer use back to off")

    // Thinking levels follow the model. The ChatGPT backend lists them per
    // model and answers anything else with a 400 mid-run.
    let thinking = AppModel()
    thinking.modelRows = [row("openai_oauth", "gpt-6.1-sol", efforts: ["low", "medium", "high", "xhigh", "max"]), row("lan", "swift-1.5")]
    thinking.selectedModel = "lan/swift-1.5"
    checks.equal(thinking.availableEfforts, ReasoningEffort.generic, "a model with no listed levels offers the generic ones")
    thinking.reasoningEffort = ReasoningEffort.none
    thinking.selectedModel = "openai_oauth/gpt-6.1-sol"
    checks.equal(thinking.availableEfforts, [.low, .medium, .high, .xhigh, .max], "a listed model offers its own levels, xhigh and max included")
    checks.equal(thinking.reasoningEffort, nil, "a level the new model refuses falls back to the model default")
    thinking.reasoningEffort = .xhigh
    thinking.selectedModel = ""
    checks.equal(thinking.reasoningEffort, nil, "…and so does one the router's generic list lacks")
}

/// The context meter: usage events fill it without touching the conversation,
/// and a new conversation empties it.
@MainActor
private func contextMeterChecks(_ checks: Checks) async throws {
    let (stream, continuation) = AsyncStream<AgentEvent>.makeStream()
    let model = AppModel()
    model.ingest(stream)
    continuation.yield(try ev(#"{"type":"run_started","run_id":"r1"}"#))
    await settle(model)
    let publishes = model.transcriptPublishes
    checks.equal(model.contextUsed, nil, "no conversation, nothing to measure")

    continuation.yield(try ev(#"{"type":"context_usage","run_id":"r1","used_tokens":12170,"context_window":262144,"compact_at":203161}"#))
    try? await Task.sleep(nanoseconds: 30_000_000)
    checks.equal(model.contextUsed, 12170, "context_usage fills the meter")
    checks.equal(model.compactAt, 203161, "…with the compaction point")
    checks.equal(model.contextWindow, 262144, "the run's window stands in when no model is selected")
    checks.equal(model.transcriptPublishes, publishes, "usage does not republish the transcript")

    continuation.yield(try ev(#"{"type":"context_usage","run_id":"r1","used_tokens":900,"context_window":null,"compact_at":null}"#))
    try? await Task.sleep(nanoseconds: 30_000_000)
    checks.equal(model.contextUsed, 900, "an unknown window still reports the tokens")
    checks.equal(model.contextWindow, nil, "…and no window")

    model.newRun()
    checks.equal(model.contextUsed, nil, "a new conversation empties the meter")
    model.quiesce()
}

/// The live view: frames reach the panel, and only the panel.
@MainActor
private func browserPanelChecks(_ checks: Checks) async throws {
    let picture = NSImage(size: NSSize(width: 4, height: 3))
    picture.lockFocus()
    NSColor.systemPurple.setFill()
    NSRect(x: 0, y: 0, width: 4, height: 3).fill()
    picture.unlockFocus()
    let jpeg = NSBitmapImageRep(data: picture.tiffRepresentation!)!.representation(using: .jpeg, properties: [:])!
    func frame(_ run: String, _ url: String) throws -> AgentEvent {
        try ev(#"{"type":"browser_frame","run_id":"\#(run)","data":"\#(jpeg.base64EncodedString())","url":"\#(url)","title":"Shop","width":4,"height":3}"#)
    }

    let (stream, continuation) = AsyncStream<AgentEvent>.makeStream()
    let model = AppModel()
    model.ingest(stream)
    continuation.yield(try ev(#"{"type":"run_started","run_id":"r1"}"#))
    continuation.yield(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"looking"}"#))
    await settle(model)
    let publishes = model.transcriptPublishes
    let lines = model.transcript.lines.count

    continuation.yield(try frame("r1", "https://shop.example/"))
    continuation.yield(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":" around"}"#))
    for _ in 0..<20 { continuation.yield(try frame("r1", "https://shop.example/cart")) }
    try? await Task.sleep(nanoseconds: 30_000_000)

    checks.expect(model.browserPanel.isVisible, "a run's first frame opens the live view")
    checks.equal(model.browserPanel.url, "https://shop.example/cart", "the panel shows the newest frame")
    checks.equal(model.browserPanel.framesShown, 21, "every frame reaches the panel")
    checks.expect(model.browserPanel.image != nil, "the JPEG decodes")
    checks.equal(model.transcriptPublishes, publishes, "frames do not republish the transcript")
    checks.equal(model.transcript.lines.count, lines, "frames add nothing to the conversation")
    model.quiesce()
    let text = model.transcript.lines.compactMap {
        if case .assistant(let text) = $0.kind { return text } else { return nil }
    }.joined()
    checks.equal(text, "looking around", "a frame between deltas does not split or drop them")

    model.browserPanel.dismiss(runId: "r1")
    continuation.yield(try frame("r1", "https://shop.example/checkout"))
    try? await Task.sleep(nanoseconds: 30_000_000)
    checks.expect(!model.browserPanel.isVisible, "closed stays closed for the rest of the run")
    continuation.yield(try frame("r2", "https://news.example/"))
    try? await Task.sleep(nanoseconds: 30_000_000)
    checks.expect(model.browserPanel.isVisible, "…and the next run's first frame opens it again")
    continuation.finish()
}

@MainActor
func appChecks(_ checks: Checks) async {
    print("app model")
    do {
        try await streamingCostChecks(checks)
    } catch {
        checks.expect(false, "streaming cost checks ran: \(error)")
    }
    do {
        try await reasoningCostChecks(checks)
        try await reasoningCapChecks(checks)
    } catch {
        checks.expect(false, "reasoning cost checks ran: \(error)")
    }
    do {
        try await browserPanelChecks(checks)
        try await contextMeterChecks(checks)
    } catch {
        checks.expect(false, "browser panel checks ran: \(error)")
    }
    // The root the sidecar spawns from, in the shape the launcher needs.
    //
    // `AGENT_REPO_ROOT` is documented as `..`-shaped, and the root is used twice:
    // as the child's cwd and as the base of the executable path. A relative URL
    // is a hop applied to both, and the spawn looks for the venv beside the cwd —
    // which shows up as the check below failing with "not connected", with no
    // hint that the cause is a path. So the root is asserted to be absolute and
    // to carry an executable interpreter, before anything is spawned.
    // `AGENT_ENGINE` names a native engine only when it is an executable file;
    // a typo must fall through to the Python launch, not spawn nothing.
    let configuredEngine = ProcessInfo.processInfo.environment["AGENT_ENGINE"]
    setenv("AGENT_ENGINE", "/bin/sh", 1)
    checks.expect(Sidecar.engineOverride()?.path == "/bin/sh", "AGENT_ENGINE names a native engine")
    checks.expect(
        Sidecar.resolveLaunch() == .native(engine: URL(fileURLWithPath: "/bin/sh")),
        "an unbundled app launches AGENT_ENGINE before any repository"
    )
    checks.expect(Sidecar.Launch.native(engine: URL(fileURLWithPath: "/bin/sh")).script == nil, "a native engine has no script")
    setenv("AGENT_ENGINE", "/definitely/not/an/engine", 1)
    checks.expect(Sidecar.engineOverride() == nil, "an AGENT_ENGINE that is not executable is ignored")
    if let configuredEngine { setenv("AGENT_ENGINE", configuredEngine, 1) } else { unsetenv("AGENT_ENGINE") }

    if let engine = Sidecar.engineOverride() {
        checks.note("live sidecar: native engine \(engine.path)")
    } else if let root = Sidecar.repositoryRoot() {
        checks.expect(
            root.isFileURL && root.path.hasPrefix("/") && !root.pathComponents.contains(".."),
            "the sidecar's root is absolute, with no hops left in it: \(root.path)"
        )
        checks.expect(
            FileManager.default.isExecutableFile(
                atPath: root.appendingPathComponent(".venv/bin/python").path
            ),
            "the root has an executable .venv/bin/python: \(root.path)"
        )
    } else {
        checks.note("skipped: no repository with .venv/bin/python (set AGENT_REPO_ROOT or AGENT_ENGINE)")
        return
    }

    let liveModel = ProcessInfo.processInfo.environment["AGENT_LIVE_MODEL"]

    // HOME points at a sandbox: the app saves a session for every run, and a
    // suite that writes into the developer's ~/.openagent is a suite that gets
    // turned off. AGENT_LIVE_MODEL opts out, because the model servers a real
    // run routes to are the developer's — their config, their Keychain keys.
    let sandbox = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("oaapp\(getpid())")
    let home = liveModel.map { _ in FileManager.default.homeDirectoryForCurrentUser }
        ?? sandbox.appendingPathComponent("home")
    try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
    if liveModel == nil {
        setenv("HOME", home.path, 1)
        setenv("XDG_CONFIG_HOME", home.appendingPathComponent(".config").path, 1)
    }
    defer {
        unsetenv("XDG_CONFIG_HOME")
        unsetenv("HOME")
        try? FileManager.default.removeItem(at: sandbox)
    }

    let work = sandbox.appendingPathComponent("work")
    try? FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)

    // The launch flag the app's Settings toggle turns on: a service started with
    // `--computer-use` must advertise the GUI tools, and one without it must not.
    // This is the flag, not a screenshot call: pyautogui is only imported when a
    // tool runs, so the check works without the optional dependency installed.
    do {
        let probe = Sidecar()
        try await probe.start(computerUse: true)
        let client = AgentServiceClient(
            endpoint: ServiceEndpoint(socketPath: probe.socketPath, clientName: "computer-use-check")
        )
        _ = try await client.connect()
        let names = try await client.tools().map(\.name)
        checks.expect(names.contains("screenshot"), "--computer-use exposes screenshot")
        checks.expect(names.contains("click"), "--computer-use exposes click")
        await client.close()
        probe.stop()

        // The same Sidecar, restarted — what a Settings toggle does. A `Process`
        // launches once; reusing it raised "task already launched" and crashed the app.
        try await probe.start(computerUse: false)
        let plainClient = AgentServiceClient(
            endpoint: ServiceEndpoint(socketPath: probe.socketPath, clientName: "computer-use-off-check")
        )
        _ = try await plainClient.connect()
        let plainNames = try await plainClient.tools().map(\.name)
        checks.expect(!plainNames.contains("screenshot"), "without the flag the GUI tools are absent")
        checks.expect(!plainNames.contains("browser_navigate"), "without --browser-use the browser tools are absent")
        await plainClient.close()
        probe.stop()

        try await probe.start(browserUse: true)
        let browserClient = AgentServiceClient(
            endpoint: ServiceEndpoint(socketPath: probe.socketPath, clientName: "browser-use-check")
        )
        _ = try await browserClient.connect()
        let browserNames = try await browserClient.tools().map(\.name)
        checks.expect(browserNames.contains("browser_navigate"), "--browser-use exposes browser_navigate")
        checks.expect(browserNames.contains("browser_click"), "--browser-use exposes browser_click")
        await browserClient.close()
        probe.stop()
    } catch {
        checks.expect(false, "computer-use launch flag check ran: \(error)")
    }

    let model = AppModel()
    model.workingDir = work.path
    model.start()

    let deadline = Date().addingTimeInterval(45)
    while !model.connected, Date() < deadline {
        try? await Task.sleep(nanoseconds: 100_000_000)
    }
    checks.expect(model.connected, "the app connected to the service it spawned (\(model.status))")
    guard model.connected else { model.stop(); return }

    checks.expect(model.status.contains("openagent"), "the status line names the server")
    await model.refreshModels()
    checks.expect(!model.modelPolicy.isEmpty, "the app read the routing policy")

    // The composer gate: a run needs a scope, and the app says so instead of
    // sending a request the server would refuse.
    model.workingDir = ""
    model.send("hello")
    checks.expect(
        model.banner == AppModel.SendBlock.noWorkingDir.reason,
        "a run without a working directory is refused by the app, in the words the Send button shows"
    )
    checks.expect(!model.busy, "and the composer is not left spinning")
    model.workingDir = work.path

    // A run whose model is not reachable must end as a failure, not a hang: the
    // transcript closes itself when the stream says so.
    model.send("say hi")
    let settledOff = await settled(model, timeout: 90)
    checks.expect(settledOff, "a run that cannot reach its model settles")
    checks.expect(model.transcript.lines.contains { if case .user = $0.kind { return true } else { return false } }, "the user's own message is in the timeline")
    checks.expect(model.transcript.state.isRunning == false, "the run is closed")
    checks.note("state after an unreachable run: \(model.transcript.state.label)")

    model.newRun()
    checks.expect(model.transcript.lines.isEmpty, "a new run starts from an empty timeline")
    checks.expect(model.sessionId == nil, "and a new session — the id comes from the service, not the app")

    if let modelKey = liveModel, !modelKey.isEmpty {
        model.selectedModel = modelKey
        model.permissionMode = .allowReadonly
        // The knob the ten-seconds-of-nothing is about. A reasoning model's
        // server default spent ~10s on private thinking before the first answer
        // character, and in one run 686,439 characters of it with no answer at
        // all; "none" answers in ~1s. The app has to be able to ask for that.
        //
        // Spelled in full, on purpose: `.none` here is `Optional.none` — nil,
        // "model decides" — which is the slow path this check exists to avoid.
        // The compiler warns and picks the wrong one.
        model.reasoningEffort = ReasoningEffort.none
        // Wall clock and publishes for a real streamed answer: the number that
        // says what the user waits for, and the number that says what the UI
        // does while they wait.
        let publishesBefore = model.transcriptPublishes
        let t0 = DispatchTime.now().uptimeNanoseconds
        model.send("List the files in this directory with a tool, then tell me how many there are.")
        let finished = await settled(model, timeout: 240)
        let seconds = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e9
        checks.expect(finished, "a real run finishes")
        checks.expect((model.transcript.finalText ?? "").isEmpty == false, "the app shows the model's answer")
        checks.expect(!model.transcript.toolRows.isEmpty, "the app shows the tool the model used")
        checks.expect(model.sessionId != nil, "the session id came back from run.start")
        let chars = (model.transcript.finalText ?? "").count
        checks.note("app run: \(model.transcript.lines.count) lines, session \(model.sessionId ?? "?"), \(model.transcript.state.label)")
        checks.note(
            "app run wall \(String(format: "%.1f", seconds))s for \(chars) chars of answer, "
            + "\(model.transcriptPublishes - publishesBefore) publishes, "
            + "\(model.transcript.lines.count) lines"
        )

        // The answer to a prompt the app never asked for is a bug the sheet
        // cannot recover from; the transcript is the only source of truth.
        checks.expect(model.promptToAnswer == nil, "nothing is left waiting for an answer")

        // The thinking path, on a real model. The run above asks for `none`, so
        // it measures the fast half of a reasoning model and never streams a
        // private token — which is how the thinking path came to be the slow one
        // with the answer path's checks green. This is the phase the user waits
        // through, so it gets the same two numbers: what they wait for, and what
        // the UI does while they wait.
        model.newRun()
        model.reasoningEffort = ReasoningEffort.medium
        let thinkingPublishesBefore = model.transcriptPublishes
        let t1 = DispatchTime.now().uptimeNanoseconds
        model.send("Think this through carefully before answering: how many times does the letter 'p' appear in 'supercalifragilisticexpialidocious'?")
        let thought = await settled(model, timeout: 240)
        let thinkingSeconds = Double(DispatchTime.now().uptimeNanoseconds - t1) / 1e9
        let thinking = model.transcript.lines.first {
            if case .reasoning = $0.kind { return true } else { return false }
        }
        let kept: Int
        if case .reasoning(let accumulated) = thinking?.kind { kept = accumulated.utf8.count } else { kept = 0 }
        let thinkingPublishes = model.transcriptPublishes - thinkingPublishesBefore
        checks.expect(thought, "a thinking run finishes")
        checks.expect(kept > 0, "the app shows the model thinking, not a still spinner (\(kept) chars kept)")
        checks.expect(
            thinkingPublishes < 200,
            "a thinking run costs \(thinkingPublishes) publishes, not one per reasoning delta"
        )
        checks.note(
            "thinking run: \(String(format: "%.1f", thinkingSeconds))s, \(kept) chars of thinking kept, "
            + "\(thinkingPublishes) publishes, \(model.transcript.lines.count) lines"
        )
        model.reasoningEffort = ReasoningEffort.none
    }

    model.stop()
    checks.expect(!model.connected, "stopping closes the connection")
}

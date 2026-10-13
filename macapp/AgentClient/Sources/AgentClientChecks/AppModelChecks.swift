import AgentAppCore
import AgentClient
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

@MainActor
func appChecks(_ checks: Checks) async {
    print("app model")
    do {
        try await streamingCostChecks(checks)
    } catch {
        checks.expect(false, "streaming cost checks ran: \(error)")
    }
    guard Sidecar.repositoryRoot() != nil else {
        checks.note("skipped: no repository with .venv/bin/python (set AGENT_REPO_ROOT)")
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
    checks.expect(model.banner?.contains("working directory") == true, "a run without a working directory is refused by the app, with a reason")
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
        model.reasoningEffort = .none
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
    }

    model.stop()
    checks.expect(!model.connected, "stopping closes the connection")
}

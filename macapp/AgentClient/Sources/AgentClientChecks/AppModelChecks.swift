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

@MainActor
func appChecks(_ checks: Checks) async {
    print("app model")
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
        model.send("List the files in this directory with a tool, then tell me how many there are.")
        let finished = await settled(model, timeout: 240)
        checks.expect(finished, "a real run finishes")
        checks.expect((model.transcript.finalText ?? "").isEmpty == false, "the app shows the model's answer")
        checks.expect(!model.transcript.toolRows.isEmpty, "the app shows the tool the model used")
        checks.expect(model.sessionId != nil, "the session id came back from run.start")
        checks.note("app run: \(model.transcript.lines.count) lines, session \(model.sessionId ?? "?"), \(model.transcript.state.label)")

        // The answer to a prompt the app never asked for is a bug the sheet
        // cannot recover from; the transcript is the only source of truth.
        checks.expect(model.promptToAnswer == nil, "nothing is left waiting for an answer")
    }

    model.stop()
    checks.expect(!model.connected, "stopping closes the connection")
}

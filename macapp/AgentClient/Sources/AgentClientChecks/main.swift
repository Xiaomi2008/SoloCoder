import AgentAppCore
import AgentClient
import Foundation

// The contract suite that runs without Xcode.
//
// `swift test` needs XCTest/Swift Testing, and those libraries ship inside
// Xcode.app — a Command Line Tools install has the compiler but not the test
// libraries. This executable runs the same checks with a small harness, so
// `swift run agent-checks` is a gate on any machine that can compile Swift.
//
// Three groups: the fixtures the Python side generates decode into the Swift
// types; the framing rules hold at byte boundaries; and, when
// `.venv/bin/python` exists, the real service answers the real protocol.

final class Checks {
    private var passed = 0
    private var failures: [String] = []

    func expect(_ condition: Bool, _ what: String, line: Int = #line) {
        if condition {
            passed += 1
        } else {
            failures.append("line \(line): \(what)")
        }
    }

    func equal<T: Equatable>(_ lhs: T, _ rhs: T, _ what: String, line: Int = #line) {
        if lhs == rhs {
            passed += 1
        } else {
            failures.append("line \(line): \(what)\n      got:  \(lhs)\n      want: \(rhs)")
        }
    }

    func note(_ message: String) {
        print("  · \(message)")
    }

    func report() -> Int32 {
        if failures.isEmpty {
            print("\n\(passed) checks passed")
            return 0
        }
        print("\n\(failures.count) of \(failures.count + passed) checks FAILED")
        for failure in failures { print("  ✗ \(failure)") }
        return 1
    }
}

struct MissingFixture: Error, CustomStringConvertible {
    let type: String
    var description: String { "no \(type) fixture; run scripts/gen_protocol_fixtures.py" }
}

func fixture(_ events: [ContractFixtures.EventEntry], _ type: String) throws -> ContractFixtures.EventEntry {
    guard let entry = events.first(where: { $0.type == type }) else { throw MissingFixture(type: type) }
    return entry
}

/// True when the call fails with exactly this service error code. The refusals
/// are the contract, so "it threw something" is not a passing assertion.
func refused(_ code: ErrorCode, _ call: () async throws -> Void) async -> Bool {
    do {
        try await call()
        return false
    } catch let error as ServiceError {
        return error.code == code
    } catch {
        return false
    }
}

func refusedSync(_ code: ErrorCode, _ call: () throws -> Void) -> Bool {
    do {
        try call()
        return false
    } catch let error as ServiceError {
        return error.code == code
    } catch {
        return false
    }
}

// MARK: - Fixtures

func fixtureChecks(_ checks: Checks) throws {
    let events = try ContractFixtures.events()
    checks.expect(!events.isEmpty, "the fixtures are empty; run scripts/gen_protocol_fixtures.py")

    var decodedTypes: Set<String> = []
    for (index, entry) in events.enumerated() {
        let event = try AgentEvent.decode(from: ContractFixtures.paramsData(entry))
        decodedTypes.insert(event.type)
        checks.equal(event.type, entry.type, "fixture \(index) type")
        checks.equal(event.runId, "run_sample", "fixture \(index) (\(entry.type)) run_id")
        checks.equal(
            event.isTerminal,
            Wire.terminalEventTypes.contains(entry.type),
            "fixture \(index) (\(entry.type)) isTerminal"
        )
    }
    checks.equal(
        Wire.eventTypes.subtracting(decodedTypes),
        Set<String>(),
        "every Swift event type needs a fixture"
    )

    if case .messageDelta(let payload) =
        try AgentEvent.decode(from: ContractFixtures.paramsData(fixture(events, "message_delta"))) {
        checks.equal(payload.delta, "Writing — 中文 ✓\n", "a unicode delta survives the wire")
    }

    if case .toolCallStarted(let call) =
        try AgentEvent.decode(from: ContractFixtures.paramsData(fixture(events, "tool_call_started"))),
        let arguments = call.arguments.objectValue {
        checks.equal(arguments["command"]?.stringValue, "ls -la", "nested tool argument (command)")
        checks.equal(
            arguments["nested"]?.objectValue?["b"]?.arrayValue?.count,
            2,
            "nested tool argument (nested.b)"
        )
    }

    if case .permissionResolved(let answer) =
        try AgentEvent.decode(from: ContractFixtures.paramsData(fixture(events, "permission_resolved"))) {
        checks.equal(answer.scope, PermissionScope.session, "permission scope decodes")
        checks.expect(!answer.reason.isEmpty, "permission_resolved carries a reason")
    }

    // Forward compatibility: a newer server must not crash an older client.
    let future = try AgentEvent.decode(from: Data(#"{"type":"quantum_leap","run_id":"r","why":"because"}"#.utf8))
    checks.equal(future.type, "quantum_leap", "an unknown event type is preserved")
    checks.expect(!future.isTerminal, "an unknown event type is not terminal")

    let futureScope = try AgentEvent.decode(
        from: Data(#"{"type":"permission_resolved","run_id":"r","request_id":"q","granted":true,"scope":"decade","reason":"x"}"#.utf8)
    )
    if case .permissionResolved(let answer) = futureScope {
        checks.equal(answer.scope, PermissionScope.other("decade"), "an unknown enum value is preserved")
    }
}

func manifestChecks(_ checks: Checks) throws {
    print("manifest")
    let manifest = try ContractFixtures.manifest()
    checks.equal(manifest.protocolVersion, Wire.protocolVersion, "protocol version")
    checks.equal(manifest.maxFrameBytes, Wire.maxFrameBytes, "max frame bytes")
    checks.equal(Set(manifest.methods), Wire.methods, "method list")
    checks.equal(Set(manifest.terminalEventTypes), Wire.terminalEventTypes, "terminal event types")
    checks.equal(Set(manifest.events.map(\.type)), Wire.eventTypes, "event list")
    checks.equal(
        Set(manifest.errorCodes.values),
        Set(ErrorCode.known.map(\.rawCode)),
        "error codes the server defines vs the codes this client names"
    )
    checks.equal(ErrorCode(code: -33999).rawCode, -33999, "an unknown error code keeps its number")
}

// MARK: - Framing

func framingChecks(_ checks: Checks) throws {
    print("framing")
    var decoder = FrameDecoder()
    let frames = try decoder.append(Data("{\"a\":1}\n{\"b\":2}\n".utf8))
    checks.equal(frames.count, 2, "two frames in one chunk")

    // The newline is the only split point and it is one byte, so a chunk can
    // end inside 中 (three bytes). Decoding per chunk would corrupt it.
    var bytewise = FrameDecoder()
    var collected: [Data] = []
    for byte in Data("{\"delta\":\"中 ✓\"}\n".utf8) {
        collected.append(contentsOf: try bytewise.append(Data([byte])))
    }
    checks.equal(collected.count, 1, "a frame split at every byte boundary reassembles")
    if case .event(.messageDelta(let delta)) = try Frame.decode(collected[0]) {
        checks.equal(delta.delta, "中 ✓", "multi-byte content survives byte splits")
    }

    var small = FrameDecoder(maxBytes: 16)
    let oversizedRejected = try { () -> Bool in
        do {
            _ = try small.append(Data("{\"aaaaaaaaaaaaaaaaaaaa\":1}\n".utf8))
            return false
        } catch is ClientError {
            return true
        }
    }()
    checks.expect(oversizedRejected, "an oversized frame refuses the connection")

    var partial = FrameDecoder()
    _ = try partial.append(Data("{\"a\":1".utf8))
    checks.expect(partial.takeTrailingPartial() != nil, "a partial frame at close is reported")

    let request = try Frame.encode(RpcRequest(id: "c1", method: "ping", params: nil).wireObject())
    let object = try JSONSerialization.jsonObject(with: request) as? [String: Any]
    checks.expect(object?["params"] == nil, "JSON-RPC wants params absent, not null")

    // NSNumber bridges to Bool for 1 and 0. A JSON 1 that decodes as `true`
    // turns a protocol version into a flag, so the distinction is a check.
    let mixed = try Frame.decode(Data("{\"jsonrpc\":\"2.0\",\"id\":\"c1\",\"result\":{\"pong\":true,\"n\":1,\"pi\":1.5}}".utf8))
    if case .response(_, let result, _) = mixed, let fields = result?.objectValue {
        checks.equal(fields["pong"]?.boolValue, true, "a JSON true is a bool")
        checks.equal(fields["n"], .int(1), "a JSON 1 is an int, not true")
        checks.equal(fields["pi"], .double(1.5), "a JSON 1.5 is a double")
    }

    let failure = try Frame.decode(
        Data("""
        {"jsonrpc":"2.0","id":"c2","error":{"code":-33005,"message":"nope","data":{"roots":["/x"]}}}
        """.utf8)
    )
    if case .response(_, _, let error) = failure {
        checks.equal(error?.code, .workingDirDenied, "an error frame maps to working_dir_denied")
        checks.equal(
            error?.data?.objectValue?["roots"]?.arrayValue?.first?.stringValue,
            "/x",
            "an error frame keeps its structured data"
        )
    }
}

// MARK: - Live service

func liveChecks(_ checks: Checks) async -> Bool {
    print("live service")
    guard FileManager.default.fileExists(atPath: ContractFixtures.venvPython.path) else {
        checks.note("skipped: no .venv/bin/python at \(ContractFixtures.venvPython.path)")
        return true
    }

    let sandbox = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("oa\(UUID().uuidString.prefix(6))")
    let liveModel = ProcessInfo.processInfo.environment["AGENT_LIVE_MODEL"]

    // HOME points at a sandbox: every run saves a session, and a suite that
    // writes into the developer's ~/.openagent is a suite that gets turned off.
    //
    // AGENT_LIVE_MODEL opts out, on purpose. The model servers a real run needs
    // are the developer's — their config and their Keychain keys — so a
    // sandboxed HOME has nothing to route to. Setting that variable is the
    // developer saying "yes, use my config, and write a session".
    let home = liveModel.map { _ in FileManager.default.homeDirectoryForCurrentUser }
        ?? sandbox.appendingPathComponent("home")
    let socketPath = sandbox.appendingPathComponent("s.sock").path
    let child = Process()
    let client = AgentServiceClient(endpoint: ServiceEndpoint(socketPath: socketPath, clientName: "agent-checks"))

    let ok: Bool
    do {
        // HOME points at a sandbox: every run saves a session, and a suite that
        // writes into the developer's ~/.openagent is a suite that gets turned off.
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        child.executableURL = ContractFixtures.venvPython
        child.arguments = [ContractFixtures.cliEntry.path, "serve", "--socket", socketPath, "--quiet"]
        child.currentDirectoryURL = ContractFixtures.repositoryRoot
        var env = ProcessInfo.processInfo.environment
        if liveModel == nil {
            env["HOME"] = home.path
            env["XDG_CONFIG_HOME"] = home.appendingPathComponent(".config").path
        }
        child.environment = env
        child.standardError = FileHandle.standardError
        child.standardOutput = FileHandle.standardError
        try child.run()

        try await liveSession(
            checks,
            client: client,
            socketPath: socketPath,
            sandbox: sandbox,
            child: child,
            liveModel: liveModel
        )
        ok = true
    } catch {
        checks.expect(false, "the live service checks aborted: \(error)")
        ok = false
    }

    await client.close()
    if child.isRunning {
        child.terminate()
        child.waitUntilExit()
    }
    try? FileManager.default.removeItem(at: sandbox)
    return ok
}

func liveSession(
    _ checks: Checks,
    client: AgentServiceClient,
    socketPath: String,
    sandbox: URL,
    child: Process,
    liveModel: String?
) async throws {
    // The socket file appearing is the server being ready to accept; the banner
    // is suppressed with --quiet, so there is nothing to read.
    let deadline = Date().addingTimeInterval(25)
    while !FileManager.default.fileExists(atPath: socketPath) {
        if !child.isRunning { throw ClientError.connected("the service exited before it bound \(socketPath)") }
        if Date() > deadline { throw ClientError.timedOut("the service to bind \(socketPath)") }
        try await Task.sleep(nanoseconds: 50_000_000)
    }

    let info = try await client.connect()
    checks.equal(info.protocolVersion, Wire.protocolVersion, "handshake protocol version")
    checks.equal(info.server, "openagent", "handshake server name")
    checks.equal(Set(info.methods), Wire.methods, "the server's methods match this client's")
    checks.equal(Set(info.events), Wire.eventTypes, "the server's events match this client's")
    checks.equal(
        Set(info.terminalEventTypes ?? []),
        Wire.terminalEventTypes,
        "the server declares which events end a stream"
    )
    checks.expect(
        await client.terminalEventTypes.isSuperset(of: Wire.terminalEventTypes),
        "the client ends streams on the union of both sets"
    )
    checks.expect(try await client.ping(), "ping")

    let tools = try await client.tools()
    checks.expect(!tools.isEmpty, "tool.list returns rows")
    checks.expect(
        tools.allSatisfy { !$0.risk.isEmpty && !$0.reason.isEmpty },
        "every tool names its risk and its reason"
    )

    let models = try await client.models()
    checks.expect(!models.policy.isEmpty, "model.list reports the routing policy")
    checks.expect(models.models.allSatisfy { $0.key.contains("/") }, "model rows are provider/model keys")

    let status = try await client.authStatus()
    checks.expect(!status.providers.isEmpty, "auth.status reports providers")

    _ = try await client.sessions()
    let config = try await client.config(keys: ["models"])
    checks.equal(
        config.objectValue?["models"]?.objectValue?["policy"]?.stringValue,
        "local_first",
        "config.get(keys:) narrows to the sections asked for"
    )

    for bad in ["relative/dir", "/", "/definitely/not/here"] {
        checks.expect(
            await refused(.workingDirDenied) {
                _ = try await client.startRun(prompt: "go", workingDir: bad)
            },
            "working_dir \(bad) is refused before the model is touched"
        )
    }

    let work = sandbox.appendingPathComponent("work")
    try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
    checks.expect(
        await refused(.modelUnavailable) {
            _ = try await client.startRun(prompt: "go", workingDir: work.path, model: "nosuchprovider/nosuchmodel")
        },
        "an unknown model is refused as a response, not an event"
    )

    checks.expect(
        refusedSync(.protocolMismatch) { try RawProbe(path: socketPath).hello(version: 999) },
        "a protocol version the server cannot serve is reported"
    )
    checks.expect(
        refusedSync(.invalidRequest) { try RawProbe(path: socketPath).pingFirst() },
        "a call before hello is rejected"
    )
    checks.expect(
        refusedSync(.methodNotFound) { try RawProbe(path: socketPath).call(afterHello: "no.such.method") },
        "an unknown method is method-not-found"
    )

    // A real streamed run, when the developer points the suite at a model.
    guard let model = liveModel, !model.isEmpty else { return }
    let stream = await client.events()
    let started = try await client.startRun(
        prompt: "List the files here with a tool, then say how many there are.",
        workingDir: work.path,
        model: model,
        permissionMode: .allowReadonly
    )
    checks.equal(started.model, model, "run.start reports the model it used")

    var transcript = Transcript(terminalTypes: await client.terminalEventTypes)
    transcript.addUserMessage("List the files here with a tool, then say how many there are.")
    var types: [String] = []
    var sawDelta = false
    var sawTool = false
    var streamingBlocks = 0
    for await event in stream {
        types.append(event.type)
        transcript.apply(event)
        if case .messageDelta = event {
            sawDelta = true
            // The answer is markdown *while it streams*, not only after it ends:
            // the blocks the reducer parsed mid-run are the proof, on real traffic
            // from a real model.
            if let answer = transcript.lines.last(where: { if case .assistant = $0.kind { return true } else { return false } }) {
                streamingBlocks = max(streamingBlocks, answer.blocks.count)
            }
        }
        if case .toolCallCompleted = event { sawTool = true }
        if event.isTerminal { break }
    }
    checks.note("stream: \(types.joined(separator: " → "))")
    checks.equal(types.first, "run_started", "the stream starts with run_started")
    checks.expect(sawDelta, "a streamed run produces deltas")
    checks.expect(sawTool, "the model used a tool")
    checks.expect(
        types.last.map { Wire.terminalEventTypes.contains($0) } ?? false,
        "the stream ends on a terminal event"
    )
    checks.expect(
        streamingBlocks > 0,
        "the answer is markdown while it streams: \(streamingBlocks) blocks parsed before the run ended"
    )

    // The reducer, fed a real stream rather than a scripted one: this is what
    // the app renders, and a protocol field the UI needs shows up here as a
    // missing line, not as a compile error.
    checks.expect((transcript.finalText ?? "").isEmpty == false, "the timeline holds the model's answer")
    checks.expect(!transcript.toolRows.isEmpty, "the timeline shows the tool the model used")
    checks.expect(
        transcript.toolRows.allSatisfy({ if case .running = $0.state { return false } else { return true } }),
        "no tool row is left running after the run ends"
    )
    checks.expect(!transcript.state.isRunning, "the timeline closes the run")
    checks.expect(transcript.lines.count >= 3, "the timeline has the prompt, the answer and the tool")
    checks.note("timeline: \(transcript.lines.count) lines, state \(transcript.state.label)")
}


/// A raw socket conversation, for the cases the typed client cannot express:
/// the wrong protocol version, a call before `hello`, an unknown method.
///
/// One socket per probe, deliberately: a fresh connection per call would make
/// "a call after hello" a "call before hello" and pass for the wrong reason.
final class RawProbe {
    private let socket: UnixSocketTransport
    private var decoder = FrameDecoder()

    init(path: String) throws {
        socket = try UnixSocketTransport(path: path)
    }

    func close() {
        socket.close()
    }

    @discardableResult
    func hello(version: Int) throws -> [String: Any] {
        try call(id: "r1", method: "hello", params: [
            "protocol_version": version, "client": "raw-probe", "client_version": "0",
        ])
    }

    @discardableResult
    func pingFirst() throws -> [String: Any] {
        try call(id: "r1", method: "ping", params: nil)
    }

    @discardableResult
    func call(afterHello method: String) throws -> [String: Any] {
        _ = try hello(version: Wire.protocolVersion)
        return try call(id: "r2", method: method, params: nil)
    }

    @discardableResult
    func call(id: String, method: String, params: [String: Any]?) throws -> [String: Any] {
        var object: [String: Any] = ["jsonrpc": "2.0", "id": id, "method": method]
        if let params { object["params"] = params }
        try socket.write(Frame.encode(object))

        while let chunk = socket.readChunk() {
            for frame in try decoder.append(chunk) {
                guard let decoded = try JSONSerialization.jsonObject(with: frame) as? [String: Any] else { continue }
                guard (decoded["id"] as? String) == id else { continue }
                if let error = decoded["error"] as? [String: Any] {
                    throw ServiceError(
                        code: ErrorCode(code: (error["code"] as? Int) ?? 0),
                        message: (error["message"] as? String) ?? "",
                        data: JSONValue.fromJSON(error["data"])
                    )
                }
                return (decoded["result"] as? [String: Any]) ?? [:]
            }
        }
        throw ClientError.closed
    }
}

// MARK: - Main

let checks = Checks()
var status: Int32 = 0
var stage = "startup"
do {
    stage = "fixtures"
    try fixtureChecks(checks)
    stage = "manifest"
    try manifestChecks(checks)
    stage = "framing"
    try framingChecks(checks)
    stage = "transcript"
    try transcriptChecks(checks)
    stage = "markdown"
    markdownChecks(checks)
    stage = "tool display"
    toolDisplayChecks(checks)
    stage = "rows"
    try rowChecks(checks)
    stage = "composer"
    composerChecks(checks)
    stage = "live"
    _ = await liveChecks(checks)
    stage = "app"
    await appChecks(checks)
    status = checks.report()
} catch {
    print("check suite aborted in \(stage): \(error)")
    status = checks.report()
}
exit(status)

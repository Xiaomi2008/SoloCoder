import Testing
import Foundation
@testable import AgentClient

/// The same contract `tests/service/test_server.py` executes, run from Swift
/// against the real Python server on a real socket.
///
/// This is the half that makes the two suites one suite: the Python tests prove
/// the server keeps its promises, and these prove the Swift types can speak to
/// it. A protocol change that updates only one side fails here.
// The trait is the gate; the words below it are why it is gated. Swift
// Testing's `Suite`/`Test` macros take a display name and traits, not a
// trailing explanation, so the explanation is a comment and not an argument.
@Suite(
    "live service contract",
    .enabled(if: FileManager.default.fileExists(atPath: ContractFixtures.venvPython.path))
)
struct LiveServiceContractTests {
    @Test("handshake reports the contract")
    func handshake() async throws {
        try await withService { service in
            let info = try #require(await service.client.handshake)
            #expect(info.protocolVersion == Wire.protocolVersion)
            #expect(info.server == "openagent")
            #expect(info.methods.contains("run.start"))
            #expect(info.events.contains("message_delta"))
            #expect(
                Set(info.methods) == Wire.methods,
                "the server serves methods this client does not know"
            )
            #expect(
                Set(info.events) == Wire.eventTypes,
                "the server emits events this client does not know"
            )
        }
    }

    @Test("ping")
    func ping() async throws {
        try await withService { service in
            let pong = try await service.client.ping()
            #expect(pong)
        }
    }

    @Test("model list decodes")
    func modelList() async throws {
        try await withService { service in
            let list = try await service.client.models()
            #expect(!list.policy.isEmpty)
            for row in list.models {
                #expect(row.key.contains("/"), "model rows are provider/model keys: \(row.key)")
            }
        }
    }

    @Test("tool list decodes")
    func toolList() async throws {
        try await withService { service in
            let tools = try await service.client.tools()
            #expect(!tools.isEmpty, "the service exposes no tools")
            for row in tools {
                #expect(!row.risk.isEmpty, "\(row.name) has no risk classification")
                #expect(!row.reason.isEmpty, "\(row.name) has no reason a user can read")
            }
        }
    }

    @Test("auth status decodes")
    func authStatus() async throws {
        try await withService { service in
            let status = try await service.client.authStatus()
            #expect(!status.providers.isEmpty)
        }
    }

    @Test("sessions list and a filtered config read")
    func sessionsAndConfig() async throws {
        try await withService { service in
            _ = try await service.client.sessions()
            // `config.get(keys:)` filters *top-level* keys, and the policy is
            // nested under `models` — the shape the service actually writes.
            let config = try await service.client.config(keys: ["models"])
            #expect(
                config.objectValue?["models"]?.objectValue?["policy"] != nil,
                "config.get(keys:) returns the section asked for"
            )
        }
    }

    @Test("an unknown method is method-not-found")
    func unknownMethod() async throws {
        try await withService { service in
            await #expect(throws: ServiceError.self) {
                _ = try await RawProbe(socketPath: service.socketPath).call(afterHello: "no.such.method")
            }
        }
    }

    @Test("a protocol version the server cannot serve is reported")
    func protocolMismatch() async throws {
        try await withService { service in
            do {
                _ = try await RawProbe(socketPath: service.socketPath).hello(version: 999)
                Issue.record("expected protocol_mismatch")
            } catch let error as ServiceError {
                #expect(error.code == .protocolMismatch)
            }
        }
    }

    @Test("a call before hello is rejected")
    func callBeforeHello() async throws {
        try await withService { service in
            do {
                _ = try await RawProbe(socketPath: service.socketPath).pingFirst()
                Issue.record("expected invalid_request")
            } catch let error as ServiceError {
                #expect(error.code == .invalidRequest)
            }
        }
    }

    @Test("working_dir is validated before the model is touched")
    func workingDirRefusals() async throws {
        try await withService { service in
            for bad in ["relative/dir", "/", "/definitely/not/here"] {
                do {
                    _ = try await service.client.startRun(prompt: "go", workingDir: bad)
                    Issue.record("expected \(bad) to be refused")
                } catch let error as ServiceError {
                    #expect(error.code == .workingDirDenied, "\(bad) was not refused as a working dir")
                }
            }
        }
    }

    @Test("an unknown model is refused as a response, not an event")
    func unknownModel() async throws {
        try await withService { service in
            let dir = service.sandbox.appendingPathComponent("work")
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            do {
                _ = try await service.client.startRun(
                    prompt: "go",
                    workingDir: dir.path,
                    model: "nosuchprovider/nosuchmodel"
                )
                Issue.record("expected model_unavailable")
            } catch let error as ServiceError {
                #expect(error.code == .modelUnavailable)
                #expect(error.data?.objectValue?["model"]?.stringValue == "nosuchprovider/nosuchmodel")
            }
        }
    }

    @Test("answering a permission that was never asked is reported")
    func stalePermissionAnswer() async throws {
        try await withService { service in
            do {
                try await service.client.respondPermission(requestId: "req_nope", granted: true)
                Issue.record("expected an error for a request that was never asked")
            } catch let error as ServiceError {
                #expect(
                    [ErrorCode.invalidRequest, .invalidParams, .runNotFound].contains(error.code),
                    "unexpected code \(error.code)"
                )
            }
        }
    }

    /// Set `AGENT_LIVE_MODEL=provider/model` to stream a real run through the
    /// socket. Off by default: a contract suite that needs a model is a suite
    /// that cannot run in CI.
    @Test(
        "a real run streams and terminates",
        .enabled(if: LiveModel.key != nil)
    )
    func realRun() async throws {
        let model = try #require(LiveModel.key)
        try await withService { service in
            let dir = service.sandbox.appendingPathComponent("work")
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try "alpha\n".write(to: dir.appendingPathComponent("a.txt"), atomically: true, encoding: .utf8)

            let stream = await service.client.events()
            let started = try await service.client.startRun(
                prompt: "List the files here with a tool, then say how many there are.",
                workingDir: dir.path,
                model: model,
                permissionMode: .allowReadonly
            )
            #expect(started.model == model)

            var events: [AgentEvent] = []
            var sawDelta = false
            var sawTool = false
            for await event in stream {
                events.append(event)
                if case .messageDelta = event { sawDelta = true }
                if case .toolCallCompleted = event { sawTool = true }
                if event.isTerminal { break }
            }

            #expect(events.first?.type == "run_started", "the stream must start with run_started")
            #expect(sawDelta, "a streamed run produced no deltas")
            #expect(sawTool, "the model was asked to use a tool and did not")
            #expect(events.last?.isTerminal ?? false, "the stream ended without a terminal event")
        }
    }
}

// MARK: - Fixture

enum LiveModel {
    static var key: String? {
        let value = ProcessInfo.processInfo.environment["AGENT_LIVE_MODEL"]
        return (value?.isEmpty == false) ? value : nil
    }
}

/// One server per test, on its own socket in its own sandbox.
///
/// The sandbox points `HOME` at a temp directory: every run saves a session,
/// and a test suite that writes into the developer's `~/.openagent` is a suite
/// that gets turned off.
final class ServiceFixture {
    let sandbox: URL
    let socketPath: String
    let client: AgentServiceClient
    private var process: Process?
    /// The service watches its stdin for parent death, so the test has to hand
    /// it a pipe that stays open. Inheriting the test's own stdin, which is
    /// closed under `swift test`, tells the service its parent is gone at once
    /// and it exits before the socket is bound.
    private var stdinPipe: Pipe?

    init() async throws {
        let base = NSTemporaryDirectory()
        sandbox = URL(fileURLWithPath: base).appendingPathComponent("oa\(UUID().uuidString.prefix(6))")
        let home = sandbox.appendingPathComponent("home")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        socketPath = sandbox.appendingPathComponent("s.sock").path

        let child = Process()
        child.executableURL = ContractFixtures.venvPython
        child.arguments = [ContractFixtures.cliEntry.path, "serve", "--socket", socketPath, "--quiet"]
        child.currentDirectoryURL = ContractFixtures.repositoryRoot
        var env = ProcessInfo.processInfo.environment
        env["HOME"] = home.path
        env["XDG_CONFIG_HOME"] = home.appendingPathComponent(".config").path
        child.environment = env
        child.standardError = FileHandle.standardError
        child.standardOutput = FileHandle.standardError
        let pipe = Pipe()
        stdinPipe = pipe
        child.standardInput = pipe
        try child.run()
        process = child

        // The socket file appearing is the server being ready to accept; the
        // banner is suppressed with --quiet, so there is nothing to read.
        let deadline = Date().addingTimeInterval(25)
        while !FileManager.default.fileExists(atPath: socketPath) {
            if !child.isRunning {
                Issue.record("the service exited before it bound \(socketPath)")
                break
            }
            if Date() > deadline {
                Issue.record("the service did not bind \(socketPath) in 25s")
                break
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }

        client = AgentServiceClient(
            endpoint: ServiceEndpoint(socketPath: socketPath, clientName: "swift-contract-tests")
        )
        _ = try await client.connect()
    }

    func shutdown() async {
        await client.close()
        if let process, process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
        stdinPipe?.fileHandleForWriting.closeFile()
        stdinPipe = nil
        try? FileManager.default.removeItem(at: sandbox)
    }
}

func withService(_ body: (ServiceFixture) async throws -> Void) async throws {
    let fixture = try await ServiceFixture()
    do {
        try await body(fixture)
    } catch {
        await fixture.shutdown()
        throw error
    }
    await fixture.shutdown()
}

/// A raw socket conversation, for the cases the typed client cannot express:
/// the wrong protocol version, a call before `hello`, and an unknown method.
struct RawProbe {
    private let socketPath: String

    init(socketPath: String) { self.socketPath = socketPath }

    func hello(version: Int) async throws -> [String: Any] {
        let socket = try UnixSocketTransport(path: socketPath)
        defer { socket.close() }
        return try await exchange(socket: socket, id: "r1", method: "hello", params: [
            "protocol_version": version, "client": "raw-probe", "client_version": "0",
        ])
    }

    func call(afterHello method: String) async throws -> [String: Any] {
        let socket = try UnixSocketTransport(path: socketPath)
        defer { socket.close() }
        _ = try await exchange(socket: socket, id: "r1", method: "hello", params: [
            "protocol_version": Wire.protocolVersion, "client": "raw-probe", "client_version": "0",
        ])
        return try await exchange(socket: socket, id: "r2", method: method, params: nil)
    }

    func pingFirst() async throws -> [String: Any] {
        let socket = try UnixSocketTransport(path: socketPath)
        defer { socket.close() }
        return try await exchange(socket: socket, id: "r1", method: "ping", params: nil)
    }

    private func exchange(
        socket: UnixSocketTransport,
        id: String,
        method: String,
        params: [String: Any]?
    ) throws -> [String: Any] {
        var object: [String: Any] = ["jsonrpc": "2.0", "id": id, "method": method]
        if let params { object["params"] = params }
        try socket.write(try Frame.encode(object))
        return try read(socket: socket, id: id)
    }

    private func read(socket: UnixSocketTransport, id: String) throws -> [String: Any] {
        var decoder = FrameDecoder()
        while let chunk = socket.readChunk() {
            for frame in try decoder.append(chunk) {
                guard let object = try JSONSerialization.jsonObject(with: frame) as? [String: Any] else { continue }
                guard (object["id"] as? String) == id else { continue }
                if let error = object["error"] as? [String: Any] {
                    throw ServiceError(
                        code: ErrorCode(code: (error["code"] as? Int) ?? 0),
                        message: (error["message"] as? String) ?? "",
                        data: JSONValue.fromJSON(error["data"])
                    )
                }
                return (object["result"] as? [String: Any]) ?? [:]
            }
        }
        throw ClientError.closed
    }
}

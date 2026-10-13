import Foundation

// MARK: - Transport

/// A strerror that is a String, so the socket code does not sprinkle
/// `String(cString:)` everywhere and get the optional-chaining wrong once.
enum SystemMessage {
    static func forErrno(_ code: Int32) -> String {
        String(cString: strerror(code))
    }
}

/// A connected `AF_UNIX` `SOCK_STREAM`.
///
/// Deliberately small: `connect`, blocking `read`, locked `write`, `close`. The
/// ordering rules that matter live in the client, and a socket that buffers
/// reads inside itself is how a client ends up with two framers.
public final class UnixSocketTransport: @unchecked Sendable {
    private let fd: Int32
    private let writeLock = NSLock()
    private var closedFlag = false

    public init(path: String) throws {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw ClientError.connected(SystemMessage.forErrno(errno)) }

        // sockaddr_un.sun_path is 104 bytes on macOS, and the limit is enforced
        // at connect() with a message that names neither the path nor the cap.
        // Check it here, where the answer is "use a shorter socket path".
        let bytes = Array(path.utf8)
        guard bytes.count < 104 else {
            Foundation.close(fd)
            throw ClientError.connected(
                "socket path is \(bytes.count) bytes; macOS caps AF_UNIX at 104 — use a shorter --socket"
            )
        }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutablePointer(to: &address.sun_path) { pathPointer in
            pathPointer.withMemoryRebound(to: UInt8.self, capacity: 104) { raw in
                for (offset, byte) in bytes.enumerated() { raw[offset] = byte }
                raw[bytes.count] = 0
            }
        }
        let length = socklen_t(MemoryLayout<sockaddr_un>.size)
        let result = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, length)
            }
        }
        guard result == 0 else {
            let why = SystemMessage.forErrno(errno)
            Foundation.close(fd)
            throw ClientError.connected("\(why) (\(path))")
        }
        self.fd = fd
    }

    /// One blocking read. `nil` means end of stream: the peer closed, or we did.
    public func readChunk() -> Data? {
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        let count = recv(fd, &buffer, buffer.count, 0)
        if count > 0 { return Data(buffer[0..<count]) }
        if count < 0, errno == EINTR { return Data() }  // not an end, just an interruption
        return nil
    }

    public func write(_ data: Data) throws {
        writeLock.lock()
        defer { writeLock.unlock() }
        guard !closedFlag else { throw ClientError.closed }
        try data.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let written = send(fd, raw.baseAddress!.advanced(by: sent), raw.count - sent, 0)
                if written > 0 { sent += written; continue }
                if written < 0, errno == EINTR { continue }
                throw ClientError.closed
            }
        }
    }

    public func close() {
        writeLock.lock()
        defer { writeLock.unlock() }
        guard !closedFlag else { return }
        closedFlag = true
        // SHUT_RDWR first: a bare close() of a socket with a reader blocked in
        // recv() leaves that thread parked until the peer times out.
        shutdown(fd, SHUT_RDWR)
        Foundation.close(fd)
    }
}

// MARK: - Endpoint

public struct ServiceEndpoint: Sendable {
    public var socketPath: String
    public var clientName: String
    public var clientVersion: String

    public init(
        socketPath: String = (NSHomeDirectory() as NSString).appendingPathComponent(".openagent/agent.sock"),
        clientName: String = "solocoder-app",
        clientVersion: String = "0.1.0"
    ) {
        self.socketPath = socketPath
        self.clientName = clientName
        self.clientVersion = clientVersion
    }
}

// MARK: - Results

public struct Handshake: Decodable, Sendable, Equatable {
    public let protocolVersion: Int
    public let minProtocolVersion: Int
    public let server: String
    public let serverVersion: String
    public let methods: [String]
    public let events: [String]
}

public struct RunStartResult: Decodable, Sendable, Equatable {
    public let runId: String
    public let sessionId: String
    public let model: String
}

public struct ModelCapabilities: Decodable, Sendable, Equatable {
    public let tools: Bool
    public let vision: Bool
    public let reasoning: Bool
    public let streaming: Bool
    public let jsonMode: Bool
    public let contextWindow: Int
    public let maxOutputTokens: Int?
    /// False means the capability is *claimed* by a server, not observed. Local
    /// servers routinely advertise tool support they do not deliver, so a picker
    /// shows a declared capability differently from a probed one.
    public let probed: Bool
}

public struct ModelCost: Decodable, Sendable, Equatable {
    public let kind: String
    public let inputPerMtok: Double?
    public let outputPerMtok: Double?
}

public struct ModelRow: Decodable, Sendable, Equatable {
    public let model: String
    public let provider: String
    public let wireApi: String
    public let authMode: String
    public let baseUrl: String?
    public let label: String
    public let family: String
    public let isLocal: Bool
    public let caps: ModelCapabilities
    public let cost: ModelCost

    /// The key `run.start(model:)` and `model.select` take.
    public var key: String { "\(provider)/\(model)" }
    public var display: String { label.isEmpty ? model : label }
}

public struct ModelList: Decodable, Sendable, Equatable {
    public let models: [ModelRow]
    public let policy: String
    public let defaultModel: String?
    /// What the router would pick for the main loop right now — not what the
    /// user chose, which is `defaultModel`.
    public let selected: String?
    public let discoveryErrors: [String: String]
}

public struct ToolRow: Decodable, Sendable, Equatable {
    public let name: String
    public let description: String
    public let risk: String
    public let requiresPermission: Bool
    public let reason: String
}

public struct ToolList: Decodable, Sendable, Equatable {
    public let tools: [ToolRow]
}

public struct SessionRow: Decodable, Sendable, Equatable {
    public let sessionId: String
    public let path: String
    /// Seconds since the epoch, straight from the file's mtime.
    public let updatedAt: Double
    public let messageCount: Int
    /// Set when the file exists but cannot be read. A damaged session is
    /// reported, not hidden.
    public let error: String?
}

public struct SessionList: Decodable, Sendable, Equatable {
    public let sessions: [SessionRow]
}

public struct SessionRecord: Decodable, Sendable, Equatable {
    public let sessionId: String
    public let systemPrompt: String
    public let messages: [JSONValue]
}

public struct AuthProviderRow: Decodable, Sendable, Equatable {
    public let provider: String
    public let label: String
    public let mode: String
    public let configured: Bool
    public let source: String
    public let account: String
    public let plan: String
    public let expiresAt: Double?
    public let needsRefresh: Bool
    public let unofficial: Bool
}

public struct AuthServerRow: Decodable, Sendable, Equatable {
    public let provider: String
    public let label: String
    public let baseUrl: String?
    public let keySet: Bool
    public let local: Bool
}

public struct AuthStatus: Decodable, Sendable, Equatable {
    public let providers: [AuthProviderRow]
    public let servers: [AuthServerRow]
}

public struct ModelProbe: Decodable, Sendable, Equatable {
    public let ok: Bool
    public let latencyMs: Int
    public let tools: Bool
    public let detail: String
}

/// The gate the server applies to this run's tools.
///
/// The raw values are the wire literals, spelled the server's way
/// (`allow_readonly`, underscore and all). `JSONEncoder` emits the raw value,
/// so a camelCase rawValue here is a run that never starts.
public enum PermissionMode: String, Sendable, CaseIterable {
    /// Prompt for every risky tool. The default, and the only mode where
    /// `permission_requested` arrives.
    case ask
    /// Read-only tools run; anything that writes is refused without asking.
    case allowReadonly = "allow_readonly"
    /// Everything runs. For a demo, not for a user's machine.
    case allowAll = "allow_all"
}

// MARK: - Client

/// The client half of `dev_docs/agent-service-protocol.md`.
///
/// Shape of a connection: one `hello`, then calls, and — for a run — one
/// `run.start` that occupies the connection while its events stream. That is
/// why this is a *connection* object and not a stateless request function: a
/// second `run.start` on a busy connection is refused, and `run.cancel` /
/// `permission.respond` are answered while a run is in flight.
public actor AgentServiceClient {
    public let endpoint: ServiceEndpoint

    private var transport: UnixSocketTransport?
    private var nextId = 0
    private var pending: [String: CheckedContinuation<JSONValue, any Error>] = [:]
    private let eventStream: AsyncStream<AgentEvent>
    private let eventContinuation: AsyncStream<AgentEvent>.Continuation
    private var failure: (any Error)?
    private var handshakeValue: Handshake?

    public init(endpoint: ServiceEndpoint = ServiceEndpoint()) {
        self.endpoint = endpoint
        // The stream is created up front, not on demand: events start arriving
        // the moment `run.start` is answered, and a client that subscribes after
        // that would silently miss `run_started` and every delta before it.
        let made = AsyncStream<AgentEvent>.makeStream(of: AgentEvent.self)
        self.eventStream = made.stream
        self.eventContinuation = made.continuation
    }

    // ------------------------------------------------------------------ lifecycle

    /// Connect and complete the handshake. Every other method requires it.
    @discardableResult
    public func connect() async throws -> Handshake {
        if transport != nil { return handshakeValue! }
        let socket = try UnixSocketTransport(path: endpoint.socketPath)
        transport = socket
        startReader(on: socket)

        let params: JSONValue = .object([
            "protocol_version": .int(Wire.protocolVersion),
            "client": .string(endpoint.clientName),
            "client_version": .string(endpoint.clientVersion),
        ])
        let result = try await call("hello", params: params)
        let info = try decode(Handshake.self, from: result)
        guard info.protocolVersion >= Wire.minProtocolVersion else {
            await close()
            throw ServiceError(
                code: .protocolMismatch,
                message: "server serves protocol \(info.protocolVersion); this client speaks \(Wire.protocolVersion)"
            )
        }
        handshakeValue = info
        return info
    }

    public func close() {
        for (_, continuation) in pending { continuation.resume(throwing: ClientError.closed) }
        pending.removeAll()
        eventContinuation.finish()
        transport?.close()
        transport = nil
    }

    public var handshake: Handshake? { handshakeValue }

    // ------------------------------------------------------------------ events

    /// The event stream for this connection. Single consumer, and it is live
    /// from `connect()` — subscribe before starting a run.
    public func events() -> AsyncStream<AgentEvent> { eventStream }

    // ------------------------------------------------------------------ calls

    public func ping() async throws -> Bool {
        let result = try await call("ping", params: nil)
        return result.objectValue?["pong"]?.boolValue ?? false
    }

    /// Start a run. The connection is then occupied by that run's events until
    /// a terminal one; a second `run.start` is refused by the server.
    @discardableResult
    public func startRun(
        prompt: String,
        workingDir: String,
        model: String? = nil,
        sessionId: String? = nil,
        maxTurns: Int? = nil,
        permissionMode: PermissionMode = .ask,
        images: [String] = []
    ) async throws -> RunStartResult {
        var params: [String: JSONValue] = [
            "prompt": .string(prompt),
            "working_dir": .string(workingDir),
            "permission_mode": .string(permissionMode.rawValue),
        ]
        if let model { params["model"] = .string(model) }
        if let sessionId { params["session_id"] = .string(sessionId) }
        if let maxTurns { params["max_turns"] = .int(maxTurns) }
        if !images.isEmpty { params["images"] = .array(images.map(JSONValue.string)) }

        let result = try await call("run.start", params: .object(params))
        return try decode(RunStartResult.self, from: result)
    }

    /// Ask the server to stop a run. The `run_cancelled` event follows on the
    /// stream; this call only confirms the request was accepted.
    public func cancelRun(runId: String) async throws {
        _ = try await call("run.cancel", params: .object(["run_id": .string(runId)]))
    }

    /// Answer a `permission_requested`. `scope` decides how long the answer
    /// lasts: `.once` this call, `.session` for the rest of the session,
    /// `.never` to stop asking about that tool by refusing it.
    public func respondPermission(
        requestId: String,
        granted: Bool,
        scope: PermissionScope = .once
    ) async throws {
        _ = try await call(
            "permission.respond",
            params: .object([
                "request_id": .string(requestId),
                "granted": .bool(granted),
                "scope": .string(scope.rawValue),
            ])
        )
    }

    public func models(discover: Bool = false) async throws -> ModelList {
        try decode(
            ModelList.self,
            from: try await call("model.list", params: .object(["discover": .bool(discover)]))
        )
    }

    public func probe(model: String) async throws -> ModelProbe {
        try decode(ModelProbe.self, from: try await call("model.probe", params: .object(["model": .string(model)])))
    }

    public func selectModel(_ key: String) async throws -> String? {
        let result = try await call("model.select", params: .object(["model": .string(key)]))
        return result.objectValue?["default_model"]?.stringValue
    }

    public func tools() async throws -> [ToolRow] {
        try decode(ToolList.self, from: try await call("tool.list", params: nil)).tools
    }

    public func sessions() async throws -> [SessionRow] {
        try decode(SessionList.self, from: try await call("session.list", params: nil)).sessions
    }

    public func loadSession(_ sessionId: String) async throws -> SessionRecord {
        try decode(
            SessionRecord.self,
            from: try await call("session.load", params: .object(["session_id": .string(sessionId)]))
        )
    }

    public func authStatus() async throws -> AuthStatus {
        try decode(AuthStatus.self, from: try await call("auth.status", params: nil))
    }

    /// Read the config. The server answers with the whole config object
    /// (`{"models": {...}, "search": {...}}`), narrowed to `keys` when given.
    public func config(keys: [String]? = nil) async throws -> JSONValue {
        var params: [String: JSONValue] = [:]
        if let keys { params["keys"] = .array(keys.map(JSONValue.string)) }
        let result = try await call("config.get", params: params.isEmpty ? nil : .object(params))
        return result.objectValue?["config"] ?? .object([:])
    }

    public func setConfig(_ values: [String: JSONValue]) async throws -> JSONValue {
        let result = try await call("config.set", params: .object(["values": .object(values)]))
        return result.objectValue?["config"] ?? .object([:])
    }

    // ------------------------------------------------------------------ plumbing

    private func call(_ method: String, params: JSONValue?, timeout: TimeInterval = 120) async throws -> JSONValue {
        if let failure { throw failure }
        guard transport != nil else { throw ClientError.notConnected }

        nextId += 1
        let requestId = "c\(nextId)"
        let frame = try Frame.encode(RpcRequest(id: requestId, method: method, params: params).wireObject())

        return try await withThrowingTaskGroup(of: JSONValue.self) { group in
            group.addTask { [weak self] in
                guard let self else { throw ClientError.closed }
                return try await self.sendAndAwait(requestId: requestId, frame: frame, method: method)
            }
            group.addTask { [weak self] in
                try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                await self?.abandon(requestId: requestId, method: method)
                throw ClientError.timedOut(method)
            }
            defer { group.cancelAll() }
            guard let value = try await group.next() else { throw ClientError.closed }
            return value
        }
    }

    private func sendAndAwait(
        requestId: String,
        frame: Data,
        method: String
    ) async throws -> JSONValue {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<JSONValue, any Error>) in
                pending[requestId] = continuation
                do {
                    guard let transport else { throw ClientError.notConnected }
                    try transport.write(frame)
                } catch {
                    pending[requestId] = nil
                    continuation.resume(throwing: error)
                }
            }
        } onCancel: {
            Task { await self.abandon(requestId: requestId, method: method) }
        }
    }

    /// Resume a caller that is no longer waiting, so the continuation is never
    /// leaked and a timed-out call cannot be answered into a dead task.
    private func abandon(requestId: String, method: String) {
        if let continuation = pending.removeValue(forKey: requestId) {
            continuation.resume(throwing: ClientError.timedOut(method))
        }
    }

    private func resolve(id: String, result: JSONValue?, error: ServiceError?) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        if let error {
            continuation.resume(throwing: error)
        } else {
            continuation.resume(returning: result ?? .null)
        }
    }

    private func connectionClosed() {
        for (_, continuation) in pending { continuation.resume(throwing: ClientError.closed) }
        pending.removeAll()
        eventContinuation.finish()
        transport?.close()
        transport = nil
    }

    private func decode<T: Decodable>(_ type: T.Type, from value: JSONValue) throws -> T {
        let data = try JSONEncoder().encode(value)
        do {
            return try wireDecoder.decode(type, from: data)
        } catch {
            throw ClientError.decoding("\(T.self): \(error)")
        }
    }

    /// Reads on its own thread and decodes there, in order.
    ///
    /// The ordering rule is the reason: events must reach the UI in the order
    /// the server sent them, so `yield` happens on this thread. Response
    /// resolution is order-insensitive (keyed by id), so it may hop to the
    /// actor. Handing the frames to `Task`s wholesale would let the two
    /// interleave and reorder the stream.
    private nonisolated func startReader(on socket: UnixSocketTransport) {
        Thread.detachNewThread {
            var decoder = FrameDecoder()
            while let chunk = socket.readChunk() {
                if chunk.isEmpty { continue }
                let frames: [Data]
                do {
                    frames = try decoder.append(chunk)
                } catch {
                    Task { await self.failWith(error) }
                    socket.close()
                    break
                }
                for frame in frames {
                    let message: RpcMessage
                    do {
                        message = try Frame.decode(frame)
                    } catch {
                        Task { await self.failWith(error) }
                        continue
                    }
                    switch message {
                    case .event(let event):
                        self.eventContinuation.yield(event)
                        if event.isTerminal { self.eventContinuation.finish() }
                    case .response(let id, let result, let error):
                        Task { await self.resolve(id: id, result: result, error: error) }
                    case .serverRequest(let method, let id):
                        // v1 defines no server-initiated requests. Answering with
                        // a method-not-found error is the protocol's own answer,
                        // and ignoring it would hang the server's caller.
                        Task { await self.rejectServerRequest(method: method, id: id) }
                    }
                }
            }
            if let tail = decoder.takeTrailingPartial(),
               String(data: tail, encoding: .utf8).map { !$0.trimmingCharacters(in: .whitespaces).isEmpty } == true {
                Task { await self.failWith(ClientError.framing("connection closed mid-frame")) }
            }
            Task { await self.connectionClosed() }
        }
    }

    private func failWith(_ error: any Error) {
        failure = error
        for (_, continuation) in pending { continuation.resume(throwing: error) }
        pending.removeAll()
        eventContinuation.finish()
    }

    private func rejectServerRequest(method: String, id: String) {
        guard let transport else { return }
        let frame = try? Frame.encode([
            "jsonrpc": "2.0",
            "id": id,
            "error": ["code": ErrorCode.methodNotFound.rawCode, "message": "the client answers no requests"],
        ])
        if let frame { try? transport.write(frame) }
    }
}

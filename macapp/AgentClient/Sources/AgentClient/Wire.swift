import Foundation

// MARK: - Constants

/// The numbers and names the Python service publishes. `tests/service/fixtures/protocol.json`
/// is generated from the Python types, and `ProtocolManifestTests` asserts these
/// lists match it — so adding a method or event on one side without the other
/// fails a test instead of shipping a client that cannot speak to its server.
public enum Wire {
    public static let protocolVersion = 1
    public static let minProtocolVersion = 1
    public static let maxFrameBytes = 8 * 1024 * 1024

    public static let methods: Set<String> = [
        "auth.login", "auth.logout", "auth.status",
        "config.get", "config.set",
        "hello",
        "model.list", "model.probe", "model.select",
        "permission.respond",
        "ping",
        "run.cancel", "run.start",
        "session.delete", "session.list", "session.load", "session.save",
        "tool.list",
    ]

    public static let terminalEventTypes: Set<String> = [
        "run_cancelled", "run_completed", "run_failed",
    ]

    public static let eventTypes: Set<String> = [
        "run_started",
        "message_started", "message_delta", "reasoning_delta", "message_completed", "message_failed",
        "tool_call_started", "tool_call_completed", "tool_call_failed",
        "permission_requested", "permission_resolved",
        "context_compaction_started", "context_compaction_completed", "context_compaction_failed",
        "run_completed", "run_failed", "run_cancelled",
    ]
}

// MARK: - Error codes

/// JSON-RPC's reserved range plus the service's own -33xxx range. The numbers
/// are the contract; the names are for logs and for switching in Swift.
public enum ErrorCode: Sendable, Equatable {
    case parseError
    case invalidRequest
    case methodNotFound
    case invalidParams
    case internalError
    case notImplemented
    case providerError
    case modelUnavailable
    case notAuthenticated
    case permissionDenied
    case cancelled
    case workingDirDenied
    case sessionNotFound
    case runNotFound
    case protocolMismatch
    case dependencyMissing
    /// The session has a run on it. Not a failure to show the user as one: the
    /// app disables delete on a running row, so this is the backstop for a run
    /// that started on another client — the CLI resuming the same id.
    case sessionBusy

    /// An unrecognised code is not in the client's vocabulary; keep the number
    /// so a newer server is still reported faithfully.
    case unknown(Int)

    /// Every code this client knows, for the manifest drift test.
    public static let known: [ErrorCode] = [
        .parseError, .invalidRequest, .methodNotFound, .invalidParams, .internalError,
        .notImplemented, .providerError, .modelUnavailable, .notAuthenticated,
        .permissionDenied, .cancelled, .workingDirDenied, .sessionNotFound,
        .runNotFound, .protocolMismatch, .dependencyMissing, .sessionBusy,
    ]

    public init(code: Int) {
        self = switch code {
        case -32700: .parseError
        case -32600: .invalidRequest
        case -32601: .methodNotFound
        case -32602: .invalidParams
        case -32603: .internalError
        case -32604: .notImplemented
        case -33000: .providerError
        case -33001: .modelUnavailable
        case -33002: .notAuthenticated
        case -33003: .permissionDenied
        case -33004: .cancelled
        case -33005: .workingDirDenied
        case -33006: .sessionNotFound
        case -33007: .runNotFound
        case -33008: .protocolMismatch
        case -33009: .dependencyMissing
        case -33010: .sessionBusy
        default: .unknown(code)
        }
    }

    public var rawCode: Int {
        if case .unknown(let code) = self { return code }
        return switch self {
        case .parseError: -32700
        case .invalidRequest: -32600
        case .methodNotFound: -32601
        case .invalidParams: -32602
        case .internalError: -32603
        case .notImplemented: -32604
        case .providerError: -33000
        case .modelUnavailable: -33001
        case .notAuthenticated: -33002
        case .permissionDenied: -33003
        case .cancelled: -33004
        case .workingDirDenied: -33005
        case .sessionNotFound: -33006
        case .runNotFound: -33007
        case .protocolMismatch: -33008
        case .dependencyMissing: -33009
        case .sessionBusy: -33010
        case .unknown(let code): code
        }
    }
}

/// An error the server reported for a call. `data` carries the structured
/// detail — a refused `working_dir` names the roots it is inside of, which is
/// the difference between an alert the user can act on and one they cannot.
public struct ServiceError: Error, LocalizedError, Sendable {
    public let code: ErrorCode
    public let message: String
    public let data: JSONValue?

    public init(code: ErrorCode, message: String, data: JSONValue? = nil) {
        self.code = code
        self.message = message
        self.data = data
    }

    public var errorDescription: String? { "\(code): \(message)" }
}

/// Transport-level failures: the socket, the framing, the decode. Distinct from
/// `ServiceError`, which is a well-formed answer to a well-formed question.
public enum ClientError: Error, LocalizedError, Sendable {
    case notConnected
    case connected(String)
    case frameTooLarge(Int)
    case framing(String)
    case decoding(String)
    case closed
    case timedOut(String)

    public var errorDescription: String? {
        return switch self {
        case .notConnected: "not connected"
        case .connected(let why): "connection failed: \(why)"
        case .frameTooLarge(let n): "frame of \(n) bytes exceeds the \(Wire.maxFrameBytes) byte limit"
        case .framing(let why): "framing error: \(why)"
        case .decoding(let why): "decode error: \(why)"
        case .closed: "the connection closed"
        case .timedOut(let what): "timed out waiting for \(what)"
        }
    }
}

// MARK: - Envelope

public struct RpcRequest: Sendable {
    public let id: String
    public let method: String
    public let params: JSONValue?

    public init(id: String, method: String, params: JSONValue?) {
        self.id = id
        self.method = method
        self.params = params
    }

    /// Wire form, written by hand rather than encoded from a struct: JSON-RPC
    /// requires `id` and `method` present and `params` absent (not null) when
    /// there are none, and a struct with optional fields encodes `"params": null`.
    public func wireObject() -> [String: Any] {
        var out: [String: Any] = ["jsonrpc": "2.0", "id": id, "method": method]
        if let params { out["params"] = Self.plain(params) }
        return out
    }

    static func plain(_ value: JSONValue) -> Any {
        switch value {
        case .null: NSNull()
        case .bool(let v): v
        case .int(let v): v
        case .double(let v): v
        case .string(let v): v
        case .array(let v): v.map(Self.plain)
        case .object(let v): v.mapValues(Self.plain)
        }
    }
}

/// A decoded incoming frame. The server sends responses and `event`
/// notifications; a request from the server would be a protocol violation, and
/// decoding it as a distinct case is how the client notices rather than ignores.
public enum RpcMessage: Sendable {
    case response(id: String, result: JSONValue?, error: ServiceError?)
    case event(AgentEvent)
    case serverRequest(method: String, id: String)
}

// MARK: - Framing

/// Splits the byte stream into NDJSON frames.
///
/// Splitting is done on the newline *byte*, never on a decoded string: a chunk
/// boundary can land in the middle of a multi-byte UTF-8 sequence (the fixtures
/// contain `中文 ✓` on purpose), and decoding per chunk would corrupt it.
public struct FrameDecoder {
    private var buffer = Data()
    private let maxBytes: Int

    public init(maxBytes: Int = Wire.maxFrameBytes) {
        self.maxBytes = maxBytes
    }

    public mutating func append(_ data: Data) throws -> [Data] {
        buffer.append(data)
        var frames: [Data] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<newline]
            if line.count > maxBytes {
                // Refuse the whole connection: a peer that sends an 8 MiB frame
                // is either broken or hostile, and a partial read is not a
                // recoverable state.
                buffer.removeAll(keepingCapacity: false)
                throw ClientError.frameTooLarge(line.count)
            }
            if !line.isEmpty { frames.append(Data(line)) }
            buffer.removeSubrange(buffer.startIndex...newline)
        }
        if buffer.count > maxBytes {
            buffer.removeAll(keepingCapacity: false)
            throw ClientError.frameTooLarge(buffer.count)
        }
        return frames
    }

    /// A frame the peer closed without a newline. Reported, not silently dropped:
    /// a truncated event is a hole in the timeline the UI would never show.
    public mutating func takeTrailingPartial() -> Data? {
        guard !buffer.isEmpty else { return nil }
        let tail = buffer
        buffer.removeAll(keepingCapacity: false)
        return tail
    }
}

/// The one decoder for result payloads. The wire is snake_case by design, so
/// every result struct is camelCase and this is the single place that bridges
/// them — a decoder configured per call site is how a field silently stops
/// decoding in one method and keeps decoding in the next.
let wireDecoder: JSONDecoder = {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return decoder
}()

public enum Frame {
    /// One JSON object plus the newline that ends it.
    public static func encode(_ object: [String: Any]) throws -> Data {
        let data = try JSONSerialization.data(withJSONObject: object, options: [])
        var framed = data
        framed.append(0x0A)
        return framed
    }

    public static func decode(_ frame: Data) throws -> RpcMessage {
        guard let object = try JSONSerialization.jsonObject(with: frame) as? [String: Any] else {
            throw ClientError.decoding("frame is not a JSON object")
        }
        if let method = object["method"] as? String {
            if method == "event" {
                let params = object["params"] as? [String: Any] ?? [:]
                let data = try JSONSerialization.data(withJSONObject: params, options: [])
                return .event(try AgentEvent.decode(from: data))
            }
            let id = (object["id"] as? String) ?? (object["id"] as? Int).map(String.init) ?? ""
            return .serverRequest(method: method, id: id)
        }
        let id = (object["id"] as? String) ?? (object["id"] as? Int).map(String.init) ?? ""
        let result = JSONValue.fromJSON(object["result"])
        var error: ServiceError?
        if let raw = object["error"] as? [String: Any] {
            error = ServiceError(
                code: ErrorCode(code: (raw["code"] as? Int) ?? 0),
                message: (raw["message"] as? String) ?? "",
                data: JSONValue.fromJSON(raw["data"])
            )
        }
        return .response(id: id, result: result, error: error)
    }
}

extension JSONValue {
    /// `JSONSerialization` hands back `Any`, and the only place allowed to see
    /// it is here.
    ///
    /// The order of the checks is the whole trick: `NSNumber` bridges to `Bool`
    /// for 1 and 0, so a JSON `1` answers `as? Bool` and becomes `true`. A
    /// boolean is identified by identity against the shared `kCFBoolean` singletons
    /// before any numeric cast is tried, and numbers are matched as numbers.
    public static func fromJSON(_ value: Any?) -> JSONValue? {
        if value == nil { return nil }
        if value is NSNull { return .null }
        if let number = value as? NSNumber {
            if number === (kCFBooleanTrue as NSNumber) || number === (kCFBooleanFalse as NSNumber) {
                return .bool(number.boolValue)
            }
            let double = number.doubleValue
            if double == double.rounded(), let exact = Int(exactly: number.int64Value) {
                return .int(exact)
            }
            return .double(double)
        }
        if let text = value as? String { return .string(text) }
        if let array = value as? [Any] { return .array(array.map { fromJSON($0) ?? .null }) }
        if let object = value as? [String: Any] {
            var out: [String: JSONValue] = [:]
            for (key, item) in object { out[key] = fromJSON(item) ?? .null }
            return .object(out)
        }
        return .string(String(describing: value!))
    }
}

import Foundation

// MARK: - Events

/// The event stream, as `openagent/service/protocol.py` defines it.
///
/// Two rules make this survivable across versions:
///
/// * An unknown `type` decodes to ``AgentEvent/unknown(type:params:)``. The
///   Python side ignores fields it does not know, so a newer service must not
///   crash an older app — a dropped event is visible in the timeline, a crash
///   is the app disappearing.
/// * Every payload is decoded from the notification's `params`, whose field
///   names are snake_case on the wire and camelCase here
///   (`.convertFromSnakeCase`), which is the whole reason the wire format is
///   snake_case.
public enum AgentEvent: Sendable, Equatable {
    case runStarted(RunStarted)
    case messageStarted(MessageStarted)
    case messageDelta(MessageDelta)
    case messageCompleted(MessageCompleted)
    case messageFailed(MessageFailed)
    case toolCallStarted(ToolCallStarted)
    case toolCallCompleted(ToolCallCompleted)
    case toolCallFailed(ToolCallFailed)
    case permissionRequested(PermissionRequested)
    case permissionResolved(PermissionResolved)
    case contextCompactionStarted(ContextCompactionStarted)
    case contextCompactionCompleted(ContextCompactionCompleted)
    case contextCompactionFailed(ContextCompactionFailed)
    case runCompleted(RunCompleted)
    case runFailed(RunFailed)
    case runCancelled(RunCancelled)
    case unknown(type: String, params: JSONValue)

    public var type: String {
        return switch self {
        case .runStarted: "run_started"
        case .messageStarted: "message_started"
        case .messageDelta: "message_delta"
        case .messageCompleted: "message_completed"
        case .messageFailed: "message_failed"
        case .toolCallStarted: "tool_call_started"
        case .toolCallCompleted: "tool_call_completed"
        case .toolCallFailed: "tool_call_failed"
        case .permissionRequested: "permission_requested"
        case .permissionResolved: "permission_resolved"
        case .contextCompactionStarted: "context_compaction_started"
        case .contextCompactionCompleted: "context_compaction_completed"
        case .contextCompactionFailed: "context_compaction_failed"
        case .runCompleted: "run_completed"
        case .runFailed: "run_failed"
        case .runCancelled: "run_cancelled"
        case .unknown(let type, _): type
        }
    }

    /// The run this event belongs to. Every event carries `run_id`, which is
    /// what lets a UI attach a stream to the run it started.
    public var runId: String? {
        return switch self {
        case .runStarted(let e): e.runId
        case .messageStarted(let e): e.runId
        case .messageDelta(let e): e.runId
        case .messageCompleted(let e): e.runId
        case .messageFailed(let e): e.runId
        case .toolCallStarted(let e): e.runId
        case .toolCallCompleted(let e): e.runId
        case .toolCallFailed(let e): e.runId
        case .permissionRequested(let e): e.runId
        case .permissionResolved(let e): e.runId
        case .contextCompactionStarted(let e): e.runId
        case .contextCompactionCompleted(let e): e.runId
        case .contextCompactionFailed(let e): e.runId
        case .runCompleted(let e): e.runId
        case .runFailed(let e): e.runId
        case .runCancelled(let e): e.runId
        case .unknown: nil
        }
    }

    public var isTerminal: Bool {
        return switch self {
        case .runCompleted, .runFailed, .runCancelled: true
        // A terminal type this client does not know still ends the stream, so a
        // newer server cannot leave an older app waiting forever.
        case .unknown(let type, _): Wire.terminalEventTypes.contains(type)
        default: false
        }
    }

    /// Decode one notification's `params` object.
    public static func decode(from data: Data) throws -> AgentEvent {
        let decoder = agentEventDecoder
        let envelope = try decoder.decode(TypeProbe.self, from: data)
        return switch envelope.type {
        case "run_started": .runStarted(try decoder.decode(RunStarted.self, from: data))
        case "message_started": .messageStarted(try decoder.decode(MessageStarted.self, from: data))
        case "message_delta": .messageDelta(try decoder.decode(MessageDelta.self, from: data))
        case "message_completed": .messageCompleted(try decoder.decode(MessageCompleted.self, from: data))
        case "message_failed": .messageFailed(try decoder.decode(MessageFailed.self, from: data))
        case "tool_call_started": .toolCallStarted(try decoder.decode(ToolCallStarted.self, from: data))
        case "tool_call_completed": .toolCallCompleted(try decoder.decode(ToolCallCompleted.self, from: data))
        case "tool_call_failed": .toolCallFailed(try decoder.decode(ToolCallFailed.self, from: data))
        case "permission_requested": .permissionRequested(try decoder.decode(PermissionRequested.self, from: data))
        case "permission_resolved": .permissionResolved(try decoder.decode(PermissionResolved.self, from: data))
        case "context_compaction_started":
            .contextCompactionStarted(try decoder.decode(ContextCompactionStarted.self, from: data))
        case "context_compaction_completed":
            .contextCompactionCompleted(try decoder.decode(ContextCompactionCompleted.self, from: data))
        case "context_compaction_failed":
            .contextCompactionFailed(try decoder.decode(ContextCompactionFailed.self, from: data))
        case "run_completed": .runCompleted(try decoder.decode(RunCompleted.self, from: data))
        case "run_failed": .runFailed(try decoder.decode(RunFailed.self, from: data))
        case "run_cancelled": .runCancelled(try decoder.decode(RunCancelled.self, from: data))
        default:
            .unknown(type: envelope.type, params: try decoder.decode(JSONValue.self, from: data))
        }
    }
}

private struct TypeProbe: Decodable {
    let type: String
}

/// One decoder configuration for the whole stream, so the key strategy cannot
/// drift between event types.
let agentEventDecoder: JSONDecoder = {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return decoder
}()

// MARK: - Payloads

public struct RunStarted: Decodable, Sendable, Equatable {
    public let runId: String
}

public struct MessageStarted: Decodable, Sendable, Equatable {
    public let runId: String
    public let messageId: String
}

public struct MessageDelta: Decodable, Sendable, Equatable {
    public let runId: String
    public let messageId: String
    public let delta: String
}

public struct MessageCompleted: Decodable, Sendable, Equatable {
    public let runId: String
    public let messageId: String
    public let outputText: String
}

public struct MessageFailed: Decodable, Sendable, Equatable {
    public let runId: String
    public let messageId: String
    public let error: String
}

public struct ToolCallStarted: Decodable, Sendable, Equatable {
    public let runId: String
    public let messageId: String
    public let toolCallId: String
    public let toolName: String
    /// Free-form: the arguments are whatever the model passed, and the tool's
    /// own schema decides them. A UI renders `arguments` as rows, not fields.
    public let arguments: JSONValue
}

public struct ToolCallCompleted: Decodable, Sendable, Equatable {
    public let runId: String
    public let messageId: String
    public let toolCallId: String
    public let toolName: String
    public let result: JSONValue
}

public struct ToolCallFailed: Decodable, Sendable, Equatable {
    public let runId: String
    public let messageId: String
    public let toolCallId: String
    /// Optional, because the engine can fail a call whose name it never
    /// resolved. A tool that fails before its name is known is a real case.
    public let toolName: String?
    public let error: String
}

public struct PermissionRequested: Decodable, Sendable, Equatable {
    public let runId: String
    /// Correlation key for the answer, which goes back as `permission.respond`.
    public let requestId: String
    public let toolName: String
    public let arguments: JSONValue
    /// Why the tool is risky, in the server's words ("exec: runs arbitrary
    /// commands"). Show it: it is the only explanation the user gets.
    public let reason: String
}

public struct PermissionResolved: Decodable, Sendable, Equatable {
    public let runId: String
    public let requestId: String
    public let granted: Bool
    public let scope: PermissionScope
    /// *Why* the answer is what it is. "The user refused" and "nobody answered
    /// in 60s" are different lines in a UI, and a silent timeout is the
    /// difference between a hung app and a refused tool.
    public let reason: String
}

public struct ContextCompactionStarted: Decodable, Sendable, Equatable {
    public let runId: String
    public let reason: String
}

public struct ContextCompactionCompleted: Decodable, Sendable, Equatable {
    public let runId: String
    public let reason: String
}

public struct ContextCompactionFailed: Decodable, Sendable, Equatable {
    public let runId: String
    public let reason: String
    public let error: String
}

public struct AgentResult: Decodable, Sendable, Equatable {
    public let runId: String
    public let finalMessageId: String
    public let outputText: String
    public let stopReason: StopReason
}

public struct RunCompleted: Decodable, Sendable, Equatable {
    public let runId: String
    public let finalMessageId: String
    /// The terminal payload mirrors the engine's `AgentResult`, so a client
    /// that only wants the answer reads one object and never reassembles
    /// deltas.
    public let result: AgentResult
}

public struct RunFailed: Decodable, Sendable, Equatable {
    public let runId: String
    public let error: String
}

public struct RunCancelled: Decodable, Sendable, Equatable {
    public let runId: String
    public let reason: String
}

// MARK: - Closed sets with a fallback

public enum PermissionScope: Sendable, Equatable {
    case once, session, never, other(String)

    public var rawValue: String {
        return switch self {
        case .once: "once"
        case .session: "session"
        case .never: "never"
        case .other(let v): v
        }
    }
}

extension PermissionScope: Decodable {
    public init(from decoder: any Decoder) throws {
        self = PermissionScope(rawValue: try decoder.singleValueContainer().decode(String.self))
    }

    public init(rawValue: String) {
        self = switch rawValue {
        case "once": .once
        case "session": .session
        case "never": .never
        default: .other(rawValue)
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

public enum StopReason: Sendable, Equatable {
    case endTurn, maxTurns, emptyResponse, other(String)

    public var rawValue: String {
        return switch self {
        case .endTurn: "end_turn"
        case .maxTurns: "max_turns"
        case .emptyResponse: "empty_response"
        case .other(let v): v
        }
    }
}

extension StopReason: Decodable {
    public init(from decoder: any Decoder) throws {
        self = StopReason(rawValue: try decoder.singleValueContainer().decode(String.self))
    }

    public init(rawValue: String) {
        self = switch rawValue {
        case "end_turn": .endTurn
        case "max_turns": .maxTurns
        case "empty_response": .emptyResponse
        default: .other(rawValue)
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

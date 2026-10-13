import Foundation

/// A JSON value that is not a known shape.
///
/// Tool arguments, config blobs, and session messages are decided by the
/// server (and by the model), so the client cannot have a struct for them. The
/// alternative — decoding into `[String: Any]` — leaks `Any` into the UI layer
/// and makes `Sendable` checking impossible, which matters because these values
/// cross actor boundaries on their way to a SwiftUI view.
public enum JSONValue: Sendable, Hashable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public var stringValue: String? {
        if case .string(let v) = self { return v }
        return nil
    }

    public var boolValue: Bool? {
        if case .bool(let v) = self { return v }
        return nil
    }

    public var intValue: Int? {
        return switch self {
        case .int(let v): v
        case .double(let v) where v == v.rounded(): Int(v)
        default: nil
        }
    }

    public var objectValue: [String: JSONValue]? {
        if case .object(let v) = self { return v }
        return nil
    }

    public var arrayValue: [JSONValue]? {
        if case .array(let v) = self { return v }
        return nil
    }

    /// Text for a UI row. Anything the model produced is shown as-is; a tool
    /// result is a string in practice, and a structured one is JSON-dumped
    /// rather than dropped, because a hidden result is a silent failure.
    public var displayText: String {
        return switch self {
        case .null: ""
        case .bool(let v): v ? "true" : "false"
        case .int(let v): String(v)
        case .double(let v): String(v)
        case .string(let v): v
        case .array, .object:
            String(data: (try? JSONEncoder().encode(self)) ?? Data(), encoding: .utf8) ?? ""
        }
    }
}

extension JSONValue: Codable {
    public init(from decoder: any Decoder) throws {
        var container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let v = try? container.decode(Bool.self) {
            self = .bool(v)
        } else if let v = try? container.decode(Int.self) {
            self = .int(v)
        } else if let v = try? container.decode(Double.self) {
            self = .double(v)
        } else if let v = try? container.decode(String.self) {
            self = .string(v)
        } else if let v = try? container.decode([JSONValue].self) {
            self = .array(v)
        } else if let v = try? container.decode([String: JSONValue].self) {
            self = .object(v)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "value is not JSON"
            )
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let v): try container.encode(v)
        case .int(let v): try container.encode(v)
        case .double(let v): try container.encode(v)
        case .string(let v): try container.encode(v)
        case .array(let v): try container.encode(v)
        case .object(let v): try container.encode(v)
        }
    }
}

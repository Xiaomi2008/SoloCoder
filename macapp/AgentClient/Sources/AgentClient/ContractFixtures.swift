import Foundation

/// The contract's own samples, read from the files Python generates.
///
/// `scripts/gen_protocol_fixtures.py` writes `tests/service/fixtures/{protocol,events}.json`
/// from the Python event dataclasses. This type is the only reader of them, so
/// the Swift suites and the standalone `agent-checks` executable check the same
/// bytes, and a fixture that only one of them reads is impossible.
///
/// It lives in the library rather than in a test target because the checks
/// executable is a product, not a test: on a Command Line Tools install (no
/// Xcode) `swift test` cannot build at all, and the contract suite would be
/// unrunnable.
public enum ContractFixtures {
    /// The repository root, found by walking up from this file's compile-time
    /// path. Walking up (rather than hard-coding) keeps the suite working from
    /// any checkout; failing loudly when the layout moves keeps a silently
    /// skipped contract test from shipping.
    public static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = url.appendingPathComponent("tests/service/fixtures/events.json")
            if FileManager.default.fileExists(atPath: candidate.path) { return url }
            url = url.deletingLastPathComponent()
        }
        fatalError("tests/service/fixtures not found above \(#filePath)")
    }()

    public static var fixturesDirectory: URL { repositoryRoot.appendingPathComponent("tests/service/fixtures") }
    public static var eventsFile: URL { fixturesDirectory.appendingPathComponent("events.json") }
    public static var protocolFile: URL { fixturesDirectory.appendingPathComponent("protocol.json") }
    public static var venvPython: URL { repositoryRoot.appendingPathComponent(".venv/bin/python") }
    public static var cliEntry: URL { repositoryRoot.appendingPathComponent("cli_coder.py") }

    public struct EventEntry: Decodable, Sendable {
        public let type: String
        public let params: JSONValue
    }

    public struct Manifest: Decodable, Sendable {
        public let protocolVersion: Int
        public let maxFrameBytes: Int
        public let methods: [String]
        public let terminalEventTypes: [String]
        public let errorCodes: [String: Int]
        public let events: [EventEntry]
    }

    public static func events() throws -> [EventEntry] {
        struct Wrapper: Decodable { let events: [EventEntry] }
        return try JSONDecoder().decode(Wrapper.self, from: Data(contentsOf: eventsFile)).events
    }

    public static func manifest() throws -> Manifest {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Manifest.self, from: Data(contentsOf: protocolFile))
    }

    /// The `params` object of a fixture entry, as the event codec sees it.
    public static func paramsData(_ entry: EventEntry) throws -> Data {
        try JSONEncoder().encode(entry.params)
    }
}

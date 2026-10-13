import Testing
import Foundation
@testable import AgentClient

/// The fixtures in `tests/service/fixtures/` are generated from the Python
/// types by `scripts/gen_protocol_fixtures.py`. Decoding them here is the drift
/// detector: add a field to a Python event and regenerate, and this suite fails
/// until the Swift types follow.
@Suite("fixture decoding")
struct FixtureDecodingTests {
    private var events: [ContractFixtures.EventEntry] { get throws { try ContractFixtures.events() } }

    @Test("every fixture event decodes to its known type")
    func everyFixtureEventDecodes() throws {
        let list = try events
        #expect(!list.isEmpty, "the fixtures are empty; regenerate them")

        for (index, entry) in list.enumerated() {
            let event = try AgentEvent.decode(from: try ContractFixtures.paramsData(entry))
            #expect(
                event.type == entry.type,
                "fixture \(index) (\(entry.type)) decoded as \(event.type)"
            )
            if case .unknown(let type, _) = event {
                Issue.record("fixture \(index) (\(type)) has no Swift case")
            }
        }
    }

    @Test("the fixtures cover every Swift event type")
    func fixturesCoverEveryEventType() throws {
        let covered = Set(try events.map(\.type))
        let missing = Wire.eventTypes.subtracting(covered)
        #expect(
            missing.isEmpty,
            "no fixture exercises \(missing.sorted()); the Swift decoder is untested for those"
        )
    }

    @Test("unicode survives the wire")
    func unicode() throws {
        let delta = try #require(try events.first { $0.type == "message_delta" })
        let event = try AgentEvent.decode(from: ContractFixtures.paramsData(delta))
        guard case .messageDelta(let payload) = event else {
            Issue.record("expected message_delta")
            return
        }
        #expect(payload.delta == "Writing — 中文 ✓\n")
    }

    @Test("tool arguments keep their nesting")
    func nestedArguments() throws {
        let started = try #require(try events.first { $0.type == "tool_call_started" })
        let event = try AgentEvent.decode(from: ContractFixtures.paramsData(started))
        guard case .toolCallStarted(let call) = event else {
            Issue.record("expected tool_call_started")
            return
        }
        #expect(call.toolName == "bash")
        let arguments = try #require(call.arguments.objectValue)
        #expect(arguments["command"]?.stringValue == "ls -la")
        let nested = try #require(arguments["nested"]?.objectValue)
        #expect(nested["a"]?.intValue == 1)
        #expect(nested["b"]?.arrayValue?.count == 2)
    }

    @Test("permission events carry their reasons")
    func permissionReasons() throws {
        let requested = try #require(try events.first { $0.type == "permission_requested" })
        if case .permissionRequested(let request) =
            try AgentEvent.decode(from: ContractFixtures.paramsData(requested)) {
            #expect(request.requestId == "req_sample")
            #expect(request.toolName == "bash")
            #expect(request.reason == "exec: runs arbitrary commands")
        } else {
            Issue.record("expected permission_requested")
        }

        let resolved = try #require(try events.first { $0.type == "permission_resolved" })
        if case .permissionResolved(let answer) =
            try AgentEvent.decode(from: ContractFixtures.paramsData(resolved)) {
            #expect(answer.granted)
            #expect(answer.scope == .session)
            #expect(answer.reason == "approved by the user for this session")
        } else {
            Issue.record("expected permission_resolved")
        }
    }

    @Test("every stop reason in the fixtures decodes")
    func stopReasons() throws {
        var seen: Set<String> = []
        for entry in try events where entry.type == "run_completed" {
            if case .runCompleted(let completed) = try AgentEvent.decode(from: ContractFixtures.paramsData(entry)) {
                seen.insert(completed.result.stopReason.rawValue)
            }
        }
        #expect(seen == ["end_turn", "max_turns"])
    }

    @Test("terminal events are marked terminal")
    func terminalFlags() throws {
        for entry in try events {
            let event = try AgentEvent.decode(from: ContractFixtures.paramsData(entry))
            #expect(
                event.isTerminal == Wire.terminalEventTypes.contains(entry.type),
                "isTerminal disagrees for \(entry.type)"
            )
        }
    }

    @Test("every event carries its run id")
    func runIds() throws {
        for entry in try events {
            let event = try AgentEvent.decode(from: ContractFixtures.paramsData(entry))
            #expect(event.runId == "run_sample", "\(entry.type) lost its run_id")
        }
    }

    @Test("an unknown event type does not kill the stream")
    func unknownEventType() throws {
        // A newer service must not crash an older app. The event is preserved,
        // not dropped, so a UI can show that something happened.
        let data = Data(#"{"type":"quantum_leap","run_id":"run_1","why":"because"}"#.utf8)
        let event = try AgentEvent.decode(from: data)
        guard case .unknown(let type, let params) = event else {
            Issue.record("expected .unknown")
            return
        }
        #expect(type == "quantum_leap")
        #expect(params.objectValue?["why"]?.stringValue == "because")
        #expect(!event.isTerminal)
    }

    @Test("an unknown enum value does not kill the event")
    func unknownEnumValue() throws {
        let data = Data(
            #"{"type":"permission_resolved","run_id":"r","request_id":"q","granted":true,"scope":"decade","reason":"because"}"#.utf8
        )
        if case .permissionResolved(let answer) = try AgentEvent.decode(from: data) {
            #expect(answer.scope == .other("decade"))
        } else {
            Issue.record("expected permission_resolved")
        }
    }
}

/// The manifest is the summary of the contract. If a method is added on the
/// Python side and the Swift list is not updated, a client written against this
/// package would not know the method exists — so the lists must agree.
@Suite("protocol manifest")
struct ProtocolManifestTests {
    @Test("the manifest matches the Swift constants")
    func manifestMatches() throws {
        let manifest = try ContractFixtures.manifest()
        #expect(manifest.protocolVersion == Wire.protocolVersion)
        #expect(manifest.maxFrameBytes == Wire.maxFrameBytes)
        #expect(Set(manifest.methods) == Wire.methods, "method list drifted")
        #expect(Set(manifest.terminalEventTypes) == Wire.terminalEventTypes)
        #expect(Set(manifest.events.map(\.type)) == Wire.eventTypes, "event list drifted")
    }

    @Test("every error code the server defines is named here")
    func errorCodesMatch() throws {
        let manifest = try ContractFixtures.manifest()
        #expect(
            Set(manifest.errorCodes.values) == Set(ErrorCode.known.map(\.rawCode)),
            "the codes the server defines and the codes this client names have drifted"
        )
        for (name, code) in manifest.errorCodes {
            #expect(ErrorCode(code: code).rawCode == code, "\(name) does not round-trip")
        }
    }

    @Test("an unknown error code keeps its number")
    func unknownErrorCode() {
        #expect(ErrorCode(code: -33999).rawCode == -33999)
    }
}

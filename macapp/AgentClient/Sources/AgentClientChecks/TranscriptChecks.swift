import AgentAppCore
import AgentClient
import Foundation

// The UI state machine, checked headless.
//
// Every case here is a property of the protocol that a chat view has to get
// right, written down as JSON off the wire and folded through the reducer. The
// events are decoded, not constructed: a payload field that drifts fails here
// too.

private func ev(_ json: String) throws -> AgentEvent {
    try AgentEvent.decode(from: Data(json.utf8))
}

private func fold(
    _ events: [AgentEvent],
    prompt: String? = "list the files",
    terminalTypes: Set<String> = Wire.terminalEventTypes
) throws -> Transcript {
    var transcript = Transcript(terminalTypes: terminalTypes)
    if let prompt { transcript.addUserMessage(prompt) }
    for event in events { transcript.apply(event) }
    return transcript
}

private func assistantText(_ transcript: Transcript) -> [String] {
    transcript.lines.compactMap { if case .assistant(let text) = $0.kind { return text } else { return nil } }
}

private func notes(_ transcript: Transcript) -> [String] {
    transcript.lines.compactMap { if case .note(let text) = $0.kind { return text } else { return nil } }
}

private func thinkingIndexOrMax(_ transcript: Transcript) -> Int {
    transcript.lines.firstIndex { if case .reasoning = $0.kind { return true } else { return false } } ?? .max
}

func transcriptChecks(_ checks: Checks) throws {
    print("transcript")

    // MARK: the model thinking before it answers

    let thinking = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"reasoning_delta","run_id":"r1","message_id":"m1","delta":"The user wants "}"#),
        ev(#"{"type":"reasoning_delta","run_id":"r1","message_id":"m1","delta":"a list. I will glob."}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"Here "}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"are they."}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"Here are they."}"#),
        ev(#"{"type":"run_completed","run_id":"r1","final_message_id":"m1","result":{"run_id":"r1","final_message_id":"m1","output_text":"Here are they.","stop_reason":"end_turn"}}"#),
    ])
    let reasoning = thinking.lines.compactMap { if case .reasoning(let text) = $0.kind { return text } else { return nil } }
    checks.expect(reasoning == ["The user wants a list. I will glob."], "the thinking is visible, in order, as one line")
    checks.expect(
        assistantText(thinking) == ["Here are they."],
        "the thinking never joins the answer — the model is not shown its own reasoning"
    )
    checks.expect(thinking.finalText == "Here are they.", "finalText is the answer, not the thinking")
    let thinkingLine = thinking.lines.first { if case .reasoning = $0.kind { return true } else { return false } }
    checks.expect(thinkingLine?.streaming == false, "the thinking closes once the answer starts, so the view collapses it")
    let answerIndex = thinking.lines.firstIndex { if case .assistant = $0.kind { return true } else { return false } }!
    let thinkingIndex = thinkingIndexOrMax(thinking)
    checks.expect(thinkingIndex < answerIndex, "the thinking sits above the answer it produced, not below it")

    // A run that thinks and never answers: the thinking must stay on screen,
    // because that is the only evidence the user has that the run is alive.
    let onlyThinking = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"reasoning_delta","run_id":"r1","message_id":"m1","delta":"thinking and thinking"}"#),
        ev(#"{"type":"run_cancelled","run_id":"r1","reason":"cancelled"}"#),
    ])
    checks.expect(
        onlyThinking.lines.contains { if case .reasoning(let t) = $0.kind { return t == "thinking and thinking" } else { return false } },
        "a run that only thinks still shows what it thought"
    )
    checks.expect(
        onlyThinking.lines.first(where: { $0.messageId == "m1" && $0.streaming })?.kind != nil,
        "and it stays open, because nothing closed it"
    )

    // MARK: the documented sequence

    let run = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"He"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"llo"}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"Hello world"}"#),
        ev(#"{"type":"tool_call_started","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"bash","arguments":{"command":"ls"}}"#),
        ev(#"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"bash","result":"file.txt"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m2"}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m2","output_text":"1 file"}"#),
        ev(#"{"type":"run_completed","run_id":"r1","final_message_id":"m2","result":{"run_id":"r1","final_message_id":"m2","output_text":"1 file","stop_reason":"end_turn"}}"#),
    ])

    checks.equal(run.lines.count, 4, "user, assistant, tool, assistant")
    checks.equal(assistantText(run), ["Hello world", "1 file"], "one line per assistant turn")
    checks.equal(run.toolRows.count, 1, "the tool call is a row")
    checks.equal(run.toolRows.first?.name, "bash", "the row names the tool")
    checks.equal(run.toolRows.first?.state, .done("file.txt"), "the row shows the result")
    checks.equal(run.state, .finished(.endTurn), "the run finished at end_turn")
    checks.expect(!run.state.isRunning, "a finished run is not running")
    checks.equal(run.finalText, "1 file", "finalText is the last assistant text")
    checks.equal(run.runId, "r1", "the run id is tracked")

    // MARK: deltas

    let partial = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"Hel"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"lo w"}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"Hello world"}"#),
    ])
    checks.equal(
        assistantText(partial),
        ["Hello world"],
        "message_completed replaces the deltas — it is the authoritative text"
    )

    let interleaved = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"A1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m2"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m2","delta":"B1"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"A2"}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m2","output_text":"B1"}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"A1A2"}"#),
    ])
    checks.equal(
        assistantText(interleaved),
        ["A1A2", "B1"],
        "deltas are routed by message_id, not to the last line"
    )

    let orphan = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m9","delta":"surprise"}"#),
    ])
    checks.equal(assistantText(orphan), ["surprise"], "a delta with no message_started is shown, not dropped")

    let failedMessage = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"message_failed","run_id":"r1","message_id":"m1","error":"provider 502"}"#),
    ])
    checks.expect(
        failedMessage.lines.contains { if case .messageFailed(let e) = $0.kind { return e == "provider 502" } else { return false } },
        "a failed message shows its error"
    )

    // MARK: permissions

    let asked = try ev(
        #"{"type":"permission_requested","run_id":"r1","request_id":"p1","tool_name":"bash","arguments":{"command":"rm -r x"},"reason":"exec: runs arbitrary commands"}"#
    )
    let awaiting = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        asked,
    ])
    checks.equal(awaiting.state, .awaitingPermission("p1"), "the sheet knows which request to answer")
    checks.equal(awaiting.pendingPermission?.toolName, "bash", "the sheet names the tool")
    checks.equal(
        awaiting.pendingPermission?.arguments.objectValue?["command"]?.stringValue,
        "rm -r x",
        "the sheet shows the arguments"
    )
    checks.equal(
        awaiting.pendingPermission?.reason,
        "exec: runs arbitrary commands",
        "the sheet shows the server's reason"
    )
    checks.expect(awaiting.state.isRunning, "waiting on the user is still a running run")

    let denied = try fold([
        asked,
        ev(#"{"type":"permission_resolved","run_id":"r1","request_id":"p1","granted":false,"scope":"never","reason":"the user refused"}"#),
    ])
    checks.equal(denied.pendingPermission, nil, "the sheet closes when the answer lands")
    checks.equal(denied.toolRows.count, 0, "a denied tool emits no tool_call_started, so it has no tool row")
    checks.equal(denied.outcomes.count, 1, "the refusal is recorded")
    checks.equal(denied.outcomes.first?.granted, false, "the outcome says refused")
    checks.equal(denied.outcomes.first?.reason, "the user refused", "and why")
    checks.expect(
        denied.lines.contains { if case .permission(let outcome) = $0.kind { return !outcome.granted } else { return false } },
        "the refusal is a line in the timeline"
    )

    let timedOut = try fold([
        asked,
        ev(#"{"type":"permission_resolved","run_id":"r1","request_id":"p1","granted":false,"scope":"never","reason":"no answer within 60s; refused by default"}"#),
    ])
    checks.equal(
        timedOut.outcomes.first?.reason,
        "no answer within 60s; refused by default",
        "a timeout reads as a timeout, not a hang"
    )

    let granted = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        asked,
        ev(#"{"type":"permission_resolved","run_id":"r1","request_id":"p1","granted":true,"scope":"session","reason":"approved by the user for this session"}"#),
        ev(#"{"type":"tool_call_started","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"bash","arguments":{"command":"rm -r x"}}"#),
        ev(#"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"bash","result":"removed"}"#),
        ev(#"{"type":"run_completed","run_id":"r1","final_message_id":"m2","result":{"run_id":"r1","final_message_id":"m2","output_text":"done","stop_reason":"end_turn"}}"#),
    ])
    checks.equal(granted.outcomes.first?.scope, PermissionScope.session, "the scope the user chose is kept")
    checks.equal(granted.state, .finished(.endTurn), "the run went on after the grant")
    checks.equal(granted.toolRows.first?.state, .done("removed"), "and the tool ran")

    // An answer for a request the client never saw (a reconnect mid-run): the
    // outcome is still recorded, with the id it carries.
    let stray = try fold([ev(#"{"type":"permission_resolved","run_id":"r1","request_id":"p9","granted":true,"scope":"once","reason":"answered elsewhere"}"#)])
    checks.equal(stray.outcomes.count, 1, "an unrequested answer is recorded")
    checks.equal(stray.outcomes.first?.request.id, "p9", "with its request id")

    // MARK: terminals

    let cancelled = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"run_cancelled","run_id":"r1","reason":"the client disconnected"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"late"}"#),
    ])
    checks.equal(cancelled.state, .cancelled("the client disconnected"), "a cancelled run stays cancelled")
    checks.equal(assistantText(cancelled), [""], "a late delta does not resurrect the run")

    let broken = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"run_failed","run_id":"r1","error":"provider connection reset"}"#),
    ])
    checks.equal(broken.state, .failed("provider connection reset"), "a failed run says why")
    checks.expect(!broken.state.isRunning, "a failed run is over")

    let maxTurns = try fold([
        ev(#"{"type":"run_completed","run_id":"r1","final_message_id":"m1","result":{"run_id":"r1","final_message_id":"m1","output_text":"…","stop_reason":"max_turns"}}"#),
    ])
    checks.equal(maxTurns.state, .finished(.maxTurns), "max_turns is a distinct ending")
    checks.equal(maxTurns.state.label, "Stopped: max turns", "and is labelled as one")

    // A terminal type this client has never heard of still ends the run.
    // A newer server's terminal event, learned from its handshake: the client
    // has never heard of it, and the stream must still end.
    let futureTerminal = try fold(
        [ev(#"{"type":"run_aborted","run_id":"r1","why":"the model server was stopped"}"#)],
        terminalTypes: Wire.terminalEventTypes.union(["run_aborted"])
    )
    checks.equal(futureTerminal.state, .finished(.other("run_aborted")), "a terminal type learned from the server ends the run")

    let futureEvent = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"quantum_leap","run_id":"r1","why":"because"}"#),
    ])
    checks.equal(notes(futureEvent), ["event: quantum_leap"], "an unknown event leaves a trace")
    checks.equal(futureEvent.state, .streaming, "and does not end the run")

    // MARK: compaction

    let compacted = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"context_compaction_started","run_id":"r1","reason":"context exceeded the window"}"#),
        ev(#"{"type":"context_compaction_completed","run_id":"r1","reason":"context exceeded the window"}"#),
    ])
    let compactionRows = compacted.lines.compactMap { if case .compaction(let row) = $0.kind { return row } else { return nil } }
    checks.equal(compactionRows.count, 1, "compaction is one row")
    checks.equal(compactionRows.first?.state, .done, "which shows its outcome")

    let compactionFailed = try fold([
        ev(#"{"type":"context_compaction_started","run_id":"r1","reason":"context exceeded the window"}"#),
        ev(#"{"type":"context_compaction_failed","run_id":"r1","reason":"context exceeded the window","error":"no model for summarising"}"#),
    ])
    let failedRows = compactionFailed.lines.compactMap { if case .compaction(let row) = $0.kind { return row } else { return nil } }
    checks.equal(failedRows.first?.state, .failed("no model for summarising"), "a failed compaction says why")

    // MARK: previews

    let long = Transcript.preview(of: .string(String(repeating: "line\n", count: 40)))
    checks.expect(!long.contains("\n"), "a preview is one line")
    checks.expect(long.count <= 241, "and it is bounded (\(long.count) chars)")
    checks.expect(long.hasSuffix("…"), "truncation is visible")
    checks.equal(
        Transcript.preview(of: .object(["content": .string("42")])),
        "42",
        "a {content: …} result previews its content"
    )
    checks.equal(Transcript.preview(of: .null), "", "a null result previews as empty")

    // MARK: tool output
    //
    // The row used to store its own 240-character preview, so expanding a tool
    // row showed the arguments and never the output — the half the user opens it
    // for. The row keeps the result now; the label folds it.

    func toolRow(_ result: String) throws -> ToolCallRow? {
        let encoded = String(data: try JSONEncoder().encode(result), encoding: .utf8) ?? #""""#
        let folded = try fold([
            ev(#"{"type":"tool_call_started","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"read","arguments":{}}"#),
            ev(#"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"read","result":\#(encoded)}"#),
        ])
        return folded.toolRows.first
    }

    let manyLines = (1...40).map { "line \($0)" }.joined(separator: "\n")
    let forty = try toolRow(manyLines)?.state
    if case .done(let text) = forty {
        checks.equal(
            text.split(separator: "\n").count,
            40,
            "the row keeps all 40 lines of a result — a disclosure shows a file, not a fold"
        )
        checks.expect(Transcript.oneLine(text).count <= 241, "and the label is one bounded line")
    } else {
        checks.expect(false, "a completed tool row is done, got \(String(describing: forty))")
    }

    let huge = try toolRow(String(repeating: "x", count: 100_000))?.state
    if case .done(let text) = huge {
        checks.expect(text.utf8.count < 9_000, "a 100k result is bounded (\(text.utf8.count) kept)")
        checks.expect(text.hasPrefix("xxxx"), "the head is what is kept — a file starts at the top")
        checks.expect(text.contains("truncated"), "and the row says so, rather than lying about the whole")
    } else {
        checks.expect(false, "a huge result still lands in the row, got \(String(describing: huge))")
    }

    checks.equal(
        Transcript.resultText(
            of: .array([
                .object(["type": .string("text"), "text": .string("first block")]),
                .object(["type": .string("text"), "text": .string("second")]),
            ])
        ),
        "first block\nsecond",
        "a block-list result is its text, not its JSON"
    )

    // MARK: the whole event set

    var all = Transcript()
    for entry in try ContractFixtures.events() {
        all.apply(try AgentEvent.decode(from: ContractFixtures.paramsData(entry)))
    }
    checks.expect(all.finished, "the fixture stream ends the run")
    checks.expect(!all.lines.isEmpty, "and leaves a timeline")
    checks.equal(all.pendingPermission, nil, "a finished run has nothing pending")
}

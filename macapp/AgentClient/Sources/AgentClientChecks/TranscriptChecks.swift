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

/// A JSON string literal for text the test builds rather than types: a 4,000
/// paragraph answer with `\n\n` between the paragraphs is not a string literal
/// worth writing out, and its newlines have to be escaped to go on the wire.
private func jsonLiteral(_ text: String) -> String {
    let data = try! JSONSerialization.data(withJSONObject: [text], options: [])
    let encoded = String(decoding: data, as: UTF8.self)   // ["…"]
    return String(encoded.dropFirst().dropLast())
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
    // It stays on screen *while the run is alive*. Once the run has ended there is
    // nothing left to prove: the thinking is history, and the view keeps history
    // collapsed behind its "thought for a moment" disclosure. Left open, a
    // cancelled run leaves 36,000 characters of thinking expanded in the timeline
    // for the rest of the session.
    checks.expect(
        onlyThinking.lines[thinkingIndexOrMax(onlyThinking)].streaming == false,
        "and it closes when the run ends, so the thinking lives in its disclosure"
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

    // MARK: a streaming answer's structure

    // The answer arrives a character at a time and the view renders its finished
    // part as markdown, so the blocks have to be parsed at block boundaries — in
    // the reducer, where a check can see them, and not in the view per publish.
    var streaming = Transcript()
    for json in [
        #"{"type":"run_started","run_id":"r1"}"#,
        #"{"type":"message_started","run_id":"r1","message_id":"m1"}"#,
        ###"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"## Plan"}"###,
    ] {
        streaming.apply(try ev(json))
    }
    checks.expect(
        streaming.lines.last?.blocks.isEmpty == true,
        "a heading that has not had its blank line is not a finished block"
    )

    streaming.apply(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"\n- one"}"#))
    checks.expect(
        streaming.lines.last?.blocks.isEmpty == true,
        "a heading and a bullet with no blank line between them are one block in progress"
    )

    streaming.apply(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"\n\nbody"}"#))
    checks.equal(
        streaming.lines.last?.blocks,
        [.heading(level: 2, text: "Plan"), .bullets(["one"])],
        "the blank line closes the block, so the parse runs once and `body` is the tail"
    )

    // A run that ends without `message_completed` — Cancel pressed mid-answer, a
    // stream that drops — used to leave its answer `streaming` for the rest of the
    // session, which is the user reading `## Plan` in a run that finished.
    let cancelledMidAnswer = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(###"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"## Plan"}"###),
        ev(#"{"type":"run_cancelled","run_id":"r1","reason":"the user pressed Cancel"}"#),
    ])
    let answerLine = cancelledMidAnswer.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    checks.expect(
        answerLine?.streaming == false,
        "a cancelled run closes its answer, so the answer renders as markdown and not as markup"
    )

    // An answer a model never finishes.
    //
    // A quantized local model loops, and the answer becomes the longest text in
    // the app. Two per-delta costs are O(n) in it — the markdown re-parse a
    // newline triggers, and the publish that folds the next delta — so the run
    // is quadratic in its own output. The app that did this held 100% CPU with
    // its memory growing 19 MB a second, and kept spinning after Cancel: the
    // text was already there. So the answer is bounded, at both ends.
    var looping: [AgentEvent] = [
        try ev(#"{"type":"run_started","run_id":"r1"}"#),
        try ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
    ]
    for chunk in 0..<20_000 {
        looping.append(
            try ev(
                #"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"word\#(chunk) \n\n"}"#
            )
        )
    }
    let looped = try fold(looping)
    let kept = assistantText(looped).last ?? ""
    checks.expect(
        kept.utf8.count <= Transcript.answerTrimAt + 200,
        "a 20,000-delta answer is bounded in the transcript: \(kept.utf8.count) chars kept"
    )
    checks.expect(
        kept.hasPrefix("word0 "),
        "the head is kept — an answer is read from the top"
    )
    checks.expect(
        kept.hasSuffix("word19999 \n\n"),
        "the tail is kept — it is what is arriving, and the user watches it move"
    )
    checks.expect(
        kept.contains("truncated: showing"),
        "the line says what it dropped, in the words a tool result already uses"
    )
    // The bound is what the parse pays for: `blocks` is built from the kept text,
    // so a cap that holds keeps the per-delta parse flat.
    let blocks = looped.lines.last { if case .assistant = $0.kind { return true } else { return false } }?.blocks
    checks.expect(
        (blocks?.count ?? 0) > 0 && (blocks?.count ?? 0) < 20_000,
        "the parsed blocks stay bounded with the text (\(blocks?.count ?? 0) blocks)"
    )

    // MARK: the gate, and the seam between the blocks and the tail

    // The cap bounds the answer; the scan is O(n) in it all the same, so it runs
    // once per `Transcript.answerReparseEvery` characters. That leaves a seam: the
    // blocks stop at the last scan, and the answer has grown since. The view draws
    // `blocks` and `tailText` and parses nothing, so between them is the screen —
    // a paragraph in neither is a paragraph nobody sees, and one in both is
    // printed twice. The old view asked `Markdown.split(text).tail` for its half,
    // which is the newest boundary, and that is exactly the seam this breaks.
    var paragraphs: [String] = []
    var streamed: [AgentEvent] = [
        try ev(#"{"type":"run_started","run_id":"r1"}"#),
        try ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
    ]
    for index in 0..<300 {
        let paragraph = "paragraph \(index) is a sentence long enough to matter here"
        paragraphs.append(paragraph)
        streamed.append(
            try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"\#(paragraph)"}"#)
        )
        streamed.append(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"\n\n"}"#))
    }
    // And it stops mid-paragraph, which is where a stream is when it is cancelled.
    streamed.append(
        try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"the last line, still arriving"}"#)
    )
    let mid = try fold(streamed)
    let midLine = mid.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    let onScreen = blocksText(midLine?.blocks ?? []) + "\n" + (midLine?.tailText ?? "")
    let missing = paragraphs.filter { onScreen.range(of: $0) == nil }
    let doubled = paragraphs.filter { p in
        guard let first = onScreen.range(of: p) else { return false }
        return onScreen.range(of: p, range: first.upperBound..<onScreen.endIndex) != nil
    }
    checks.expect(
        missing.isEmpty,
        "every paragraph is on the screen: \(missing.count) missing, first «\(missing.first ?? "")»"
    )
    checks.expect(
        doubled.isEmpty,
        "none of them is drawn twice: \(doubled.count) doubled, first «\(doubled.first ?? "")»"
    )
    checks.expect(
        (midLine?.tailText ?? "").contains("the last line, still arriving"),
        "the tail is the text that is arriving"
    )
    checks.expect(
        blocksText(midLine?.blocks ?? []).contains(paragraphs[0]),
        "the blocks are the answer from the top"
    )
    checks.expect(
        !blocksText(midLine?.blocks ?? []).contains(paragraphs[299]),
        "the blocks stop short of the newest text — that is the gate, not a lost paragraph"
    )
    checks.expect(
        (midLine?.tailText ?? "").contains(paragraphs[295]),
        "the tail carries the paragraphs committed since the last scan, not just the last line"
    )

    // MARK: the bound on what a publish lays out

    // The cap bounds the answer's characters and the gate bounds the scans, and
    // the app *still* wedged on a live looping run: CPU pinned at 100%, RSS
    // climbing 6 MB/s (178 MB → 782 MB in two minutes), `sample` putting the main
    // thread inside SwiftUI's attribute graph with no frame from this codebase in
    // it at all. The unit was wrong. A publish lays the streaming answer out as
    // views — one per block, and one text view per block however long that block
    // is. Measured (`swift run ui-cost`, section 2c): a looping answer with blank
    // lines leaves 2,573 blocks; one without blank lines leaves a single paragraph
    // of 18,005 characters. Both are laid out whole, every 60 ms, for as long as
    // the run does not stop.

    // The 300 paragraphs above, drawn. The bound keeps the end, where the text is
    // arriving, and counts the rest so the row can say what it left out.
    let shown = midLine?.shownBlocks ?? ShownBlocks()
    checks.expect(
        shown.shown.count <= Transcript.answerBlocksShown,
        "a publish lays out at most \(Transcript.answerBlocksShown) blocks (\(shown.shown.count))"
    )
    checks.expect(
        shown.hiddenBlocks == (midLine?.blocks.count ?? 0) - shown.shown.count,
        "the hidden count is the difference and not a guess (\(shown.hiddenBlocks) hidden)"
    )
    // The newest committed block, which is the end of what the reducer has parsed.
    // The newest of the *answer* is in the tail, bounded at its end below.
    let newestCommitted = blocksText(Array((midLine?.blocks ?? []).suffix(1)))
    checks.expect(
        shown.hiddenBlocks > 0,
        "the bound is doing something here: \(shown.hiddenBlocks) blocks hidden"
    )
    checks.expect(
        !newestCommitted.isEmpty && blocksText(shown.shown).contains(newestCommitted),
        "the bound keeps the newest committed block — that is where the user is reading"
    )
    checks.expect(
        !blocksText(shown.shown).contains(paragraphs[0]),
        "and drops the top, which is off the screen anyway"
    )
    checks.expect(
        shown.tail.contains("the last line, still arriving"),
        "the drawn tail keeps the arriving text — that is the end of the answer"
    )
    checks.expect(
        blocksText(midLine?.blocks ?? []).contains(paragraphs[0]),
        "the stored blocks are untouched: the bound is on the drawing, not on the answer"
    )

    // The other shape a loop takes: no blank lines, so one paragraph, and the
    // bound has to cut inside a block, not only between them.
    var oneParagraph: [AgentEvent] = [
        try ev(#"{"type":"run_started","run_id":"r1"}"#),
        try ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
    ]
    for _ in 0..<4_000 {
        oneParagraph.append(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"apple\n"}"#))
    }
    let runOn = try fold(oneParagraph)
    let runOnLine = runOn.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    let shownRunOn = runOnLine?.shownBlocks ?? ShownBlocks()
    checks.expect(
        shownRunOn.shown.count == 1,
        "one paragraph is one block, however long it is (\(shownRunOn.shown.count))"
    )
    checks.expect(
        blocksText(shownRunOn.shown).utf8.count <= Transcript.answerBlockCharsShown,
        "and the drawing of it is bounded: \(blocksText(shownRunOn.shown).utf8.count) characters"
    )
    checks.expect(
        shownRunOn.cutChars > 10_000,
        "the cut is counted, so the row can say what it dropped (\(shownRunOn.cutChars) characters)"
    )

    // The tail is bounded too, at its end.
    //
    // How long the tail can be is not free choice: `Markdown.split` cuts the tail
    // at the last blank line, and the reparse gate moves that boundary every
    // `answerReparseEvery` characters, so a stream that ends each delta with a
    // newline leaves the tail empty — the run-on case above commits its whole
    // paragraph and draws it as a block. The tail is long when the arriving text
    // has no newline in it, which is what word-sized deltas look like. That is
    // the case the bound is for: the tail is the one unit the block bound cannot
    // reach, because it is not in `blocks`.
    var noNewline: [AgentEvent] = [
        try ev(#"{"type":"run_started","run_id":"r1"}"#),
        try ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
    ]
    for _ in 0..<3_000 {
        noNewline.append(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"apple"}"#))
    }
    let streamFold = try fold(noNewline)
    let streamLine = streamFold.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    let shownStream = streamLine?.shownBlocks ?? ShownBlocks()
    checks.expect(
        (streamLine?.tailText ?? "").utf8.count > 10_000,
        "a stream with no newline in it is all tail: \((streamLine?.tailText ?? "").utf8.count) characters"
    )
    checks.expect(
        shownStream.tail.utf8.count == Transcript.answerBlockCharsShown,
        "and the bound bites: \(shownStream.tail.utf8.count) characters drawn"
    )
    checks.expect(
        shownStream.tailHidden == (streamLine?.tailText ?? "").utf8.count - shownStream.tail.utf8.count,
        "and its hidden part is counted: \(shownStream.tailHidden) characters"
    )
    checks.expect(
        shownStream.tail.hasSuffix("apple"),
        "the tail keeps its end, where the text is arriving"
    )
    checks.expect(
        (streamLine?.tailText ?? "").utf8.count > shownStream.tail.utf8.count,
        "the stored tail is untouched: the bound is on the drawing"
    )
    checks.expect(
        !shownStream.isEmpty,
        "a long tail alone is a drawn row — the bound leaves something to show"
    )

    // The other shape: committed blocks *and* a long tail, which is what a loop
    // with blank lines looks like once it is past the scan gate. Both bounds have
    // to hold at once.
    var longTail: [AgentEvent] = [
        try ev(#"{"type":"run_started","run_id":"r1"}"#),
        try ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"a paragraph, closed.\n\n"}"#),
    ]
    for _ in 0..<3_000 {
        longTail.append(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"apple"}"#))
    }
    let tailFold = try fold(longTail)
    let tailLine = tailFold.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    let shownTail = tailLine?.shownBlocks ?? ShownBlocks()
    checks.expect(
        shownTail.tail.utf8.count <= Transcript.answerBlockCharsShown,
        "blocks and tail are bounded together: \(shownTail.tail.utf8.count) tail characters"
    )
    checks.expect(
        shownTail.tailHidden > 10_000,
        "with committed blocks behind it, the tail is still cut (\(shownTail.tailHidden) hidden)"
    )
    checks.expect(
        shownTail.shown.contains { blocksText([$0]).contains("a paragraph, closed.") },
        "the block bound keeps its own end, independent of the tail"
    )

    // A finished answer is bounded too, and the row says what it left out.
    //
    // "The bound is for the stream, not the product" was the rule. The live app
    // answered it: a looping run leaves a 20,000-character answer in the
    // transcript, that row is in the viewport, and every publish lays out all
    // 2,573 of its blocks — 100% CPU, 800 MB, an AX walk that times out, and a
    // Stop click that is never delivered. So the row draws the end of the answer
    // and puts the rest behind a button the user presses on purpose.
    //
    // What is *stored* stays the whole answer: the cap is for the stream, and the
    // completed event is authoritative.
    // Both shapes at once: paragraphs, which are blocks, and a run-on ending,
    // which is a tail — so the bound cuts on both axes and the row has to name
    // both. The deltas stream the paragraphs; the completed event carries the
    // whole answer, tail included, because it is the authoritative one.
    let longParagraphs = (0..<4_000).map { "paragraph \($0)" }
    let runOnTail = Array(repeating: "tailword", count: 3_000).joined(separator: " ")
    let longAnswer = longParagraphs.joined(separator: "\n\n") + "\n\n" + runOnTail
    var longRun: [AgentEvent] = [
        try ev(#"{"type":"run_started","run_id":"r1"}"#),
        try ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
    ]
    for p in longParagraphs {
        longRun.append(try ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"\#(p)\n\n"}"#))
    }
    longRun.append(
        try ev(
            #"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":\#(jsonLiteral(longAnswer))}"#
        )
    )
    let longFinished = try fold(longRun)
    let longLine = longFinished.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    let shownLong = longLine?.shownBlocks ?? ShownBlocks()
    checks.expect(
        (longLine?.blocks.count ?? 0) > Transcript.answerBlocksShown,
        "the completed answer is parsed once, by the reducer: \(longLine?.blocks.count ?? 0) blocks"
    )
    checks.expect(
        shownLong.shown.count <= Transcript.answerBlocksShown,
        "a finished answer is drawn bounded too: \(shownLong.shown.count) of \(longLine?.blocks.count ?? 0) blocks"
    )
    checks.expect(
        shownLong.blocksTotal == (longLine?.blocks.count ?? 0) && shownLong.blocksTotal > 0,
        "the row is told the total, so its button can name it: \(shownLong.blocksTotal)"
    )
    checks.expect(
        shownLong.hiddenBlocks == (longLine?.blocks.count ?? 0) - shownLong.shown.count,
        "and the row says what it left out: \(shownLong.hiddenBlocks) blocks"
    )
    // A finished answer has no tail: the whole text is parsed, so the run-on
    // ending is a block like any other, and the bound reaches it as a cut.
    checks.expect(
        (longLine?.tailText ?? "").isEmpty && shownLong.tail.isEmpty,
        "a finished answer's last block is not a tail: \(longLine?.tailText ?? "")"
    )
    checks.expect(
        shownLong.cutChars > 10_000,
        "the run-on ending is bounded as a cut block: \(shownLong.cutChars) characters"
    )
    checks.expect(
        blocksText(shownLong.shown).contains(longParagraphs[longParagraphs.count - 1]),
        "the drawn blocks reach the last paragraph: \(longParagraphs[longParagraphs.count - 1])"
    )
    checks.expect(
        blocksText(shownLong.shown).contains("tailword"),
        "and the run-on ending is markdown, drawn as a block"
    )
    var storedLong = ""
    if case .assistant(let t) = longLine?.kind { storedLong = t }
    checks.expect(
        storedLong.utf8.count == longAnswer.utf8.count,
        "the stored answer is the whole answer, cap and all: \(storedLong.utf8.count) of \(longAnswer.utf8.count)"
    )

    // The common case is a short answer, and it is drawn whole: the bound leaves
    // nothing out, so the row shows no marker and no button.
    let finished = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"a whole answer, in full"}"#),
    ])
    let finishedLine = finished.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    let shownFinished = finishedLine?.shownBlocks ?? ShownBlocks()
    checks.expect(
        shownFinished.shown.count == (finishedLine?.blocks.count ?? 0) && shownFinished.hiddenBlocks == 0,
        "a short finished answer is drawn whole: \(shownFinished.shown.count) blocks, nothing hidden"
    )
    checks.expect(
        !shownFinished.isEmpty,
        "and it is drawn from the parsed blocks, not by parsing on a render"
    )
    checks.expect(
        shownFinished.hiddenSummary.isEmpty && shownFinished.revealLabel.isEmpty,
        "an answer the bound did not cut says nothing and offers nothing"
    )

    // The words the row prints. It has to say what is missing twice — above the
    // blocks, and on the button that lifts the bound — and the two have to agree,
    // so they are computed in the core. The live app got this wrong first: the
    // run-on answer has no blank lines, so it has no blocks and 20,000 characters
    // of tail, and the row read "Show all 0 blocks" under it.
    checks.expect(
        shownStream.hiddenSummary == "\(shownStream.tailHidden) characters",
        "a run-on answer is counted in characters, not blocks: \(shownStream.hiddenSummary)"
    )
    checks.expect(
        shownStream.revealLabel == "Show all \((streamLine?.tailText ?? "").utf8.count) characters",
        "and the button names what it adds: \(shownStream.revealLabel)"
    )
    checks.expect(
        shownLong.hiddenSummary.hasPrefix("\(shownLong.hiddenBlocks) blocks"),
        "a paragraphed answer is counted in blocks: \(shownLong.hiddenSummary)"
    )
    checks.expect(
        shownLong.revealLabel == "Show all \(shownLong.blocksTotal) blocks and \(shownLong.cutChars) characters",
        "and its button names both: \(shownLong.revealLabel)"
    )

    // The bug this pins. A model writes its summary table last and omits the
    // blank line after it — that is what "last" means — so `Markdown.split` put
    // the table in the tail, the view draws the tail as plain text, and the
    // finished answer reached the screen as `| file | lines |`. The live app
    // showed exactly that. A finished answer parses whole, so its last block is
    // markdown and there is no tail to draw.
    let tableAnswer = "| file | lines |\n| --- | ---: |\n| Markdown.swift | 240 |\n| RootView.swift | 1,208 |"
    let tableRun = try fold([
        ev(#"{"type":"run_started","run_id":"r1"}"#),
        ev(#"{"type":"message_started","run_id":"r1","message_id":"m1"}"#),
        ev(#"{"type":"message_delta","run_id":"r1","message_id":"m1","delta":"| file | lines |\n"}"#),
        ev(#"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":\#(jsonLiteral(tableAnswer))}"#),
    ])
    let tableLine = tableRun.lines.last { if case .assistant = $0.kind { return true } else { return false } }
    checks.expect(
        tableLine?.blocks.last.map { if case .table = $0 { return true } else { return false } } ?? false,
        "a finished answer's last block is a table, not a tail: \(tableLine?.blocks.last.map { blocksText([$0]) } ?? "nil")"
    )
    checks.expect(
        (tableLine?.tailText ?? "").isEmpty,
        "and nothing is left in the tail: \(tableLine?.tailText ?? "")"
    )
    checks.expect(
        blocksText(tableLine?.blocks ?? []).contains("1,208"),
        "every row of the table reaches the blocks the view draws"
    )
}

/// The text a block shows, for the checks that ask what is on the screen.
private func blockText(_ block: MarkdownBlock) -> String {
    switch block {
    case .paragraph(let text): return text
    case .heading(_, let text): return text
    case .code(_, let text): return text
    case .bullets(let items): return items.joined(separator: "\n")
    case .numbered(let items): return items.map(\.text).joined(separator: "\n")
    case .quote(let lines): return lines.joined(separator: "\n")
    case .table(let rows, _): return rows.map { $0.joined(separator: " ") }.joined(separator: "\n")
    case .rule: return "---"
    }
}

private func blocksText(_ blocks: [MarkdownBlock]) -> String {
    blocks.map(blockText).joined(separator: "\n")
}

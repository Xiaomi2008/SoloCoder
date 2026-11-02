import AgentAppCore
import AgentClient
import Foundation

// The row labels, the durations, and the turn grouping.
//
// These are the three decisions the timeline makes about the event stream, and
// all three are folds the screen cannot see: a row's one-line label, the
// distance between two arrivals, and which lines belong to the prompt above
// them. Each is a place where a plausible implementation is quietly wrong:
//
// * a label built from the result shows output the disclosure shows again, and a
//   label built from a 4,000-character command is not a line;
// * a duration read off the wall clock is a number that changes when you ask
//   again, and a session loaded from disk has no arrivals to measure, so "0s"
//   is a lie the row tells;
// * a turn built by sorting lines by timestamp shuffles a loaded history, where
//   every stamp is the same instant.
//
// The clock is injected, so the timing checks assert exact numbers instead of
// sleeping for them.

private final class SteppingClock: @unchecked Sendable {
    var now = Date(timeIntervalSince1970: 1_700_000_000)
    func advance(_ seconds: TimeInterval) { now.addTimeInterval(seconds) }
}

private func label(_ tool: String, _ fields: [String: JSONValue], running: Bool = false) -> String {
    ToolDisplay.summary(tool: tool, arguments: .object(fields), running: running)
}

private func s(_ text: String) -> JSONValue { .string(text) }

/// The text of a turn's prompt, and nil for a turn with none.
private func promptText(_ line: TranscriptLine?) -> String? {
    guard case .user(let text)? = line?.kind else { return nil }
    return text
}

func rowChecks(_ checks: Checks) throws {
    print("row labels, durations, turns")

    // MARK: the row's verb and object

    checks.equal(label("bash", ["command": s("npm test")]), "Ran npm test", "a finished bash is past tense")
    checks.equal(
        label("bash", ["command": s("npm test")], running: true), "Running npm test",
        "a running one is present tense — the row says what is happening"
    )
    checks.equal(label("read", ["path": s("/tmp/a.swift")]), "Read /tmp/a.swift", "read names the file")
    checks.equal(
        label("edit", ["path": s("/tmp/a.swift")], running: true), "Editing /tmp/a.swift",
        "edit is its own verb, not 'write'"
    )
    checks.equal(
        label("grep", ["pattern": s("TODO"), "path": s("/tmp/src")]), "Searched “TODO” in /tmp/src",
        "a search names the pattern and where"
    )
    checks.equal(
        label("web_search", ["query": s("swift concurrency")]), "Searched the web for “swift concurrency”",
        "a web search reads as a sentence"
    )
    checks.equal(
        label("ask_user_question", ["question": s("Which target?\nbuild or run")]), "Asked “Which target?”",
        "a question is its first line — the row is one line"
    )
    checks.equal(label("todo_write", [:]), "Updated the task list", "a task list has no object worth naming")
    checks.equal(
        label("click", ["x": .int(12), "y": .int(30)]), "Clicked (12, 30)",
        "a click names the coordinates the user is looking at"
    )
    checks.equal(label("frobnicate", ["anything": s("x")]), "Ran frobnicate", "an unknown tool names itself")
    checks.equal(label("read", [:]), "Read", "a call with no path is the verb alone, not 'Read nil'")

    // A command of several lines is one line of label.
    checks.equal(
        label("bash", ["command": s("set -e\nnpm test\nnpm run build")]), "Ran set -e",
        "a multi-line command contributes its first line"
    )
    let long = label("bash", ["command": s(String(repeating: "x", count: 4_000))])
    checks.expect(long.count <= ToolDisplay.summaryKeep, "a 4,000-character command is bounded (\(long.count))")
    checks.expect(long.hasSuffix("…"), "…and the cut is visible")
    checks.expect(!long.contains("\n"), "a label is one line")

    // The home directory is noise in front of a path, and the tail is the part
    // that identifies the file.
    let home = NSHomeDirectory()
    checks.equal(
        label("read", ["path": s("\(home)/Projects/SoloCoder/cli_coder.py")]),
        "Read ~/Projects/SoloCoder/cli_coder.py",
        "a path under the home is abbreviated to ~"
    )
    // A path that does not fit is cut from the front, so what survives is the
    // file — the part that identifies it.
    let deep = label("read", ["path": s("\(home)/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/deepfile.py")])
    checks.expect(deep.hasPrefix("Read …"), "a path too long for the row is cut, and says so: \(deep)")
    checks.expect(deep.hasSuffix("deepfile.py"), "…cut from the front, keeping the file")
    checks.expect(deep.count <= ToolDisplay.summaryKeep, "…and the whole label stays bounded")

    // MARK: the duration in words

    checks.equal(DurationFormat.short(12), "12s", "seconds under a minute")
    checks.equal(DurationFormat.short(59.6), "1m", "59.6 s is a minute, not '60s'")
    checks.equal(DurationFormat.short(90), "1m 30s", "two units at most")
    checks.equal(DurationFormat.short(120), "2m", "and no '2m 0s' — that reads as a broken clock")
    checks.equal(DurationFormat.short(3_900), "1h 5m", "hours and minutes")
    checks.equal(DurationFormat.short(0.4), nil, "under half a second is not a duration worth showing")
    checks.equal(DurationFormat.short(nil), nil, "no interval, no words")
    checks.equal(DurationFormat.short(-3), nil, "a negative interval is not a duration")
    let t0 = Date(timeIntervalSince1970: 100)
    checks.equal(DurationFormat.between(t0, t0.addingTimeInterval(30)), 30, "two arrivals, 30 s apart")
    checks.equal(DurationFormat.between(t0, t0), 0, "the same instant is 0, and the label drops it")
    checks.equal(DurationFormat.between(nil, t0), nil, "a missing arrival is no interval")

    // MARK: a tool call's own clock

    let clock = SteppingClock()
    var transcript = Transcript(clock: { clock.now })
    transcript.addUserMessage("run the tests")
    clock.advance(2)
    transcript.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"tool_call_started","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"bash","arguments":{"command":"npm test"}}"#
                    .utf8
            )
        )
    )
    let runningRow = transcript.toolRows.first
    checks.expect(runningRow?.elapsed == nil, "a running row has no duration yet — the view counts it live")
    clock.advance(12)
    transcript.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"bash","result":"34 passed"}"#
                    .utf8
            )
        )
    )
    let row = transcript.toolRows.first
    checks.equal(row?.elapsed, 12, "the call took 12 s, measured from the stamps and not the wall clock")
    checks.equal(
        DurationFormat.short(row?.elapsed), "12s",
        "…and the row shows it as 12s"
    )
    clock.advance(8)
    transcript.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"run_completed","run_id":"r1","final_message_id":"m1","result":{"run_id":"r1","final_message_id":"m1","output_text":"34 passed","stop_reason":"end_turn"}}"#
                    .utf8
            )
        )
    )
    let turn = transcript.turns.first
    checks.equal(turn?.elapsed, 22, "the turn spans prompt to last arrival: 2 + 12 + 8")
    checks.equal(turn?.activityLabel, "Worked for 22s", "the activity line says what the clock says")
    checks.equal(turn?.running, false, "a finished turn is not working")

    // A result that arrived with no start: the row is done, the duration unknown.
    var orphan = Transcript()
    orphan.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t9","tool_name":"bash","result":"ok"}"#
                    .utf8
            )
        )
    )
    checks.equal(orphan.toolRows.first?.elapsed, nil, "a call that never started has no duration to claim")

    // MARK: turns

    var two = Transcript()
    two.addUserMessage("first")
    two.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"one"}"#.utf8
            )
        )
    )
    two.addUserMessage("second")
    two.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"tool_call_started","run_id":"r2","message_id":"m2","tool_call_id":"t1","tool_name":"read","arguments":{"path":"/tmp/a"}}"#
                    .utf8
            )
        )
    )
    checks.equal(two.turns.count, 2, "a prompt opens a turn")
    checks.equal(promptText(two.turns.last?.user), "second", "…named by its prompt")
    checks.equal(two.turns.first?.answers.count, 1, "the answer belongs to the prompt it answers")
    checks.equal(two.turns.first?.activity.count, 0, "…and an answer is not activity")
    checks.equal(two.turns.last?.activity.count, 1, "the tool call of the second prompt is its activity")
    checks.equal(two.turns.last?.answers.count, 0, "…and it has no answer yet")

    // A stream with no prompt in front of it still has to render.
    var headless = Transcript()
    headless.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"no prompt"}"#.utf8
            )
        )
    )
    checks.equal(headless.turns.count, 1, "a run with no prompt is one turn")
    checks.equal(headless.turns.first?.user, nil, "…with no prompt")
    checks.equal(headless.turns.first?.answers.count, 1, "…and the answer inside it")

    // MARK: the history that has no clock

    // A session loaded from disk: every line arrives at one instant. Sorting by
    // timestamp here is a sort over equal keys, and the history renders shuffled.
    var loaded = Transcript(clock: { Date(timeIntervalSince1970: 1_700_000_000) })
    loaded.addUserMessage("history")
    loaded.addAssistantMessage("answer one")
    loaded.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"tool_call_started","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"read","arguments":{"path":"/tmp/a"}}"#
                    .utf8
            )
        )
    )
    loaded.addAssistantMessage("answer two")
    loaded.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"read","result":"a"}"#
                    .utf8
            )
        )
    )
    let history = loaded.turns.first
    checks.equal(
        history?.lines.compactMap { if case .assistant(let text) = $0.kind { return text } else { return nil } },
        ["answer one", "answer two"],
        "a loaded history keeps its arrival order — the stamps are equal, so nothing may be sorted by them"
    )
    checks.equal(history?.elapsed, 0, "…and the clock says nothing happened")
    checks.equal(history?.activityLabel, "Worked", "…so the row says 'Worked' and leaves the number out")
    checks.equal(history?.running, false, "…and a finished history is not 'Working'")

    // The thinking's duration: the protocol never says it stopped, so the
    // distance is to the next arrival.
    let clocked = SteppingClock()
    var thought = Transcript(clock: { clocked.now })
    thought.addUserMessage("think about it")
    clocked.advance(3)
    thought.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"reasoning_delta","run_id":"r1","message_id":"m1","delta":"let me see "}"#.utf8
            )
        )
    )
    clocked.advance(9)
    thought.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"done"}"#.utf8
            )
        )
    )
    let thinkingLine = thought.turns.first?.activity.first
    checks.equal(
        DurationFormat.short(thought.turns.first?.durations[thinkingLine!.id]),
        "9s",
        "the thinking is measured to the answer it produced"
    )
    checks.equal(
        thought.turns.first?.durations[thought.turns.first!.answers.first!.id],
        nil,
        "the turn's last line has no duration: nothing arrived after it"
    )

    // A failure is a result, not a detail.
    var failed = Transcript()
    failed.addUserMessage("do it")
    failed.apply(
        try AgentEvent.decode(
            from: Data(
                #"{"type":"message_failed","run_id":"r1","message_id":"m1","error":"provider timeout"}"#.utf8
            )
        )
    )
    checks.equal(failed.turns.first?.activity.count, 0, "a failed message is not hidden inside 'Worked'")
    checks.equal(failed.turns.first?.answers.count, 1, "…it is shown as the turn's result")
    checks.equal(failed.turns.first?.result.count, 1, "…and the timeline ends on it too")

    // MARK: steps and result

    func event(_ json: String) throws -> AgentEvent { try AgentEvent.decode(from: Data(json.utf8)) }
    func texts(_ lines: [TranscriptLine]?) -> [String] {
        (lines ?? []).map {
            switch $0.kind {
            case .assistant(let text): return "say:\(text)"
            case .reasoning: return "think"
            case .tool(let row): return "tool:\(row.name)"
            default: return "other"
            }
        }
    }

    // A run that talks between its tool calls: the prose is a step, in place,
    // and only the last answer is the result. The old split drew both answers
    // under both tool calls.
    var talky = Transcript()
    talky.addUserMessage("fix it")
    for json in [
        #"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"Let me read it."}"#,
        #"{"type":"tool_call_started","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"read","arguments":{"path":"/tmp/a"}}"#,
        #"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"read","result":"a"}"#,
        #"{"type":"reasoning_delta","run_id":"r1","message_id":"m2","delta":"so the fix is"}"#,
        #"{"type":"message_completed","run_id":"r1","message_id":"m2","output_text":"Fixed."}"#,
        #"{"type":"run_completed","run_id":"r1","final_message_id":"m2","result":{"run_id":"r1","final_message_id":"m2","output_text":"Fixed.","stop_reason":"end_turn"}}"#,
    ] { talky.apply(try event(json)) }
    checks.equal(
        texts(talky.turns.first?.steps), ["say:Let me read it.", "tool:read", "think"],
        "the steps are in arrival order, the prose between tool calls included"
    )
    checks.equal(texts(talky.turns.first?.result), ["say:Fixed."], "the result is the answer the run ended on")

    // An answer followed by a closing tool call: the answer is still the
    // result, not folded away behind "Worked".
    var closing = Transcript()
    closing.addUserMessage("do it")
    for json in [
        #"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"Done."}"#,
        #"{"type":"tool_call_started","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"todo_update","arguments":{}}"#,
        #"{"type":"tool_call_completed","run_id":"r1","message_id":"m1","tool_call_id":"t1","tool_name":"todo_update","result":"ok"}"#,
        #"{"type":"run_completed","run_id":"r1","final_message_id":"m1","result":{"run_id":"r1","final_message_id":"m1","output_text":"Done.","stop_reason":"end_turn"}}"#,
    ] { closing.apply(try event(json)) }
    checks.equal(texts(closing.turns.first?.result), ["say:Done."], "a finished run's last answer stays the result")
    checks.equal(texts(closing.turns.first?.steps), ["tool:todo_update"], "…and the closing call is a step")

    // Mid-run, the model has moved on from its earlier answer: it is a step,
    // so the new thinking is drawn after it, not above it.
    var moving = Transcript()
    moving.addUserMessage("go")
    for json in [
        #"{"type":"run_started","run_id":"r1"}"#,
        #"{"type":"message_completed","run_id":"r1","message_id":"m1","output_text":"First pass."}"#,
        #"{"type":"message_started","run_id":"r1","message_id":"m2"}"#,
        #"{"type":"reasoning_delta","run_id":"r1","message_id":"m2","delta":"now the second"}"#,
    ] { moving.apply(try event(json)) }
    checks.expect(moving.turns.first?.running == true, "the run is still going")
    checks.equal(moving.turns.first?.result.count, 0, "mid-run, an answer with work after it is not the result")
    checks.equal(
        Array(texts(moving.turns.first?.steps).prefix(2)), ["say:First pass.", "think"],
        "…it is a step, before the thinking that came after it"
    )

    checks.equal(ToolDisplay.symbol(tool: "read"), "doc.text", "a step's marker says what kind of tool ran")
    checks.equal(ToolDisplay.symbol(tool: "frobnicate"), "wrench.and.screwdriver", "…and an unknown tool still gets one")
    checks.equal(label("git_status", [:]), "Checked git status", "git tools have labels of their own")
}

// MARK: - Sessions
//
// The sidebar names a session and removes it, and both are decisions: what a
// row is called, what it answers to in the search box, and when delete is
// offered at all. A view cannot be asked these questions, so they are here.

func sessionChecks(_ checks: Checks) throws {
    let named = SessionRow(
        sessionId: "sess_abc123", path: "/home/u/.openagent/sessions/sess_abc123.json",
        updatedAt: 1_700_000_000, messageCount: 3, title: "fix the flaky test in test_agent.py"
    )
    checks.equal(
        SessionTitle.label(named), "fix the flaky test in test_agent.py",
        "a titled row is named by its title"
    )

    let legacy = SessionRow(
        sessionId: "sess_legacy", path: "/x", updatedAt: 0, messageCount: 3
    )
    checks.equal(
        SessionTitle.label(legacy), "sess_legacy",
        "a file written before titles existed is named by its id — the string `--session` takes"
    )

    let damaged = SessionRow(
        sessionId: "sess_bad", path: "/x", updatedAt: 0, messageCount: 0,
        error: "unreadable session file"
    )
    checks.equal(
        SessionTitle.label(damaged), "Damaged session",
        "the label says what the row's warning glyph means, because the row is also how you delete the file"
    )

    let multiline = SessionRow(
        sessionId: "s", path: "/x", updatedAt: 0, messageCount: 0, title: "first line\nsecond line"
    )
    checks.equal(SessionTitle.label(multiline), "first line", "a sidebar row is one line")

    let blank = SessionRow(sessionId: "sess_blank", path: "/x", updatedAt: 0, messageCount: 0, title: "   ")
    checks.equal(SessionTitle.label(blank), "sess_blank", "a blank title is no title")

    // The search box: the name, the id and the folder, case-insensitively.
    checks.expect(SessionTitle.matches(named, query: "flaky"), "the name is searchable")
    checks.expect(SessionTitle.matches(named, query: " SESS_ABC "), "the id is searchable, trimmed and case-free")
    checks.expect(SessionTitle.matches(named, query: ".openagent"), "the folder is searchable")
    checks.expect(SessionTitle.matches(named, query: ""), "an empty query filters nothing")
    checks.expect(!SessionTitle.matches(named, query: "a conversation about fonts"), "a miss is a miss")

    // The wire shape. The server's snake_case names are the contract, so this
    // decoder mirrors `wireDecoder`: a property that drifts from them fails
    // here, not as a sidebar with no names in it.
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    let row = try decoder.decode(
        SessionRow.self,
        from: Data(
            """
            {"session_id":"sess_x","path":"/x/sess_x.json","updated_at":1.5,"message_count":3,\
            "title":"fix the flaky test"}
            """.utf8
        )
    )
    checks.equal(row.title, "fix the flaky test", "the title decodes from the server's name")
    checks.equal(row.updatedAt, 1.5, "…and the row is the rest of the row")

    let preTitle = try decoder.decode(
        SessionRow.self,
        from: Data(#"{"session_id":"sess_y","path":"/y","updated_at":1,"message_count":0}"#.utf8)
    )
    checks.equal(preTitle.title, nil, "a file from before titles decodes with no title, not a failure")

    // Delete: the app refuses the session it is writing, because the run saves
    // its session when it ends and a file deleted now is written back a second
    // later. The server's `session_busy` is the guarantee; this is the courtesy.
    checks.expect(
        !SessionDeletion.isOffered("sess_live", openSessionId: "sess_live", running: true),
        "the session being written is not offered for deletion"
    )
    checks.expect(
        SessionDeletion.isOffered("sess_other", openSessionId: "sess_live", running: true),
        "…and the rest of them are: a run on one session does not freeze the sidebar"
    )
    checks.expect(
        SessionDeletion.isOffered("sess_live", openSessionId: "sess_live", running: false),
        "…and it is offered again when the run ends"
    )
    checks.expect(
        SessionDeletion.isOffered("sess_live", openSessionId: nil, running: true),
        "a run on an unnamed new session does not lock a stored row"
    )

    // The line that confirms a resume names the session the way the sidebar
    // names it. It used to quote the id — "resumed sess_31cd4c9ed29e412e91adcf4f
    // — the next run continues it" — under a row the user had just clicked by
    // its title, which confirms a thing they never saw.
    checks.equal(
        AppModel.resumedNotice(sessionId: named.sessionId, row: named),
        "resumed fix the flaky test in test_agent.py — the next run continues it",
        "the line repeats the name the user clicked"
    )
    checks.equal(
        AppModel.resumedNotice(sessionId: legacy.sessionId, row: legacy),
        "resumed sess_legacy — the next run continues it",
        "a session with no title is named by its id, the string `--session` takes"
    )
    checks.equal(
        AppModel.resumedNotice(sessionId: "sess_not_in_the_sidebar", row: nil),
        "resumed sess_not_in_the_sidebar — the next run continues it",
        "a session the sidebar has no row for falls back to the id"
    )
}

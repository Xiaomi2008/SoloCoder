import AgentClient
import Foundation

// The state a chat UI renders, derived from the event stream.
//
// This is the piece of the app that can be tested without a screen, and it is
// where every awkward property of the protocol lands:
//
// * **Deltas are best-effort; `message_completed` is authoritative.** A stream
//   that dies mid-message leaves partial text; the completed event carries the
//   full text. The reducer appends deltas and then *replaces* with the final
//   text, so a UI never shows a message that is 90% of what the model said.
// * **Deltas are addressed by `message_id`, not "the last line".** A run makes
//   several assistant turns around each tool call. Appending to "the current
//   line" merges them into one blob.
// * **The user's own message is not in the stream.** The server has no reason
//   to echo what it was told; the client inserts it when it starts the run.
// * **A denied tool emits no `tool_call_started`.** The refusal is a tool result
//   for the model, not a crash, so the only trace in the stream is
//   `permission_requested` → `permission_resolved(granted: false)`. The line
//   for that tool has to come from the permission, not from a tool event.
// * **`permission_requested` carries no `tool_call_id`.** It correlates by
//   `request_id`, and the tool it is about is identified by name and arguments.
// * **A terminal event ends the run exactly once.** A late event after a cancel
//   must not resurrect a finished run.

// MARK: - Rows

/// A tool call in the timeline.
///
/// Not `ToolRow`: `AgentClient.ToolRow` is a `tool.list` row (a tool the service
/// offers), this is one call the model made. Two nouns for two things, so a
/// view that imports both does not have to qualify every use.
public struct ToolCallRow: Identifiable, Equatable, Sendable {
    public enum State: Equatable, Sendable {
        case running
        case done(String)
        case failed(String)
    }

    public let id: String
    public var name: String
    public var arguments: JSONValue
    public var state: State
    /// When the call started, and when it stopped.
    ///
    /// The row shows the elapsed time while it runs and after it ends: a tool row
    /// with no clock is a row that cannot tell a 2-second `read` from a 90-second
    /// `bash` that is still going, which is the difference between "working" and
    /// "stuck". `endedAt` is nil while running, and nil for a row folded from a
    /// history that has no arrival times.
    public var startedAt: Date
    public var endedAt: Date?

    public init(
        id: String, name: String, arguments: JSONValue, state: State = .running,
        startedAt: Date = Date(), endedAt: Date? = nil
    ) {
        self.id = id
        self.name = name
        self.arguments = arguments
        self.state = state
        self.startedAt = startedAt
        self.endedAt = endedAt
    }

    /// How long the call took, once it stopped.
    ///
    /// Nil while it runs. A running row's live count is the view's — a
    /// `TimelineView` ticking once a second from `startedAt` — and not this
    /// property's: reading the wall clock here would make the row's duration
    /// depend on when it was asked, and on a clock the transcript was not
    /// stamped with.
    public var elapsed: TimeInterval? {
        DurationFormat.between(startedAt, endedAt)
    }
}

public struct PermissionRequest: Identifiable, Equatable, Sendable {
    public let id: String
    public var toolName: String
    public var arguments: JSONValue
    /// The server's own words about why this tool is risky. It is the only
    /// explanation the user gets, so the sheet shows it verbatim.
    public var reason: String
}

public struct PermissionOutcome: Equatable, Sendable {
    public var request: PermissionRequest
    public var granted: Bool
    public var scope: PermissionScope
    public var reason: String
}

public struct CompactionRow: Identifiable, Equatable, Sendable {
    public enum State: Equatable, Sendable {
        case running
        case done
        case failed(String)
    }

    public let id: String
    public var reason: String
    public var state: State
}

public struct TranscriptLine: Identifiable, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case user(String)
        case assistant(String)
        /// The model's private thinking, shown as progress.
        ///
        /// Kept apart from `assistant` on purpose: it is not the answer, it
        /// never joins the answer's text, and it is never sent back to the
        /// model. Measured on a reasoning model, ~1,900 reasoning tokens arrive
        /// before the first answer character — a client that drops this shows a
        /// spinner that has not moved for ten seconds.
        case reasoning(String)
        case messageFailed(String)
        case tool(ToolCallRow)
        case permission(PermissionOutcome)
        case compaction(CompactionRow)
        /// An event this client does not model, kept visible. A newer server
        /// must leave a trace in the timeline, not a silent gap.
        case note(String)
    }

    public let id: String
    public var kind: Kind
    /// The message this line belongs to, for delta routing.
    public var messageId: String?
    /// True while the text is still arriving.
    ///
    /// The view turns text selection off while streaming: a selectable `Text`
    /// rebuilds its text-kit view on every character, and the model streams ~1.5
    /// characters per chunk, so a 2k-character answer is 1300 rebuilds of a view
    /// that cannot be laid out incrementally.
    public var streaming: Bool = false
    /// The blocks of an answer that are already finished, parsed while it streams.
    ///
    /// The cache exists so the view parses nothing: `Markdown.split` says which
    /// part of a streaming answer is committed structure, and this is that part
    /// parsed, refreshed only when the boundary moves — once per block, not once
    /// per character. The tail, the block still being written, stays plain text
    /// until its blank line arrives.
    ///
    /// Empty for a finished line, which the view parses whole.
    public var blocks: [MarkdownBlock] = []
    /// The text `blocks` was parsed from, so the parse runs when it changes.
    ///
    /// Not `private`: a private stored property makes the memberwise init private,
    /// and this struct is constructed by name from all over the reducer.
    var parsedPrefix: String = ""
    /// When the line arrived.
    ///
    /// The turn's "Worked for 2m 50s" is the distance between the line that opened
    /// it and the line that closed it, so the arrival times have to be on the
    /// lines. A history loaded from disk stamps every line with the same instant,
    /// and `DurationFormat` answers nil for that interval — the row then says
    /// "Worked" and leaves the number out, which is the truth.
    public var at: Date = Date()

    /// True for the process, false for the result.
    ///
    /// What the "Worked for 2m 50s ›" disclosure collects, and what stays in the
    /// column: thinking, tool calls, compactions, permission decisions and events
    /// this client does not model are the process. The prompt, the answers and a
    /// failed message are the result — a failure is a result, because an error
    /// inside a collapsed disclosure is an error the user never sees.
    public var isActivity: Bool {
        switch kind {
        case .reasoning, .tool, .compaction, .permission, .note: return true
        case .user, .assistant, .messageFailed: return false
        }
    }

    /// The last instant this line speaks about.
    ///
    /// For most lines, the arrival. For a tool call, the moment the call stopped
    /// when that is later: a 90-second `bash` arrives when it starts, and a turn
    /// that ends its clock at the arrival says "Worked for 2s" about a run that
    /// took a minute and a half.
    public var stoppedAt: Date {
        guard case .tool(let row) = kind, let ended = row.endedAt, ended > at else { return at }
        return ended
    }

    /// Fold a delta into the answer, and re-parse the part of it that is finished.
    mutating func appendAnswerDelta(_ delta: String) {
        guard case .assistant(let accumulated) = kind else { return }
        // The split can only move when a newline arrives, or when the text ended on
        // a blank line that this delta is filling in — a trailing blank line stops
        // being blank the moment a character lands on it. Everything else leaves the
        // finished prefix exactly where it was, and the scan is O(n) in an answer
        // that grows per delta: ungated, a 20k-character answer costs 470 ms of
        // main-actor work spread across the run, and an 80k one costs 7.4 s.
        let canMove = delta.contains("\n") || accumulated.hasSuffix("\n")
        var text = accumulated
        text.append(delta)
        kind = .assistant(text)
        guard canMove else { return }
        let (complete, _) = Markdown.split(text)
        guard complete != parsedPrefix else { return }
        parsedPrefix = complete
        blocks = Markdown.blocks(complete)
    }
}

/// One prompt and everything the run did about it.
///
/// The transcript is a flat event log; a person reads it as a question and the
/// work that answered it. The split is the one the screen makes: the prompt, the
/// answer, and in between everything that happened on the way — thinking, tool
/// calls, compactions, permission decisions — which is collapsed behind
/// "Worked for 2m 50s ›" because it is the process, not the result.
///
/// The grouping rule is the user line: it opens a turn, and the next one closes
/// it. That makes the rule the protocol's own — `run.start` is one prompt — and
/// not a guess over event types. A history that opens with an answer, with no
/// prompt before it, is a turn with `user == nil`.
public struct Turn: Identifiable, Equatable, Sendable {
    public let id: String
    public var user: TranscriptLine?
    /// Everything after the prompt, in arrival order.
    ///
    /// Arrival order and nothing else. The activity and the answers interleave —
    /// a tool call lands between two answers — and a session loaded from disk
    /// stamps every line with the same instant, so any sort by time is a sort
    /// over equal keys and shuffles the history. The view walks this list and
    /// asks each line `isActivity`.
    public var lines: [TranscriptLine] = []

    public init(id: String, user: TranscriptLine? = nil) {
        self.id = id
        self.user = user
    }

    public var activity: [TranscriptLine] { lines.filter(\.isActivity) }

    /// How long each line of the turn ran: its arrival to the next one's, keyed
    /// by line id, and 0 for a history whose lines all arrived at once.
    ///
    /// The thinking has no end stamp — nothing in the protocol says "the model
    /// stopped thinking" — so "Reasoned for 12s" is the distance to the next
    /// thing that arrived, which is the answer the thinking produced.
    ///
    /// One pass, and the view reads it once per turn render. The first version
    /// was a `nextArrival(after:)` that scanned from the line to its successor:
    /// the same answer, O(lines) per row, and 10.5 ms measured over the 500
    /// activity rows of a 1,000-line turn — paid again on every publish while
    /// that disclosure is open.
    public var durations: [String: TimeInterval] {
        var out: [String: TimeInterval] = [:]
        out.reserveCapacity(lines.count)
        var previous: TranscriptLine?
        for line in lines {
            if let previous {
                out[previous.id] = max(0, line.at.timeIntervalSince(previous.at))
            }
            previous = line
        }
        return out
    }
    public var answers: [TranscriptLine] { lines.filter { !$0.isActivity } }

    /// The run's terminal event, set on the last turn of a finished transcript.
    ///
    /// `run_completed` closes a run and appends no line, so the last line of a
    /// turn is not where the turn ended: the answer finished, the model thought
    /// some more, and the run stopped. Without this, "Worked for" stops at the
    /// last thing that rendered.
    var runEndedAt: Date?

    public var start: Date? { user?.at ?? lines.first?.at }
    public var end: Date? {
        var last = lines.map(\.stoppedAt).max() ?? user?.at
        if let runEndedAt, last.map { runEndedAt > $0 } ?? true { last = runEndedAt }
        return last
    }
    public var elapsed: TimeInterval? { DurationFormat.between(start, end) }

    /// True while something in the turn is still going.
    public var running: Bool {
        lines.contains {
            if case .tool(let row) = $0.kind, row.state == .running { return true }
            if case .compaction(let row) = $0.kind, row.state == .running { return true }
            return $0.streaming
        }
    }

    /// The activity line's label: "Worked for 2m 50s", "Worked for 12s", and
    /// "Worked" when the clock was not running (a session loaded from disk, "Worked").
    public var activityLabel: String {
        let verb = running ? "Working" : "Worked"
        guard let text = DurationFormat.short(elapsed) else { return verb }
        return "\(verb) for \(text)"
    }
}

public enum RunState: Equatable, Sendable {
    case idle
    case starting
    case streaming
    /// Waiting on the user. The payload is the `request_id` to answer.
    case awaitingPermission(String)
    case finished(StopReason)
    case failed(String)
    case cancelled(String)

    public var isRunning: Bool {
        switch self {
        case .starting, .streaming, .awaitingPermission: return true
        default: return false
        }
    }

    /// One-line status for a title bar.
    public var label: String {
        switch self {
        case .idle: return "Idle"
        case .starting: return "Starting…"
        case .streaming: return "Streaming"
        case .awaitingPermission: return "Waiting for approval"
        case .finished(.endTurn): return "Done"
        case .finished(.maxTurns): return "Stopped: max turns"
        case .finished(.emptyResponse): return "Stopped: empty response"
        case .finished(.other(let reason)): return "Stopped: \(reason)"
        case .failed(let error): return "Failed: \(error)"
        case .cancelled(let reason): return "Cancelled: \(reason)"
        }
    }
}

// MARK: - Transcript

/// The timeline plus the run state, folded from events.
public struct Transcript: Equatable, Sendable {
    public private(set) var lines: [TranscriptLine] = []
    public private(set) var state: RunState = .idle
    public private(set) var runId: String?
    /// The prompt the user is waiting on the app to answer. Cleared by
    /// `permission_resolved`.
    public private(set) var pendingPermission: PermissionRequest?
    /// Answers already given, in order — the sheet's history and the proof that
    /// a timeout was a timeout and not a hang.
    public private(set) var outcomes: [PermissionOutcome] = []
    public private(set) var finished = false
    /// The instant the run ended, from the same clock as the line stamps.
    public private(set) var finishedAt: Date?
    /// The events that end a run. Defaults to this build's set; a client passes
    /// the union with the server's handshake set, so a terminal event added in
    /// a later protocol version ends the timeline instead of leaving it open.
    private let terminalTypes: Set<String>
    /// The clock the arrival stamps come from.
    ///
    /// Injectable, so a check can step it and assert a turn's duration instead of
    /// sleeping for it. Not compared: a stored closure makes the derived `==`
    /// impossible, and two transcripts that differ only in whose clock they use
    /// are the same timeline.
    private let clock: @Sendable () -> Date

    public init(
        terminalTypes: Set<String> = Wire.terminalEventTypes,
        clock: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.terminalTypes = terminalTypes
        self.clock = clock
    }

    public static func == (lhs: Transcript, rhs: Transcript) -> Bool {
        lhs.lines == rhs.lines && lhs.state == rhs.state && lhs.runId == rhs.runId
            && lhs.pendingPermission == rhs.pendingPermission && lhs.outcomes == rhs.outcomes
            && lhs.finished == rhs.finished && lhs.finishedAt == rhs.finishedAt
            && lhs.terminalTypes == rhs.terminalTypes
    }

    /// A line as it arrives: stamped with the clock, and appended.
    private mutating func emit(_ line: TranscriptLine) {
        var stamped = line
        stamped.at = clock()
        lines.append(stamped)
    }

    /// The same, inserted at an index — the thinking line goes above the answer
    /// it produced, not after it.
    private mutating func emit(_ line: TranscriptLine, at index: Int) {
        var stamped = line
        stamped.at = clock()
        lines.insert(stamped, at: index)
    }

    private func isTerminal(_ event: AgentEvent) -> Bool {
        event.isTerminal || terminalTypes.contains(event.type)
    }

    /// The client's own prompt. Not in the stream; the caller inserts it.
    public mutating func addUserMessage(_ text: String) {
        emit(TranscriptLine(id: "user-\(lines.count)", kind: .user(text), messageId: nil))
    }

    /// A past assistant message, for a session loaded from disk.
    ///
    /// History is not a stream: a stored session has the finished text and no
    /// events to fold, and inventing fake `message_completed` payloads to render
    /// it would make the transcript lie about what it saw.
    public mutating func addAssistantMessage(_ text: String) {
        emit(TranscriptLine(id: "history-\(lines.count)", kind: .assistant(text), messageId: nil))
    }

    /// A run that never reached the service.
    ///
    /// `run.start` was refused — bad working directory, unknown model, dead
    /// socket. No `run_failed` event is coming, so the timeline has to close the
    /// run itself, with the reason the call returned.
    public mutating func failLocally(_ reason: String) {
        finished = true
        finishedAt = clock()
        pendingPermission = nil
        state = .failed(reason)
    }

    public mutating func beginRun(runId: String) {
        self.runId = runId
        finished = false
        finishedAt = nil
        state = .starting
    }

    public mutating func apply(_ event: AgentEvent) {
        // A terminal event ends the run. Anything after it is a late arrival on
        // a connection that is closing; rendering it would resurrect a finished
        // run in the UI.
        if finished, !isTerminal(event) { return }

        switch event {
        case .runStarted(let e):
            runId = e.runId
            finished = false
            finishedAt = nil
            state = .streaming

        case .messageStarted(let e):
            emit(
                TranscriptLine(id: e.messageId, kind: .assistant(""), messageId: e.messageId, streaming: true)
            )
            state = .streaming

        case .reasoningDelta(let e):
            if let index = lines.lastIndex(where: { $0.messageId == e.messageId && isReasoning($0.kind) }) {
                appendDelta(e.delta, toLine: index)
            } else {
                let line = TranscriptLine(
                    id: "\(e.messageId)·thinking",
                    kind: .reasoning(e.delta),
                    messageId: e.messageId,
                    streaming: true
                )
                // Above the answer it belongs to. `message_started` has already
                // appended the assistant line, so appending here would put the
                // thinking underneath the reply it produced.
                if let answer = indexOfMessage(e.messageId) {
                    emit(line, at: answer)
                } else {
                    emit(line)
                }
            }
            state = .streaming

        case .messageDelta(let e):
            guard let index = indexOfMessage(e.messageId) else {
                // A delta for a message that never started: render it, do not
                // drop it. A missing message_started is the server's bug, and
                // the user should see the text.
                emit(
                    TranscriptLine(id: e.messageId, kind: .assistant(e.delta), messageId: e.messageId, streaming: true)
                )
                return
            }
            appendDelta(e.delta, toLine: index)
            // The answer has started: the thinking that produced it is finished,
            // and the view collapses it.
            closeReasoning(for: e.messageId)

        case .messageCompleted(let e):
            closeReasoning(for: e.messageId)
            // Authoritative. Deltas are the progress; this is the answer.
            var line = TranscriptLine(id: e.messageId, kind: .assistant(e.outputText), messageId: e.messageId)
            if let index = indexOfMessage(e.messageId) {
                line.at = lines[index].at
                lines[index] = line
            } else {
                emit(line)
            }

        case .messageFailed(let e):
            var line = TranscriptLine(id: e.messageId, kind: .messageFailed(e.error), messageId: e.messageId)
            if let index = indexOfMessage(e.messageId) {
                line.at = lines[index].at
                lines[index] = line
            } else {
                emit(line)
            }

        case .toolCallStarted(let e):
            emit(
                TranscriptLine(
                    id: e.toolCallId,
                    kind: .tool(
                        ToolCallRow(id: e.toolCallId, name: e.toolName, arguments: e.arguments, startedAt: clock())
                    ),
                    messageId: e.messageId
                )
            )

        case .toolCallCompleted(let e):
            // The row keeps the result, not its preview: the label folds it to a
            // line, and the disclosure shows what the tool actually returned.
            // Truncating here is what made an expanded tool row show its
            // arguments and never its output.
            let preview = ToolCallRow.State.done(Self.resultText(of: e.result))
            if let index = indexOfTool(e.toolCallId) {
                if case .tool(var row) = lines[index].kind {
                    row.state = preview
                    row.endedAt = clock()
                    lines[index].kind = .tool(row)
                }
            } else {
                emit(
                    TranscriptLine(
                        id: e.toolCallId,
                        kind: .tool(
                            // A result with no start: the row is done, and the
                            // elapsed time is unknown, so both stamps are "now"
                            // and `elapsed` answers nil.
                            // A result with no start: the row is done and its
                            // duration is unknown, so `endedAt` stays nil and
                            // `elapsed` answers nil. Stamping both to "now" would
                            // claim the call took no time at all.
                            ToolCallRow(
                                id: e.toolCallId, name: e.toolName, arguments: .null, state: preview,
                                startedAt: clock()
                            )
                        ),
                        messageId: e.messageId
                    )
                )
            }

        case .toolCallFailed(let e):
            let failure = ToolCallRow.State.failed(e.error)
            if let index = indexOfTool(e.toolCallId) {
                if case .tool(var row) = lines[index].kind {
                    row.state = failure
                    row.endedAt = clock()
                    lines[index].kind = .tool(row)
                }
            } else {
                emit(
                    TranscriptLine(
                        id: e.toolCallId,
                        kind: .tool(
                            ToolCallRow(
                                id: e.toolCallId, name: e.toolName ?? "?", arguments: .null, state: failure,
                                startedAt: clock()
                            )
                        ),
                        messageId: e.messageId
                    )
                )
            }

        case .permissionRequested(let e):
            let request = PermissionRequest(
                id: e.requestId, toolName: e.toolName, arguments: e.arguments, reason: e.reason
            )
            pendingPermission = request
            state = .awaitingPermission(e.requestId)

        case .permissionResolved(let e):
            let request = pendingPermission.map { $0.id == e.requestId ? $0 : Self.guess(from: outcomes, e) }
                ?? PermissionRequest(id: e.requestId, toolName: "?", arguments: .null, reason: "")
            let outcome = PermissionOutcome(
                request: request, granted: e.granted, scope: e.scope, reason: e.reason
            )
            outcomes.append(outcome)
            if pendingPermission?.id == e.requestId { pendingPermission = nil }
            // The refusal has no tool row of its own: a denied tool never
            // starts. The permission line is the record.
            emit(
                TranscriptLine(id: "permission-\(e.requestId)", kind: .permission(outcome), messageId: nil)
            )
            if state == .awaitingPermission(e.requestId) { state = .streaming }

        case .contextCompactionStarted(let e):
            emit(
                TranscriptLine(
                    id: "compaction-\(lines.count)",
                    kind: .compaction(
                        CompactionRow(id: "compaction-\(lines.count)", reason: e.reason, state: .running)
                    ),
                    messageId: nil
                )
            )

        case .contextCompactionCompleted:
            setCompaction(.done)

        case .contextCompactionFailed(let e):
            setCompaction(.failed(e.error))

        case .runCompleted(let e):
            runId = e.runId
            finished = true
            finishedAt = clock()
            pendingPermission = nil
            state = .finished(e.result.stopReason)
            closeStreamingLines()

        case .runFailed(let e):
            finished = true
            finishedAt = clock()
            pendingPermission = nil
            state = .failed(e.error)
            closeStreamingLines()

        case .runCancelled(let e):
            finished = true
            finishedAt = clock()
            pendingPermission = nil
            state = .cancelled(e.reason)
            closeStreamingLines()

        case .unknown(let type, _):
            emit(TranscriptLine(id: "unknown-\(lines.count)", kind: .note("event: \(type)"), messageId: nil))
            if isTerminal(event) {
                finished = true
                finishedAt = clock()
                pendingPermission = nil
                state = .finished(.other(type))
                closeStreamingLines()
            }
        }
    }

    /// The assistant text of the final message, for a "copy answer" affordance.
    public var finalText: String? {
        for line in lines.reversed() {
            if case .assistant(let text) = line.kind, !text.isEmpty { return text }
        }
        return nil
    }

    public var toolRows: [ToolCallRow] {
        lines.compactMap { if case .tool(let row) = $0.kind { return row } else { return nil } }
    }

    /// The timeline grouped into turns: a prompt, and everything the run did about it.
    public var turns: [Turn] {
        var out: [Turn] = []
        for line in lines {
            if case .user = line.kind {
                out.append(Turn(id: line.id, user: line))
            } else if out.isEmpty {
                // A run with no prompt in front of it: a session loaded from disk
                // that opens with the assistant, or a stream that starts before the
                // client echoes itself. It still needs a turn to render inside — and
                // the line has to go in it, not only name it.
                var turn = Turn(id: line.id)
                turn.lines.append(line)
                out.append(turn)
            } else {
                out[out.count - 1].lines.append(line)
            }
        }
        if finished, let finishedAt, !out.isEmpty {
            out[out.count - 1].runEndedAt = finishedAt
        }
        return out
    }

    // MARK: helpers

    private func indexOfMessage(_ messageId: String) -> Int? {
        lines.lastIndex { $0.messageId == messageId && isAssistant($0.kind) }
    }

    /// The most thinking a line keeps, and where it trims back to.
    ///
    /// The thinking stream is the longest text in the app and the least valuable
    /// one: it is never sent back to the model, never part of the answer, never
    /// saved as history, and the view shows only its tail. Measured on
    /// `lan/swift-1.5-iq3_s`, one run streamed 686,439 characters of it. Kept
    /// whole, every delta pays for the whole accumulated string — a string read
    /// out of an array element is shared with it, so appending copies: 3.4 µs per
    /// delta at 480k characters, 267 ms of memcpy per run, on the main actor, in
    /// the phase the user is watching — and every publish copies and compares the
    /// same string. `swift run ui-cost` reproduces the growth.
    ///
    /// So the line keeps a tail. Trimming at exactly the cap would copy the tail
    /// on every delta that follows it, so the cap is the trim's target, not its
    /// trigger. The answer has no cap: it is the product, shown in full and
    /// copied out.
    private static let reasoningKeep = 8_000
    private static let reasoningTrimAt = 16_000

    /// Add a delta to a line's accumulated text.
    private mutating func appendDelta(_ delta: String, toLine index: Int) {
        switch lines[index].kind {
        case .reasoning(let accumulated):
            var text = accumulated
            text.append(delta)
            if text.utf8.count > Self.reasoningTrimAt {
                // `utf8.count` is O(1); `count` walks the string.
                text = String(text.suffix(Self.reasoningKeep))
            }
            lines[index].kind = .reasoning(text)
        case .assistant:
            lines[index].appendAnswerDelta(delta)
        default:
            break
        }
    }

    private func isReasoning(_ kind: TranscriptLine.Kind) -> Bool {
        if case .reasoning = kind { return true }
        return false
    }

    private mutating func closeReasoning(for messageId: String) {
        guard let index = lines.lastIndex(where: { $0.messageId == messageId && isReasoning($0.kind) }) else { return }
        lines[index].streaming = false
    }

    /// Nothing is arriving any more, so nothing may be marked as arriving.
    ///
    /// `message_completed` is the event that normally closes an answer line, and a
    /// run ends without it more often than that suggests: Cancel pressed
    /// mid-answer, a stream that drops, a run-level failure with no message-level
    /// one. A line left `streaming` renders as plain text for the rest of the
    /// session, so the user reads `## Plan` and `**bold**` in a run that finished
    /// two minutes ago.
    private mutating func closeStreamingLines() {
        for index in lines.indices where lines[index].streaming {
            lines[index].streaming = false
        }
    }

    private func indexOfTool(_ toolCallId: String) -> Int? {
        lines.lastIndex { if case .tool(let row) = $0.kind { return row.id == toolCallId } else { return false } }
    }

    private func isAssistant(_ kind: TranscriptLine.Kind) -> Bool {
        if case .assistant = kind { return true }
        return false
    }

    private mutating func setCompaction(_ state: CompactionRow.State) {
        guard let index = lines.lastIndex(where: { if case .compaction = $0.kind { return true } else { return false } })
        else { return }
        if case .compaction(var row) = lines[index].kind {
            row.state = state
            lines[index].kind = .compaction(row)
        }
    }

    private static func guess(from outcomes: [PermissionOutcome], _ event: PermissionResolved) -> PermissionRequest {
        outcomes.first { $0.request.id == event.requestId }?.request
            ?? PermissionRequest(id: event.requestId, toolName: "?", arguments: .null, reason: "")
    }

    /// The text of a tool result, for the row to show.
    ///
    /// A result is whatever the tool returned — a string, a `{content: …}` dict,
    /// a block list, a JSON value — so it needs one string.
    ///
    /// Bounded, at the **head**, and the bound is visible. A tool result is the
    /// longest text in a run (a `read` of a source file, a `bash` of a build log)
    /// and every published write copies the transcript, so an unbounded one is a
    /// per-publish cost paid for text the user opens once. The head is the part
    /// worth keeping: a file starts at the top, a command's summary is at the
    /// top. The thinking stream is bounded at its *tail* for the opposite reason —
    /// there the part that matters is the part that is arriving.
    ///
    /// The cap is approximate: the size test is `utf8.count`, which is O(1), and
    /// the cut is in characters. Same trade as `reasoningKeep`.
    public static func resultText(of result: JSONValue) -> String {
        let text: String
        switch result {
        case .string(let value): text = value
        case .null: text = ""
        default:
            if let object = result.objectValue,
               let content = object["content"]?.stringValue ?? object["text"]?.stringValue,
               !content.isEmpty {
                text = content
            } else if let blocks = result.arrayValue {
                // A block list: `[{type: "text", text: "…"}, …]`.
                text = blocks.compactMap { block in
                    block.stringValue ?? block.objectValue?["text"]?.stringValue
                        ?? block.objectValue?["content"]?.stringValue
                }.joined(separator: "\n")
            } else {
                let encoded = (try? JSONEncoder().encode(result)) ?? Data()
                text = String(data: encoded, encoding: .utf8) ?? "\(result)"
            }
        }
        guard text.utf8.count > toolResultKeep else { return text }
        return String(text.prefix(toolResultKeep))
            + "\n\n… truncated: showing \(toolResultKeep) of \(text.utf8.count) characters"
    }

    /// The most a tool row keeps. Above it, the row says so rather than lying
    /// about having shown everything.
    public static let toolResultKeep = 8_000

    /// A timeline row's label: one line, bounded. The full text stays in the row
    /// for the disclosure; a label is a label.
    public static func oneLine(_ text: String) -> String {
        let folded = text.split(whereSeparator: \.isNewline).joined(separator: " ⏎ ")
        return folded.count > 240 ? String(folded.prefix(240)) + "…" : folded
    }

    /// One-line preview of a tool result.
    public static func preview(of result: JSONValue) -> String {
        oneLine(resultText(of: result))
    }
}

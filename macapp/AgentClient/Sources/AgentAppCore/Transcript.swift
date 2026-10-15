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

/// What a streaming answer is drawn as: the blocks in the bound, and the count of
/// everything the bound left out.
///
/// The counts are part of the answer, not decoration the view invents: a row that
/// says "1,843 blocks above" is the difference between a UI that dropped text and
/// a UI that is showing you the end of a long run.
public struct ShownBlocks: Equatable, Sendable {
    public var shown: [MarkdownBlock] = []
    /// Blocks that exist and are not drawn, all of them before `shown`.
    public var hiddenBlocks: Int = 0
    /// Characters cut off the blocks that are drawn.
    public var cutChars: Int = 0
    /// The tail a streaming answer draws: the end of `tailText`, bounded.
    ///
    /// The tail is the block that is still arriving, so it is not in `blocks`
    /// and the block bound cannot reach it. Left alone it is the whole rest of
    /// the answer — a loop with no blank lines leaves one paragraph of 18,005
    /// characters, all of it tail — and the view lays out every character of it.
    public var tail: String = ""
    /// Characters of `tailText` that precede `tail` and are not drawn.
    public var tailHidden: Int = 0
    /// Every block the answer has, drawn or not.
    ///
    /// `hiddenBlocks` is what a row says it left out; this is what a "Show all
    /// 2,573 blocks" button has to name. They are the same number while the
    /// bound is the only reason blocks are missing, and they are not the same
    /// number the moment the row is expanded — which is why the row asks for
    /// this one and not the other.
    public var blocksTotal: Int = 0

    public init(
        shown: [MarkdownBlock] = [],
        hiddenBlocks: Int = 0,
        cutChars: Int = 0,
        tail: String = "",
        tailHidden: Int = 0,
        blocksTotal: Int = 0
    ) {
        self.shown = shown
        self.hiddenBlocks = hiddenBlocks
        self.cutChars = cutChars
        self.tail = tail
        self.tailHidden = tailHidden
        self.blocksTotal = blocksTotal
    }

    public var isEmpty: Bool { shown.isEmpty && hiddenBlocks == 0 && tail.isEmpty }

    /// What the bound left out, in the words the row prints: "3,939 blocks and
    /// 18,005 characters". Empty when the bound left nothing out.
    ///
    /// Blocks and characters are counted apart because they are cut apart, and a
    /// run-on answer has no blocks at all: 20,000 characters of "apple" with no
    /// blank line is one tail and zero blocks, and a row that said "0 blocks are
    /// not shown" about it would be reading from the wrong axis.
    ///
    /// The drawn blocks' own cuts are not in this string: the row prints them on
    /// the line under the blocks, where "of this answer" reads.
    public var hiddenSummary: String {
        var parts: [String] = []
        if hiddenBlocks > 0 { parts.append("\(hiddenBlocks) blocks") }
        if tailHidden > 0 { parts.append("\(tailHidden) characters") }
        return parts.joined(separator: " and ")
    }

    /// The label of the button that lifts the bound: what pressing it adds.
    ///
    /// The counts are the totals, not the hidden ones, because that is what the
    /// user gets — "Show all 3,999 blocks" is the answer's size, and the row has
    /// already said what is missing. Empty when there is nothing to reveal.
    ///
    /// The characters are the tail's and the drawn blocks' cuts together, because
    /// the button lifts both bounds at once. A finished answer has no tail, so a
    /// short answer whose one block is 24,000 characters long would print "22,000
    /// characters are not shown" over a row with no button on it — text hidden
    /// with nothing to reveal it.
    public var revealLabel: String {
        var parts: [String] = []
        if hiddenBlocks > 0 { parts.append("\(blocksTotal) blocks") }
        let chars = tailHidden + tail.utf8.count + cutChars
        if chars > 0 { parts.append("\(chars) characters") }
        guard !parts.isEmpty else { return "" }
        return "Show all \(parts.joined(separator: " and "))"
    }
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
    /// A finished answer has these too — `parseAnswer` builds them once, from
    /// the text that arrived whole — so the row that shows it is bounded the
    /// same way and never parses on a render.
    public var blocks: [MarkdownBlock] = []
    /// The text `blocks` was parsed from, so the parse runs when it changes.
    ///
    /// Not `private`: a private stored property makes the memberwise init private,
    /// and this struct is constructed by name from all over the reducer.
    var parsedPrefix: String = ""
    /// The answer's in-progress tail, kept so the view splits nothing.
    ///
    /// The view used to ask `Markdown.split(text).tail` on every render, which is
    /// O(n) in the answer. It also pins the view to a fresh boundary: parse the
    /// finished prefix on a gate — see `answerReparseEvery` — and the view's tail
    /// starts *after* the newest blank line while `blocks` stops at the oldest, so
    /// everything between them is drawn by neither and vanishes mid-stream. The
    /// boundary therefore lives here, and the view draws `blocks` and this.
    ///
    /// Between parses it is the plain text of blocks that have committed but not
    /// been parsed yet, which is what a block that has not ended already looks like.
    public var tailText: String = ""
    /// The answer length `blocks` was last scanned at, the gate's floor.
    var parsedLength: Int = 0
    /// The blocks the answer draws, and what the bound left out.
    ///
    /// `blocks` is the answer's committed structure and is never trimmed — the
    /// stored answer is the answer. This is what the view is allowed to lay out,
    /// and it is bounded on every axis it has: the last `answerBlocksShown`
    /// blocks, each cut to `answerBlockCharsShown`, plus the last
    /// `answerBlockCharsShown` characters of the tail. The last blocks, not the
    /// first, because an answer is read at its end, where the text is arriving.
    ///
    /// This bounds a finished answer too. It used not to — "the bound is for the
    /// stream, not the product" — and the live app answered that: a run that
    /// leaves a 20,000-character answer behind leaves 2,573 blocks, the row is in
    /// the viewport, and every publish lays out every one of them. Measured on
    /// the next run in the same session: main thread pinned at ~100% CPU, RSS
    /// 235 MB → 800 MB in 90 s, `sample` all inside AttributeGraph
    /// (`propagate_dirty`, `UpdateStack::update`, `DisplayList`), the AX walk
    /// timing out, and the Stop click never delivered — the run said "Starting…"
    /// 45 s after Cancel was pressed. A row the user can read is a row of tens of
    /// blocks; the rest is behind "Show all", which is a choice, not a default.
    public var shownBlocks: ShownBlocks {
        let keep = Array(blocks.suffix(Transcript.answerBlocksShown))
        var out = ShownBlocks(
            hiddenBlocks: blocks.count - keep.count,
            blocksTotal: blocks.count
        )
        for block in keep {
            let (cut, dropped) = Transcript.capBlock(block)
            out.shown.append(cut)
            out.cutChars += dropped
        }
        // The tail is bounded at its end for the same reason the blocks are: the
        // text that is arriving is at the end, and that is the part worth
        // drawing. It is the one unit the block bound cannot reach, because the
        // tail is not in `blocks`. Its reach is narrower than the blocks':
        // `Markdown.split` cuts the tail at the last blank line and the reparse
        // gate moves that boundary every `answerReparseEvery` characters, so a
        // stream that ends its deltas with newlines leaves the tail empty, and a
        // stream of word-sized deltas leaves at most that much. The loop shapes
        // the harness measures (`swift run ui-cost`, section 2c) leave 200-2,900
        // characters of tail, so this bound is the difference between drawing
        // 2,000 and drawing all of it — real, and an order of magnitude below
        // what the blocks cost. A finished answer is where it is biggest: the
        // run-on ending never commits, so section 2d's 4,000-paragraph answer
        // with a run-on tail keeps 2,000 characters and leaves 24,999 behind
        // "Show all".
        let limit = Transcript.answerBlockCharsShown
        let n = tailText.utf8.count
        if n > limit {
            out.tail = String(tailText.suffix(limit))
            out.tailHidden = n - limit
        } else {
            out.tail = tailText
        }
        return out
    }
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

    /// Parse an answer that arrived whole, once, into the blocks the view draws.
    ///
    /// Called at `message_completed` and for a history loaded from disk. The
    /// streaming line builds `blocks` as its deltas land; the completed event
    /// replaces the line, and a line that arrives with no blocks leaves the view
    /// with nothing bounded to draw, so it parses and lays out the whole answer —
    /// 2,573 text views for a looping run, on every publish, forever. Parsing
    /// here costs a few milliseconds once (`swift run ui-cost`, section 2) and
    /// buys the row a bounded drawing and a block count to label a "Show all"
    /// with.
    mutating func parseAnswer() {
        guard case .assistant(let text) = kind else { return }
        // A finished answer has no tail.
        //
        // `Markdown.split` cuts the tail at the last blank line, which is the
        // right rule while the answer is arriving: the block after that line is
        // not finished, and parsing it per character is the O(n²) the streaming
        // path exists to avoid. It is the wrong rule once the answer is done. The
        // answer's last block has no blank line after it — that is what makes it
        // last — so the split handed it to the tail, and the view draws the tail
        // as plain text. A table is the block a model writes last, and the blank
        // line after it is the one thing it usually omits, so the answer's own
        // summary table reached the screen as `| file | lines |`. Measured on the
        // live app: a finished answer, its table, and the pipes.
        //
        // The whole text is parsed here, once, and the tail is empty. The cost is
        // one parse of a string that stopped growing.
        blocks = Markdown.blocks(text)
        parsedPrefix = text
        parsedLength = text.utf8.count
        tailText = ""
    }

    /// Fold a delta into the answer, and re-parse the part of it that is finished.
    ///
    /// Two gates, because the answer is streamed and the parse is not free. The
    /// first is the newline: the finished prefix can only move when a line ends.
    /// The second is growth — `answerReparseEvery` — because a looping model
    /// streams blank lines at the reducer without stopping, and each scan reads
    /// the whole answer: a newline per delta is quadratic in the answer, and the
    /// cap alone does not fix that, it only lowers the constant.
    ///
    /// Measured on the machine that died of it (`swift run ui-cost`, section 2b):
    /// uncapped, 20 000 characters with a newline per delta cost 7.2 s to fold,
    /// 200 000 cost 719 s. Capped, the same 200 000 cost 162 s — 6.5 ms per delta,
    /// a dropped frame every time the model ends a line. Gated, the number of
    /// scans stops growing with the deltas; `answerReparseEvery` carries that cost.
    /// Below `answerScanFreely` the gate is off, because a scan of a short answer
    /// costs nothing and a committed block should render as soon as it commits.
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
        // Bounded before the parse and before the publish, because both are O(n) in
        // this string — see `Transcript.answerKeepHead`. The test is free:
        // `utf8.count` is O(1) on a Swift String, ASCII or not.
        var capped = false
        if text.utf8.count > Transcript.answerTrimAt {
            text = Transcript.capAnswer(text)
            capped = true
        } else {
            tailText.append(delta)
        }
        kind = .assistant(text)
        // The cap bounds the string; this bounds the scans — see the constant.
        guard canMove || capped else { return }
        let length = text.utf8.count
        guard capped || length < Transcript.answerScanFreely
            || length >= parsedLength + Transcript.answerReparseEvery
        else { return }
        parsedLength = length
        let (complete, tail) = Markdown.split(text)
        // The boundary the view draws from, so `blocks` and `tailText` cover the
        // answer between them with nothing missing and nothing twice.
        tailText = tail
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
    ///
    /// Parsed on the way in, for the same reason a completed answer is: a loaded
    /// session's answers are finished, and a finished answer the view has to
    /// parse and draw whole is the row that pins the main thread.
    public mutating func addAssistantMessage(_ text: String) {
        var line = TranscriptLine(id: "history-\(lines.count)", kind: .assistant(text), messageId: nil)
        line.parseAnswer()
        emit(line)
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
                    TranscriptLine(
                        id: e.messageId,
                        kind: .assistant(e.delta),
                        messageId: e.messageId,
                        streaming: true,
                        tailText: e.delta
                    )
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
            line.parseAnswer()
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
    /// trigger.
    private static let reasoningKeep = 8_000
    private static let reasoningTrimAt = 16_000

    /// The most an answer keeps, and the size it is cut back to.
    ///
    /// The answer was uncapped on purpose: it is the product, shown in full and
    /// copied out. That is right about an answer a model *finishes*, and the
    /// answers measured here are short enough for it. It is wrong about one a
    /// model does not: a quantized local model loops, and the answer becomes the
    /// longest text in the app — and two costs are O(n) in it, once per delta.
    /// The first is the markdown re-parse `appendAnswerDelta` runs whenever a
    /// newline lands, over the whole answer; a looping model streams blank lines
    /// at it without stopping, so the answer's cost is quadratic in the answer.
    /// The second is the publish: folding the next delta copies the transcript,
    /// and the view lays the whole answer out again.
    ///
    /// Measured on the machine that died of it (`swift run ui-cost`, section 2b):
    /// a 200,000-character answer with a newline per delta takes minutes to fold,
    /// and the app running one sat at 100% CPU with its memory growing 19 MB a
    /// second, still spinning after the run was cancelled — the text was already
    /// there, and cancelling does not take it back out.
    ///
    /// So the answer is bounded, and bounded at both ends: the head, because an
    /// answer is read from the top, and the tail, because it is what is arriving
    /// and the user watches the stream move. Between them the line says how much
    /// it dropped, in the words a tool result already uses. The full text is not
    /// lost — the service saves it, and `--resume` reads it back.
    public static let answerKeepHead = 12_000
    public static let answerKeepTail = 4_000
    public static let answerTrimAt = 32_000

    /// Characters of answer between two scans of its finished prefix.
    ///
    /// The cap bounds the string; this bounds the number of scans. `Markdown.split`
    /// reads the whole answer and `Markdown.blocks` re-parses the finished part of
    /// it, so a newline per delta is O(n) per delta *at the cap too*: on the
    /// harness, 25 000 deltas over a capped answer cost 162 s, 6.5 ms each. Once
    /// per this many characters is O(n / gate) scans of O(n) each.
    ///
    /// 4 000 is the same trade the thinking line makes with its 8 000: the blocks
    /// that have committed appear up to this many characters late, and in between
    /// they are plain text, which is what a block that has not ended already looks
    /// like. A finished answer is parsed whole by the view, so nothing is late at
    /// the end, and the tail is never late — it is the text being written.
    public static let answerReparseEvery = 4_000

    /// The answer size below which every newline is scanned.
    ///
    /// The scan is O(n), so a short answer is cheap to scan and the gate would
    /// only make it late: most answers are under this, and the first one that
    /// commits should render as soon as it commits. Above it, the gate runs.
    public static let answerScanFreely = 2_000

    /// The most blocks a streaming answer draws, and the most characters of each.
    ///
    /// The cap bounds the answer's characters and the gate bounds the scans, and
    /// the app *still* wedged on a live looping run: main thread pinned at 100%,
    /// RSS climbing 6 MB/s (178 MB → 782 MB in two minutes), `sample` putting the
    /// main thread inside SwiftUI's attribute graph — `AG::Subgraph::update`,
    /// `ViewLayoutEngine.sizeThatFits`, `SelectionOverlay.updateNSView` — with no
    /// frame from this codebase in it at all. The reducer was not the cost; the
    /// unit was. A publish lays the streaming answer out as views: one per block,
    /// and one text view per block however long that block is. Measured
    /// (`swift run ui-cost`, section 2c), a looping answer with blank lines leaves
    /// 2,573 blocks; one without blank lines leaves a single paragraph of 18,005
    /// characters. Both are a per-publish cost that grows with a run that does not
    /// stop, which is the same defect wearing the answer's clothes.
    ///
    /// 60 blocks is about three screens of answer — the reading position — and
    /// 2,000 characters is about one. What is beyond them is counted in the line,
    /// and the tail, the text that is arriving, is never cut. A finished answer
    /// is drawn whole: one layout of the product is not this defect.
    public static let answerBlocksShown = 60
    public static let answerBlockCharsShown = 2_000

    /// Cut one block to the drawing bound, and say how many characters came off.
    static func capBlock(_ block: MarkdownBlock) -> (MarkdownBlock, Int) {
        let limit = answerBlockCharsShown
        func cut(_ text: String) -> (String, Int) {
            let n = text.utf8.count
            guard n > limit else { return (text, 0) }
            return (String(text.prefix(limit)), n - limit)
        }
        // A list is cut at an item, not mid-item: half a bullet is not a bullet,
        // and the bound is approximate in the same way `reasoningKeep` is.
        func cutItems(_ items: [String]) -> ([String], Int) {
            var kept: [String] = []
            var used = 0
            for item in items {
                let n = item.utf8.count
                if used + n > limit, !kept.isEmpty { break }
                kept.append(item)
                used += n
            }
            return (kept, items.dropFirst(kept.count).reduce(0) { $0 + $1.utf8.count })
        }
        switch block {
        case .paragraph(let text):
            let (t, d) = cut(text); return (.paragraph(t), d)
        case .heading(let level, let text):
            let (t, d) = cut(text); return (.heading(level: level, text: t), d)
        case .code(let language, let text):
            let (t, d) = cut(text); return (.code(language: language, text: t), d)
        case .bullets(let items):
            let (k, d) = cutItems(items); return (.bullets(k), d)
        case .numbered(let items):
            let (k, d) = cutItems(items.map(\.text)); return (.numbered(Array(items.prefix(k.count))), d)
        case .quote(let lines):
            let (k, d) = cutItems(lines); return (.quote(k), d)
        case .table(let rows, let alignment):
            var kept: [[String]] = []
            var used = 0
            for row in rows {
                let n = row.reduce(0) { $0 + $1.utf8.count }
                if used + n > limit, !kept.isEmpty { break }
                kept.append(row)
                used += n
            }
            let dropped = rows.dropFirst(kept.count).reduce(0) { r, row in r + row.reduce(0) { $0 + $1.utf8.count } }
            return (.table(rows: kept, alignment: alignment), dropped)
        case .rule:
            return (block, 0)
        }
    }

    /// Cut an answer back to the cap. Above it, the line is the head, a visible
    /// count of what is missing, and the tail.
    static func capAnswer(_ text: String) -> String {
        let total = text.utf8.count
        guard total > answerTrimAt else { return text }
        // `prefix`/`suffix` are character-based and O(n); the size test that
        // guards them is O(1), so this walk runs once per cap and not once per
        // delta. Same trade as `reasoningKeep`, and the same approximate bound.
        let head = String(text.prefix(answerKeepHead))
        let tail = String(text.suffix(answerKeepTail))
        return head
            + "\n\n… truncated: showing the first \(answerKeepHead) and last \(answerKeepTail) of \(total) characters\n\n"
            + tail
    }

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

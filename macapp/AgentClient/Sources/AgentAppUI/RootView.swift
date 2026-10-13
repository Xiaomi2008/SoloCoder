import AgentAppCore
import AgentClient
import AppKit
import SwiftUI

// The views, in a library.
//
// They used to live in the executable target, which made them unreachable: a
// SwiftPM executable's types cannot be imported, so nothing outside that target
// could put them on screen — not a check, not a preview, not a second app. The
// app's logic is in `AgentAppCore` for the same reason (see the package's
// header): a target nothing can import is a target nothing can exercise.
//
// So: the views are here, `AgentApp` is the `@main` that opens a window on them,
// and `ui-preview` opens a window on them with a transcript fed in by hand —
// which is how a change to a row gets looked at before it gets shipped.
//
// The layout is Bionic's: a sidebar of sessions, a header naming the session, a
// centred reading column of turns, and a composer that is a card with its
// controls inside it. The colours and the hairlines are in `Theme`.
//
// The rule the whole file keeps is the one the package exists for: **a view
// draws, and the core decides.** Every word a row shows — the verb of a tool
// call, the duration of a turn, whether a line is process or result — is
// computed in `AgentAppCore`, where `swift run agent-checks` can reach it. What
// is left here is geometry.

// MARK: - Root

public struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var draft: String
    @State private var query = ""
    @State private var selection: String?

    /// `prompt` seeds the composer, so a preview can show the field as the user
    /// left it: multi-line, with a reason under it, Send greyed out.
    public init(prompt: String = "") {
        _draft = State(initialValue: prompt)
    }

    public var body: some View {
        NavigationSplitView {
            Sidebar(query: $query, selection: $selection)
                .navigationSplitViewColumnWidth(min: 190, ideal: Theme.sidebarWidth, max: 320)
        } detail: {
            Detail(draft: $draft)
        }
        .tint(Theme.accent)
        // `maxWidth` and `maxHeight` and not only the minimums: a frame with a
        // maximum and no minimum takes its child's ideal size when the window
        // proposes nothing, and the split view then lays out at 590 points and
        // sits in the top-left of a 1,240-point window. The window is the thing
        // that has to win.
        .frame(minWidth: 760, maxWidth: .infinity, minHeight: 460, maxHeight: .infinity)
        .sheet(item: $model.promptToAnswer) { request in
            PermissionSheet(request: request)
        }
        .alert(
            "The service said no",
            isPresented: Binding(
                get: { model.banner != nil },
                set: { if !$0 { model.banner = nil } }
            )
        ) {
            Button("OK") { model.banner = nil }
        } message: {
            Text(model.banner ?? "")
        }
    }
}

/// Choose the scope the agent works in.
///
/// A free function, called from the sidebar's footer: the panel is AppKit's and
/// modal. It used to sit in the toolbar, which shares 900 points with the window
/// title, two pickers, a segmented control and a menu — the group overflowed
/// into the `>>` chevron, and a control hidden in an overflow menu is a control
/// the user does not know they have.
@MainActor
func chooseWorkingDir(_ model: AppModel) {
    let panel = NSOpenPanel()
    panel.canChooseDirectories = true
    panel.canChooseFiles = false
    panel.allowsMultipleSelection = false
    panel.prompt = "Choose"
    panel.message = "The agent's tools work inside this directory."
    if panel.runModal() == .OK, let url = panel.url {
        model.workingDir = url.path
    }
}

// MARK: - Sidebar

/// The sessions, and the two things that are always true about the app: what it
/// is connected to, and what directory it works in.
struct Sidebar: View {
    @EnvironmentObject var model: AppModel
    @Binding var query: String
    @Binding var selection: String?
    @State private var deleteTarget: SessionRow?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                selection = nil
                model.newRun()
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: "square.and.pencil")
                    Text("New session")
                    Spacer()
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(Theme.fill, in: RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
            .padding(.horizontal, 10)
            .padding(.top, 10)
            .help("Start a session with an empty timeline")

            HStack(spacing: 5) {
                Image(systemName: "magnifyingglass").font(.system(size: 11)).foregroundStyle(Theme.faint)
                TextField("Search", text: $query)
                    .textFieldStyle(.plain)
                    .font(.callout)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .background(Theme.fill, in: RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
            .padding(.horizontal, 10)
            .padding(.top, 8)

            List(selection: $selection) {
                ForEach(filtered, id: \.sessionId) { row in
                    SessionRowView(row: row)
                        .tag(row.sessionId)
                        .contextMenu {
                            Button("Delete Session", role: .destructive) { deleteTarget = row }
                                // The row you are watching writes itself: the run
                                // saves its session when it ends, so a delete
                                // now would be undone a second later. The server
                                // refuses the same thing (`session_busy`) for a
                                // run the app cannot see — one started on the CLI.
                                .disabled(!model.canDelete(row.sessionId))
                        }
                }
                // An empty list of files reads as a broken sidebar, and an empty
                // search reads as a broken search — the two answers are different.
                if model.sessions.isEmpty {
                    Text("No sessions yet")
                        .font(.caption)
                        .foregroundStyle(Theme.faint)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, 12)
                } else if filtered.isEmpty {
                    Text("Nothing matches “\(query)”")
                        .font(.caption)
                        .foregroundStyle(Theme.faint)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, 12)
                }
            }
            .listStyle(.sidebar)
            .scrollContentBackground(.hidden)
            .onChange(of: selection) { _, chosen in
                guard let chosen, chosen != model.sessionId else { return }
                model.open(chosen)
            }
            // The name in the question, not the id: the user is about to remove
            // the conversation they recognise, and a 28-character string is not
            // a thing they can check.
            .confirmationDialog(
                deleteTarget.map { "Delete \u{201c}\(SessionTitle.label($0))\u{201d}?" } ?? "Delete Session?",
                isPresented: deletePromptShown,
                titleVisibility: .visible
            ) {
                Button("Delete Session", role: .destructive) {
                    if let row = deleteTarget {
                        let gone = row.sessionId
                        deleteTarget = nil
                        Task { await model.delete(gone) }
                    }
                }
                Button("Cancel", role: .cancel) { deleteTarget = nil }
            } message: {
                Text("The conversation is removed from disk. This cannot be undone.")
            }

            Footer(query: $query)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface)
        .task { await model.refreshSessions() }
    }

    /// The stored sessions, filtered by the search field. The rule is the core's
    /// (`SessionTitle.matches`), so the check suite can say what a row answers
    /// to; the view only applies it.
    private var filtered: [SessionRow] {
        model.sessions.filter { SessionTitle.matches($0, query: query) }
    }

    /// The binding the dialog wants.
    ///
    /// The state holds the *row*, not the id: the question names the session,
    /// and answering it needs the title the user just read.
    private var deletePromptShown: Binding<Bool> {
        Binding(get: { deleteTarget != nil }, set: { if !$0 { deleteTarget = nil } })
    }
}

/// A stored session.
///
/// `AgentClient.SessionRow` is the protocol's row; this is its row on screen, so
/// the two names stay apart — an unqualified `SessionRow` in this file would
/// otherwise mean the view and not the payload.
struct SessionRowView: View {
    let row: AgentClient.SessionRow

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            // The id is 28 characters and the sidebar is 232 points, so the row
            // is a truncation case, not a layout case: the VStack has to be
            // offered the row's width for the Text to be able to cut itself.
            VStack(alignment: .leading, spacing: 1) {
                Text(SessionTitle.label(row))
                    .font(.callout)
                    .lineLimit(1)
                    // The tail, not the middle: the name is a sentence, and the
                    // end of a sentence is where its point is. The id row below
                    // is the one that truncates in the middle, because a prefix
                    // of an id is still an id you can type.
                    .truncationMode(.tail)
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(Theme.faint)
                if row.title != nil {
                    // The id stays visible under the name: it is what `--session`
                    // takes, and a sidebar that hides it leaves the CLI user
                    // reading a title they cannot type anywhere.
                    Text(row.sessionId)
                        .font(.caption2)
                        .monospaced()
                        .foregroundStyle(Theme.faint.opacity(0.75))
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .help(row.sessionId)
            if row.error != nil {
                Image(systemName: "exclamationmark.triangle")
                    .font(.caption2)
                    .foregroundStyle(Theme.warning)
                    .help(row.error ?? "")
            }
        }
        .padding(.vertical, 2)
    }

    private var subtitle: String {
        let when = Date(timeIntervalSince1970: row.updatedAt)
        let text = Self.relativeFormatter.localizedString(for: when, relativeTo: Date())
        return "\(text) · \(row.messageCount) msgs"
    }

    private static let relativeFormatter: RelativeDateTimeFormatter = {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter
    }()
}

/// The connection and the scope: the two facts about the app itself, kept where
/// they are always visible.
struct Footer: View {
    @EnvironmentObject var model: AppModel
    @Binding var query: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider().overlay(Theme.hairline)
            HStack(spacing: 6) {
                Circle()
                    .fill(model.connected ? Theme.success : Theme.warning)
                    .frame(width: 7, height: 7)
                Text(model.status)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
                Spacer()
                Button {
                    Task { await model.refreshModels() }
                } label: {
                    Image(systemName: "arrow.clockwise").font(.system(size: 10))
                }
                .buttonStyle(.plain)
                .foregroundStyle(Theme.muted)
                .help("Reload the models the service offers")
            }
            Button {
                chooseWorkingDir(model)
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "folder").font(.system(size: 10))
                    Text(model.workingDir.isEmpty ? "choose a working directory" : (model.workingDir as NSString).lastPathComponent)
                        .font(.caption)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(model.workingDir.isEmpty ? Theme.warning : Theme.muted)
            .help(
                model.workingDir.isEmpty
                    ? "required — the scope the agent works in"
                    : "the scope the agent works in — click to change it\n\(model.workingDir)"
            )
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }
}

// MARK: - Detail

struct Detail: View {
    @EnvironmentObject var model: AppModel
    @Binding var draft: String

    var body: some View {
        VStack(spacing: 0) {
            Header()
            TranscriptColumn()
            ComposerCard(draft: $draft)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Theme.surface)
    }
}

/// The session's name, and what the app is doing right now.
struct Header: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(1)
                if model.transcript.state.isRunning || model.busy {
                    HStack(spacing: 5) {
                        ProgressView().controlSize(.mini)
                        Text(model.transcript.state.label)
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Theme.fill, in: Capsule())
                }
                Spacer()
                if let answer = model.transcript.finalText {
                    // The run's last answer, from anywhere in the timeline: the
                    // per-answer copy is the one being read, this is the one that
                    // was scrolled past to ask a follow-up.
                    CopyButton(text: answer, label: "the last answer")
                }
                Text(model.selectedModel.isEmpty ? "router" : model.selectedModel)
                    .font(.caption)
                    .foregroundStyle(Theme.faint)
                    .lineLimit(1)
                    .help("The model for the next run. \(model.modelPolicy)")
            }
            .padding(.horizontal, 14)
            .frame(height: 44)

            if let notice = model.notice {
                HStack(spacing: 8) {
                    Image(systemName: "info.circle").font(.caption).foregroundStyle(Theme.warning)
                    Text(notice).font(.caption).foregroundStyle(Theme.muted).lineLimit(2)
                    Spacer()
                    Button("OK") { model.notice = nil }.controlSize(.mini)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 6)
                .background(Theme.fill)
            }
        }
        // Opaque, and above a hairline: the timeline scrolls under the header,
        // and a transparent header shows the answer's tops through the title.
        .background(Theme.surface)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.hairline).frame(height: 1) }
    }

    /// The first prompt, on one line. A session is named by what it asked for;
    /// with no prompt yet, it is a new session.
    private var title: String {
        for line in model.transcript.lines {
            if case .user(let text) = line.kind {
                let first = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? text
                return first.isEmpty ? "New session" : String(first.prefix(80))
            }
        }
        return "New session"
    }
}

// MARK: - Transcript

/// The turns, following the stream.
///
/// The old rule was "scroll when the line count changes", which is the wrong
/// signal twice over: a 2k-character answer is one line, so its 1,300 deltas
/// change no count and the view sat still while the answer ran off the bottom;
/// and it scrolled even when the user had scrolled up to read something, yanking
/// them back down mid-sentence.
///
/// So the signal is the **last line's value** — it changes when a line is
/// appended and when the text in it grows — and it is acted on only while the
/// view is pinned to the bottom. `LazyVStack` gives the pin for free: the last
/// turn's `onAppear`/`onDisappear` is exactly "the user is at the bottom" / "the
/// user has scrolled away", with no geometry reader per row to pay for.
struct TranscriptColumn: View {
    @EnvironmentObject var model: AppModel
    @State private var pinned = true

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    if model.transcript.turns.isEmpty {
                        ContentUnavailable(
                            title: "No run yet",
                            detail: "Choose a working directory and a model, then ask for something."
                        )
                    }
                    ForEach(model.transcript.turns) { turn in
                        // `.equatable()` is the difference between re-running
                        // every visible turn's body on every publish and
                        // re-running only the turns that changed.
                        TurnView(turn: turn)
                            .equatable()
                            .id(turn.id)
                            .onAppear { if isLast(turn) { pinned = true } }
                            .onDisappear { if isLast(turn) { pinned = false } }
                    }
                }
                .padding(.vertical, 18)
                .frame(maxWidth: Theme.columnWidth)
                // The column, centred: the window is wider than a paragraph.
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .background(Theme.surface)
            .overlay(alignment: .bottomTrailing) {
                if !pinned, let last = model.transcript.turns.last {
                    Button {
                        pinned = true
                        proxy.scrollTo(last.id, anchor: .bottom)
                    } label: {
                        Image(systemName: "arrow.down")
                            .font(.system(size: 12, weight: .semibold))
                            .frame(width: 28, height: 28)
                    }
                    .buttonStyle(.plain)
                    .background(Theme.surfaceRaised, in: Circle())
                    .hairline(14, color: Theme.edge)
                    .padding(14)
                }
            }
            .onChange(of: model.transcript.lines.last) { before, after in
                guard let after, let last = model.transcript.turns.last else { return }
                // The user's own message is always worth scrolling to: they just
                // typed it, and a view left scrolled up would hide it.
                if case .user = after.kind { pinned = true }
                guard pinned else { return }
                if before?.id != after.id {
                    withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(last.id, anchor: .bottom) }
                } else {
                    // Text growth in the turn already on screen: follow it
                    // without an animation per publish.
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
    }

    private func isLast(_ turn: Turn) -> Bool {
        turn.id == model.transcript.turns.last?.id
    }
}

/// One prompt, the work, and the answers.
struct TurnView: View {
    let turn: Turn

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let user = turn.user, case .user(let text) = user.kind {
                UserBubble(text: text)
            }
            if !turn.activity.isEmpty {
                ActivitySection(turn: turn)
            }
            ForEach(turn.answers) { line in
                AnswerLine(line: line).equatable()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension TurnView: Equatable {}

/// The process, collapsed: "Worked for 2m 50s ›".
///
/// Open while the turn is running — a run that shows nothing for two minutes
/// reads as a hang — and collapsed once it finishes, because the result is what
/// the person came for and the process is what they scroll past on the way to
/// the next prompt. The default is `turn.running`, and a click overrides it for
/// good: `expanded ?? turn.running`.
struct ActivitySection: View {
    let turn: Turn

    @State private var expanded: Bool?

    private var open: Bool { expanded ?? turn.running }

    var body: some View {
        DisclosureGroup(isExpanded: Binding(
            get: { open },
            set: { expanded = $0 }
        )) {
            // The durations are read once for the turn and handed to each row as
            // a value: a row that carried the dictionary would compare it in its
            // `.equatable()` on every publish.
            VStack(alignment: .leading, spacing: 8) {
                let durations = turn.durations
                ForEach(turn.activity) { line in
                    LineView(line: line, reasoningDuration: durations[line.id]).equatable()
                }
            }
            .padding(.top, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            HStack(spacing: 6) {
                if turn.running { ProgressView().controlSize(.mini) }
                Text(turn.activityLabel)
                    .font(.callout)
                    .foregroundStyle(Theme.muted)
            }
            .contentShape(Rectangle())
        }
        .tint(Theme.muted)
        .padding(.leading, 2)
    }
}

/// The prompt, as the user's own words: on the right, on a surface, in a bubble.
struct UserBubble: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.body)
            .textSelection(.enabled)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: 560, alignment: .trailing)
            .background(Theme.fillRaised, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .frame(maxWidth: .infinity, alignment: .trailing)
    }
}

/// The result of a turn: an answer, or a failure.
struct AnswerLine: View {
    let line: TranscriptLine

    var body: some View {
        switch line.kind {
        case .assistant(let text):
            AssistantLine(text: text, streaming: line.streaming, blocks: line.blocks, tail: line.tailText)
        case .messageFailed(let error):
            FailedLine(error: error)
        default:
            EmptyView()
        }
    }
}

extension AnswerLine: Equatable {}

/// The assistant text, split by how far along it is.
///
/// An empty message is "thinking…"; a streaming one is the markdown it has
/// finished plus the block it is still writing; a finished one is markdown,
/// selectable, parsed once.
///
/// The streaming case draws `blocks` and `tail`, both handed over by the
/// transcript, and parses nothing: the reducer parsed the finished prefix at its
/// own pace and says where it stopped. That is what keeps the trap the thinking
/// line proved: a selectable `Text` rebuilds its text-kit view on every
/// character, and the model streams ~1.5 characters per chunk, so a 2k-character
/// answer is 1300 publishes; parsing the whole answer on each of them is O(n²) per
/// run. So the finished prefix is structure, parsed at its own pace, and the tail
/// — the block in progress, whose next character can turn `- it` into a rule — is
/// plain text until its blank line arrives.
///
/// The split is not asked for here. It was `Markdown.split(text).tail`, O(n) in
/// the answer on every publish, and it recomputed the boundary: with the reducer
/// scanning on a gate, its `blocks` stop at the oldest blank line and a fresh
/// split's tail starts after the newest, so the text between them was drawn by
/// neither and disappeared while the answer streamed.
struct AssistantLine: View {
    let text: String
    let streaming: Bool
    var blocks: [MarkdownBlock] = []
    var tail: String = ""

    var body: some View {
        if text.isEmpty {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("thinking…").foregroundStyle(Theme.muted)
            }
        } else if streaming {
            VStack(alignment: .leading, spacing: 8) {
                BlocksView(blocks: blocks)
                if !tail.isEmpty {
                    Text(tail)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        } else {
            MarkdownView(text: text)
                // The answer, as markdown, is the thing worth taking away. A run
                // writes several answers around its tool calls, so the copy is
                // per line — "the answer" here means the one being looked at.
                // Not while streaming: the text is not finished, and a copy of a
                // half-arrived answer is a half answer in the pasteboard.
                .overlay(alignment: .topTrailing) {
                    CopyButton(text: text, label: "this answer").padding(4)
                }
        }
    }
}

/// A line of the process, inside the turn's disclosure.
struct LineView: View {
    let line: TranscriptLine
    /// The thinking's duration, from the turn's one pass over its lines.
    let reasoningDuration: TimeInterval?

    // `body` must be declared here, not in an extension: Swift applies the
    // result-builder transform to a `body` in the type's own declaration and
    // not to one in an extension, so a `switch` body in an extension fails with
    // "branches have mismatching types" for branches that are all Views.

    var body: some View {
        // One struct per row kind. A single switch with inline bodies is one
        // enormous result-builder expression, and the type checker gives up on
        // it ("branches have mismatching types", for branches that are all
        // Views). It is also the unit SwiftUI diffs: a row whose TranscriptLine
        // did not change is skipped only if the row is a view of its own.
        switch line.kind {
        case .user(let text):
            // A prompt is never activity, so this branch is unreachable; it is
            // here so the switch is total and stays total when a kind is added.
            UserBubble(text: text)
        case .assistant(let text):
            AssistantLine(text: text, streaming: line.streaming, blocks: line.blocks, tail: line.tailText)
        case .reasoning(let text):
            ReasoningLine(text: text, streaming: line.streaming, duration: reasoningDuration)
        case .messageFailed(let error):
            FailedLine(error: error)
        case .tool(let row):
            ToolLine(row: row)
        case .permission(let outcome):
            PermissionLine(outcome: outcome)
        case .compaction(let row):
            CompactionLine(row: row)
        case .note(let text):
            NoteLine(text: text)
        }
    }
}

/// `ForEach` can then skip a line whose `TranscriptLine` did not change,
/// instead of re-running every visible line's body on every publish.
extension LineView: Equatable {}

/// The model thinking out loud, as progress.
///
/// Open while it streams — ten seconds of a spinner that has not moved reads as
/// a hang, and this is the ten seconds. Collapsed once the answer starts, so it
/// does not push the reply off screen. Subdued because it is not the answer:
/// the model never sees it again, and the user is not reading a result.
struct ReasoningLine: View {
    let text: String
    let streaming: Bool
    /// How long it ran, from the turn's stamps. A history loaded from disk
    /// answers 0, which is the "Reasoned" label with the number left out.
    var duration: TimeInterval? = nil

    @State private var expanded: Bool?

    private var open: Bool { expanded ?? streaming }

    /// How much of the thinking the streaming view lays out.
    ///
    /// A `Text` lays out its whole string on every publish, so a streaming `Text`
    /// of the entire accumulated thinking costs O(n) per update and O(n²) per
    /// run: at 480k characters the app re-lays-out a novella sixteen times a
    /// second. That is the second reason a run crawls while the model thinks —
    /// the first is the per-delta publish in `AppModel`, the third is the
    /// per-delta string copy in `Transcript`.
    ///
    /// The user is watching the model work, not reading the first 400k characters
    /// of its thinking, so the streaming view shows the tail: the part that is
    /// arriving. The finished view is collapsed and rendered once, so it shows
    /// everything the transcript kept — which is itself a tail, bounded at
    /// `Transcript.reasoningKeep`.
    private static let streamingWindow = 1_200

    private var shown: String {
        // `utf8.count` is O(1); `text.count` walks the string, which is the cost
        // this window exists to avoid.
        guard streaming, text.utf8.count > Self.streamingWindow else { return text }
        return "… " + String(text.suffix(Self.streamingWindow))
    }

    /// Not selectable while it streams: a selectable `Text` rebuilds its
    /// text-kit view on every character. A ternary cannot express this —
    /// `.enabled` and `.disabled` are different types.
    @ViewBuilder
    private var reasoningText: some View {
        if streaming {
            Text(shown)
                .font(.callout)
                .foregroundStyle(Theme.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 2)
        } else {
            Text(text)
                .font(.callout)
                .foregroundStyle(Theme.muted)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 2)
        }
    }

    var body: some View {
        DisclosureGroup(isExpanded: Binding(
            get: { open },
            set: { expanded = $0 }
        )) {
            reasoningText
        } label: {
            HStack(spacing: 6) {
                if streaming { ProgressView().controlSize(.mini) }
                Text(label).font(.callout).foregroundStyle(Theme.muted)
            }
            .contentShape(Rectangle())
        }
        .tint(Theme.muted)
    }

    /// "Reasoned for 12s", "Reasoning…" while it goes, and "Reasoned" for a
    /// history whose lines all arrived at one instant.
    private var label: String {
        if streaming { return "Reasoning…" }
        guard let text = DurationFormat.short(duration) else { return "Reasoned" }
        return "Reasoned for \(text)"
    }
}

struct FailedLine: View {
    let error: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 7) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(Theme.danger)
            Text(error)
                .font(.callout)
                .foregroundStyle(Theme.danger)
                .textSelection(.enabled)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.fill, in: RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
        .hairline(Theme.radiusSmall, color: Theme.edge)
    }
}

struct CompactionLine: View {
    let row: CompactionRow

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "arrow.up.left.and.arrow.down.right")
            Text("compacting context — \(row.reason)")
            switch row.state {
            case .running: ProgressView().controlSize(.small)
            case .done: Text("done").foregroundStyle(Theme.muted)
            case .failed(let error): Text("failed: \(error)").foregroundStyle(Theme.danger)
            }
        }
        .font(.caption)
        .foregroundStyle(Theme.muted)
    }
}

struct NoteLine: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(Theme.faint)
    }
}

/// A tool call: a verb, its object, and how long it took.
///
/// The row used to show the tool's name and a folded preview of its result —
/// `read  1. let line = lines[index] ⏎ 2. let line = …` — which named the
/// operation and then spent the line on the output the disclosure shows again.
/// The label is now the core's (`ToolDisplay.summary`): `Read Parser.swift`,
/// `Running npm test`. The result is only ever in the disclosure, which is where
/// the user opens the row to look.
struct ToolLine: View {
    let row: ToolCallRow

    @State private var expanded = false

    private var running: Bool { row.state == .running }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                Text("arguments")
                    .font(.caption2)
                    .foregroundStyle(Theme.muted)
                ArgumentSections(sections: ToolDisplay.sections(tool: row.name, arguments: row.arguments))

                // The output. The row used to store only a 240-character
                // one-liner, so expanding it showed the arguments and never what
                // the tool returned — the half the user opens the row for.
                if case .done(let text) = row.state, !text.isEmpty {
                    Text("result")
                        .font(.caption2)
                        .foregroundStyle(Theme.muted)
                    ScrollView(.vertical, showsIndicators: true) {
                        Text(text)
                            .font(.system(.caption, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                    .frame(maxHeight: 240)
                }

                if case .failed(let error) = row.state {
                    Text(error)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(Theme.danger)
                        .textSelection(.enabled)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        } label: {
            HStack(spacing: 7) {
                glyph
                Text(ToolDisplay.summary(tool: row.name, arguments: row.arguments, running: running))
                    .font(.callout)
                    .foregroundStyle(running ? Color.primary : Theme.muted)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 8)
                elapsed
            }
            .contentShape(Rectangle())
        }
        .tint(Theme.muted)
    }

    @ViewBuilder private var glyph: some View {
        switch row.state {
        case .running: ProgressView().controlSize(.mini)
        case .done: Image(systemName: "checkmark").font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.success)
        case .failed: Image(systemName: "xmark").font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.danger)
        }
    }

    /// The call's own clock, ticking once a second while it runs.
    ///
    /// `TimelineView` and not a timer in the model: the transcript's stamps are
    /// the record, and a live count is a view's business — it is the one piece of
    /// the screen that changes with no event, and it must not cost a publish.
    @ViewBuilder private var elapsed: some View {
        if running {
            TimelineView(.periodic(from: row.startedAt, by: 1)) { context in
                Text(DurationFormat.short(context.date.timeIntervalSince(row.startedAt)) ?? "…")
                    .font(.caption)
                    .foregroundStyle(Theme.faint)
                    .monospacedDigit()
            }
        } else if let text = DurationFormat.short(row.elapsed) {
            Text(text)
                .font(.caption)
                .foregroundStyle(Theme.faint)
                .monospacedDigit()
        }
    }
}

struct PermissionLine: View {
    let outcome: PermissionOutcome

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: outcome.granted ? "hand.raised.beam.fill" : "hand.raised.slash.fill")
                .font(.caption)
                .foregroundStyle(outcome.granted ? Theme.success : Theme.danger)
            Text(
                ToolDisplay.summary(
                    tool: outcome.request.toolName, arguments: outcome.request.arguments, running: false
                )
            )
            .font(.caption)
            .foregroundStyle(Theme.muted)
            Text(outcome.granted ? "allowed" : "refused")
                .font(.caption)
                .foregroundStyle(Theme.muted)
            Text("(\(outcome.scope.rawValue))").font(.caption2).foregroundStyle(Theme.faint)
            Text(outcome.reason).font(.caption2).foregroundStyle(Theme.faint).lineLimit(2)
        }
    }
}

// MARK: - Composer

/// The prompt field, as a card with its controls inside it.
///
/// **Return sends; ⇧Return and ⌘Return write a newline.** It used to be the other
/// way round, on the reasoning that prompts are multi-line — a stack trace, a list
/// of files, a paragraph — and Return-as-send costs a paragraph each time one is
/// being typed. That reasoning is right about the cost and wrong about the key:
/// every chat box on the machine sends on Return, so a Return that quietly writes
/// a newline reads as a broken Send, and the fix is the modifier every one of
/// those boxes uses for the rarer keypress.
///
/// **A disabled Send says why.** It used to be a greyed button next to a finished
/// prompt, and the only way to learn the reason was to go looking for the empty
/// working directory in the status bar. The rule lives in `AppModel.sendBlock`,
/// which is also `send`'s own guard, so the button cannot claim a thing the call
/// does not enforce.
///
/// **The controls are in the card, not the toolbar.** Model, thinking and
/// permission mode are the three settings of *this* prompt, and the toolbar put
/// them 60 points above the field they apply to, in a strip that overflowed into
/// a chevron.
struct ComposerCard: View {
    @EnvironmentObject var model: AppModel
    @Binding var draft: String

    private var enabled: Bool { model.sendBlock(draft) == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Ask for something…", text: $draft, axis: .vertical)
                .lineLimit(1...8)
                .font(.body)
                .onSubmit(submit)
                .disabled(model.busy)

            HStack(spacing: 8) {
                ModelMenu()
                ThinkingMenu()
                ModeMenu()
                if let reason = visibleReason {
                    Text(reason)
                        .font(.caption)
                        .foregroundStyle(Theme.warning)
                        .fixedSize(horizontal: false, vertical: true)
                        .lineLimit(2)
                }
                Spacer(minLength: 8)
                trailing
            }
        }
        .padding(12)
        .frame(maxWidth: Theme.columnWidth)
        .background(Theme.surfaceRaised, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .hairline(Theme.radius, color: Theme.edge)
        .padding(.horizontal, 14)
        .padding(.bottom, 14)
        .frame(maxWidth: .infinity, alignment: .center)
    }

    @ViewBuilder private var trailing: some View {
        if model.transcript.state.isRunning {
            Button {
                model.cancel()
            } label: {
                Image(systemName: "stop.fill")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Theme.accent)
                    .frame(width: 30, height: 30)
                    .background(Theme.fillRaised, in: Circle())
            }
            .buttonStyle(.plain)
            .help("Cancel this run")
        } else {
            Button {
                send()
            } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(enabled ? Color.white : Theme.faint)
                    .frame(width: 30, height: 30)
                    .background(enabled ? Theme.accent : Theme.fillRaised, in: Circle())
            }
            .buttonStyle(.plain)
            .disabled(!enabled)
            .help(model.sendBlock(draft).map(\.reason) ?? "↵ sends, ⇧↵ writes a new line")
        }
    }

    /// The reason worth showing.
    ///
    /// Only the two the user cannot see for themselves: a dead service, and a
    /// scope nothing can run in. `busy` already has a Cancel button in its place,
    /// and an empty field next to a disabled Send explains itself — a caption for
    /// every state is a caption bar.
    private var visibleReason: String? {
        guard let block = model.sendBlock(draft) else { return nil }
        switch block {
        case .notConnected, .noWorkingDir: return block.reason
        case .busy, .emptyDraft: return nil
        }
    }

    /// Return sends; a modifier writes the newline.
    ///
    /// `onSubmit` reports that a submit happened and not which key caused it, so
    /// the modifier comes from AppKit's current event flags, which are current
    /// for the whole of the dispatch that is calling this.
    ///
    /// Shift is the newline key every chat box uses; ⌘ is kept because it is what
    /// this box did before, and a user who learned it should not find it sending.
    /// Option is in the list because SwiftUI's own vertical field inserts a newline
    /// on Option-Return, and the two possible failures are not equal: appending
    /// when AppKit already inserted costs a blank line in a prompt, and not
    /// appending when it did not costs a key that does nothing — the complaint this
    /// flip exists to answer.
    private func submit() {
        let flags = NSEvent.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if flags.contains(.shift) || flags.contains(.command) || flags.contains(.option) {
            draft.append("\n")
            return
        }
        send()
    }

    private func send() {
        let text = draft
        draft = ""
        // …and again on the next main-actor turn.
        //
        // Measured on this box: a vertical `TextField` keeps its text when its
        // binding is written from inside its own `onSubmit`. The send lands — the
        // prompt is in the timeline, the run starts — and the field still holds
        // the prompt, so the next Return sends it a second time. The immediate
        // write is the one that is correct in principle; the deferred one is the
        // one SwiftUI honours, and the pair costs nothing.
        Task { @MainActor in draft = "" }
        model.send(text)
    }
}

/// A setting of this prompt, shown as its current value.
///
/// A `Menu` and not a `Picker`: three of these in a toolbar `Picker` row is a
/// strip of 165-point boxes, and the value that matters is the one currently
/// chosen, which a label states and a `Picker` with `labelsHidden` does not.
struct SettingMenu: View {
    let title: String
    let systemImage: String
    let value: String
    let help: String
    let options: [String]
    let chosen: String
    let choose: (String) -> Void

    var body: some View {
        Menu {
            ForEach(options, id: \.self) { option in
                Button {
                    choose(option)
                } label: {
                    if option == chosen {
                        Label(option, systemImage: "checkmark")
                    } else {
                        Text(option)
                    }
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: systemImage).font(.system(size: 10))
                Text(value).font(.caption).lineLimit(1)
                Image(systemName: "chevron.down").font(.system(size: 8, weight: .semibold))
            }
            .foregroundStyle(Theme.muted)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(Theme.fill, in: RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        // The window's accent is violet, and a borderless menu tints its label
        // with the accent: these are the settings of the prompt, not the
        // window's one coloured thing, so they take the muted ink.
        .tint(Theme.muted)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(help)
    }
}

struct ModelMenu: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        SettingMenu(
            title: "Model",
            systemImage: "cube.box",
            value: model.selectedModel.isEmpty ? "router's choice" : model.selectedModel,
            help: "The model for the next run. \(model.modelPolicy)",
            options: ["(router's choice)"] + model.modelRows.map(\.key),
            chosen: model.selectedModel.isEmpty ? "(router's choice)" : model.selectedModel
        ) { choice in
            model.chooseModel(choice == "(router's choice)" ? "" : choice)
        }
    }
}

struct ThinkingMenu: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        SettingMenu(
            title: "Thinking",
            systemImage: "brain",
            value: model.reasoningEffort?.label ?? "model default",
            help:
                "How much the model may think before answering. Off answers fastest; the model's own default is the slowest thing in this app.",
            options: ["model default"] + ReasoningEffort.allCases.map(\.label),
            chosen: model.reasoningEffort?.label ?? "model default"
        ) { choice in
            // "model default" sends no `reasoning_effort` at all, which leaves
            // the server's default alone. That default is the slow path: on one
            // reasoning model it spent 686,439 characters on private reasoning
            // and produced no answer, where "off" answered in 1.1 seconds.
            model.reasoningEffort = ReasoningEffort.allCases.first { $0.label == choice }
        }
    }
}

struct ModeMenu: View {
    @EnvironmentObject var model: AppModel

    private let names: [(PermissionMode, String)] = [
        (.ask, "Ask"), (.allowReadonly, "Read-only"), (.allowAll, "Allow all"),
    ]

    var body: some View {
        SettingMenu(
            title: "Tools",
            systemImage: "hand.raised",
            value: names.first { $0.0 == model.permissionMode }?.1 ?? "Ask",
            help: "Ask prompts before a risky tool. Read-only refuses writes without asking. Allow all is for demos.",
            options: names.map(\.1),
            chosen: names.first { $0.0 == model.permissionMode }?.1 ?? "Ask"
        ) { choice in
            if let mode = names.first(where: { $0.1 == choice })?.0 { model.permissionMode = mode }
        }
    }
}

// MARK: - Permission sheet

struct PermissionSheet: View {
    @EnvironmentObject var model: AppModel
    let request: PermissionRequest

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Allow \(request.toolName)?")
                .font(.title3)
            Text(request.reason)
                .font(.callout)
                .foregroundStyle(Theme.muted)
            ScrollView {
                ArgumentSections(
                    sections: ToolDisplay.sections(tool: request.toolName, arguments: request.arguments),
                    prominent: true
                )
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 260)
            .padding(10)
            .background(Theme.fill, in: RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
            .hairline(Theme.radiusSmall, color: Theme.hairline)

            HStack {
                Button("Deny") { model.answer(granted: false, scope: .once) }
                Button("Deny always") { model.answer(granted: false, scope: .never) }
                Spacer()
                Button("Allow once") { model.answer(granted: true, scope: .once) }
                Button("Allow for session") {
                    model.answer(granted: true, scope: .session)
                }
                .keyboardShortcut(.return, modifiers: .command)
            }
        }
        .padding(16)
        .frame(width: 460)
    }
}

// MARK: - Small pieces

struct ContentUnavailable: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(spacing: 6) {
            Text(title).font(.headline)
            Text(detail).font(.callout).foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }
}

/// A tool call's arguments, as the core renders them.
///
/// The folding is in `ToolDisplay`, in the core, where a check can reach it:
/// what a `bash` prompt shows is the thing the user is asked to decide on, and a
/// view is not a place an automated check can look. This view only draws the
/// blocks the core produced.
///
/// `prominent` is the permission sheet: there the command is the decision, so it
/// is body text. In a tool row it is history, so it is caption text.
struct ArgumentSections: View {
    let sections: [ToolDisplay.Section]
    var prominent = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if sections.isEmpty {
                Text("(no arguments)").font(.caption).foregroundStyle(Theme.muted)
            }
            ForEach(Array(sections.enumerated()), id: \.offset) { _, section in
                VStack(alignment: .leading, spacing: 2) {
                    if !section.label.isEmpty {
                        Text(section.label)
                            .font(.caption2)
                            .foregroundStyle(Theme.faint)
                    }
                    switch section.style {
                    case .code:
                        Text(section.text)
                            .font(.system(prominent ? .body : .caption, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    case .text:
                        Text(section.text)
                            .font(prominent ? .callout : .caption)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    case .note:
                        Text(section.text)
                            .font(.caption2)
                            .foregroundStyle(Theme.faint)
                    }
                }
            }
        }
    }
}

/// Copy a block of text, with a mark that says it happened.
///
/// The pasteboard write is silent. A button that gives no sign of success is a
/// button the user presses twice, and the second press is a second `clearContents`
/// — which is how a copy button eats the thing the user copied from somewhere
/// else while looking at it. The mark is the point; the icon is the one the
/// fenced code blocks already used, so the two copy affordances are one idiom.
struct CopyButton: View {
    let text: String
    var label = "this"
    @State private var copied = false

    var body: some View {
        Button {
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            pasteboard.setString(text, forType: .string)
            copied = true
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(1.5))
                copied = false
            }
        } label: {
            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                .foregroundStyle(copied ? Theme.success : Theme.muted)
        }
        .buttonStyle(.borderless)
        .controlSize(.mini)
        .help("Copy \(label)")
    }
}

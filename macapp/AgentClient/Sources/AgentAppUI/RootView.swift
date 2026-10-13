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

// MARK: - Root

public struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var draft = ""

    public init() {}

    public var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            Divider()
            ChatScroll()
            Divider()
            Composer(draft: $draft)
        }
        .frame(minWidth: 680, minHeight: 460)
        .toolbar { toolbar }
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

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            Picker("Model", selection: $model.selectedModel) {
                Text("(router's choice)").tag("")
                ForEach(model.modelRows, id: \.key) { row in
                    Text(row.key).tag(row.key)
                }
            }
            .frame(width: 165)
            .labelsHidden()
            .help("The model for the next run. \(model.modelPolicy)")

            Picker("Thinking", selection: $model.reasoningEffort) {
                // `nil` sends no `reasoning_effort` at all, which leaves the
                // server's default alone. That default is the slow path: on one
                // reasoning model it spent 686,439 characters on private
                // reasoning and produced no answer, where "off" answered in 1.1
                // seconds. So the app's own default is `nil` — the model's
                // business — and the control exists to get off it.
                Text("model default").tag(ReasoningEffort?.none)
                ForEach(ReasoningEffort.allCases, id: \.self) { effort in
                    Text(effort.label).tag(ReasoningEffort?.some(effort))
                }
            }
            .frame(width: 118)
            .labelsHidden()
            .help(
                "How much the model may think before answering. Off answers fastest; the model's own default is the slowest thing in this app."
            )

            Picker("Tools", selection: $model.permissionMode) {
                Text("Ask").tag(PermissionMode.ask)
                Text("Read-only").tag(PermissionMode.allowReadonly)
                Text("Allow all").tag(PermissionMode.allowAll)
            }
            .pickerStyle(.segmented)
            .fixedSize()
            .help("Ask prompts before a risky tool. Read-only refuses writes without asking. Allow all is for demos.")

            Menu("Session") {
                Button("New run") { model.newRun() }
                Divider()
                if model.sessions.isEmpty {
                    Text("no stored sessions")
                }
                ForEach(model.sessions, id: \.sessionId) { row in
                    Button {
                        model.open(row.sessionId)
                    } label: {
                        Text("\(row.sessionId.prefix(16)) · \(row.messageCount) msgs")
                    }
                }
                Divider()
                Button("Refresh") { Task { await model.refreshSessions() } }
            }

        }
    }
}

/// Choose the scope the agent works in.
///
/// A free function, called from the status bar: the panel is AppKit's and modal,
/// and the control that opens it is the one that shows the resulting path. It
/// used to sit in the toolbar, which shares 900 points with the window title,
/// two pickers, a segmented control and a menu — the group overflowed into the
/// `>>` chevron, and a control hidden in an overflow menu is a control the user
/// does not know they have.
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

// MARK: - Status

struct StatusBar: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(model.connected ? Color.green : Color.orange)
                .frame(width: 8, height: 8)
            Text(model.status)
                .font(.callout)
                .foregroundStyle(.secondary)
            Divider().frame(height: 12)
            Text(model.transcript.state.label)
                .font(.callout)
                .fontWeight(.medium)
            if model.busy {
                ProgressView().controlSize(.small)
            }
            if let notice = model.notice {
                Text(notice)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Button("OK") { model.notice = nil }.controlSize(.mini)
            }
            Spacer()
            Button {
                chooseWorkingDir(model)
            } label: {
                Text(model.workingDir.isEmpty ? "choose a working directory" : model.workingDir)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .buttonStyle(.plain)
            .help(
                model.workingDir.isEmpty
                    ? "required — the scope the agent works in"
                    : "the scope the agent works in — click to change it\n\(model.workingDir)"
            )
            Button("Models") { Task { await model.refreshModels() } }
                .controlSize(.small)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }
}

// MARK: - Chat

/// The timeline, following the stream.
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
/// line's `onAppear`/`onDisappear` is exactly "the user is at the bottom" /
/// "the user has scrolled away", with no geometry reader per row to pay for.
struct ChatScroll: View {
    @EnvironmentObject var model: AppModel
    @State private var pinned = true

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if model.transcript.lines.isEmpty {
                        ContentUnavailable(
                            title: "No run yet",
                            detail: "Choose a working directory and a model, then ask for something."
                        )
                    }
                    ForEach(model.transcript.lines) { line in
                        // `.equatable()` is the difference between re-running
                        // every visible line's body on every publish and
                        // re-running only the lines that changed.
                        LineView(line: line)
                            .equatable()
                            .id(line.id)
                            .onAppear { if isLast(line) { pinned = true } }
                            .onDisappear { if isLast(line) { pinned = false } }
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .overlay(alignment: .bottomTrailing) {
                if !pinned, let last = model.transcript.lines.last {
                    Button {
                        pinned = true
                        proxy.scrollTo(last.id, anchor: .bottom)
                    } label: {
                        Label("Latest", systemImage: "arrow.down")
                    }
                    .padding(10)
                }
            }
            .onChange(of: model.transcript.lines.last) { before, after in
                guard let after else { return }
                // The user's own message is always worth scrolling to: they just
                // typed it, and a view left scrolled up would hide it.
                if case .user = after.kind { pinned = true }
                guard pinned else { return }
                if before?.id != after.id {
                    withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(after.id, anchor: .bottom) }
                } else {
                    // Text growth in the line already on screen: follow it
                    // without an animation per publish.
                    proxy.scrollTo(after.id, anchor: .bottom)
                }
            }
        }
    }

    private func isLast(_ line: TranscriptLine) -> Bool {
        line.id == model.transcript.lines.last?.id
    }
}

/// The assistant text, split by how far along it is.
///
/// An empty message is "thinking…"; a streaming one is plain `Text`; a finished
/// one is markdown, selectable, parsed once.
///
/// The streaming case is plain text on purpose, for the same reason the thinking
/// line shows a tail: a selectable `Text` rebuilds its text-kit view on every
/// character, and the model streams ~1.5 characters per chunk, so a 2k-character
/// answer is 1300 rebuilds of a view that cannot be laid out incrementally.
/// Parsing markdown per publish is the same trap one layer up — `Markdown.blocks`
/// is O(n) in an answer that grows per publish, so it is O(n²) per run. The
/// finished line is the only one that pays for structure, and it pays once.
struct AssistantLine: View {
    let text: String
    let streaming: Bool

    var body: some View {
        if text.isEmpty {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("thinking…").foregroundStyle(.secondary)
            }
        } else if streaming {
            Text(text)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            MarkdownView(text: text)
        }
    }
}

struct LineView: View {
    let line: TranscriptLine

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
            UserLine(text: text)
        case .assistant(let text):
            AssistantLine(text: text, streaming: line.streaming)
        case .reasoning(let text):
            ReasoningLine(text: text, streaming: line.streaming)
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

struct UserLine: View {
    let text: String

    var body: some View {
        Text(text)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .trailing)
            .background(Color.accentColor.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// The model thinking out loud, as progress.
///
/// Open while it streams — ten seconds of a spinner that has not moved reads as
/// a hang, and this is the ten seconds. Collapsed once the answer starts, so it
/// does not push the reply off screen. Subdued because it is not the answer:
/// the model never sees it again, and the user is not reading a result.
struct ReasoningLine: View {
    let text: String
    let streaming: Bool

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
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 2)
        } else {
            Text(text)
                .font(.callout)
                .foregroundStyle(.secondary)
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
                Text(streaming ? "thinking…" : "thought for a moment")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
        .tint(.secondary)
    }
}

struct FailedLine: View {
    let error: String

    var body: some View {
        Label(error, systemImage: "exclamationmark.triangle.fill")
            .foregroundStyle(.orange)
            .font(.callout)
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
            case .done: Text("done").foregroundStyle(.secondary)
            case .failed(let error): Text("failed: \(error)").foregroundStyle(.orange)
            }
        }
        .font(.callout)
        .foregroundStyle(.secondary)
    }
}

struct NoteLine: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.tertiary)
    }
}

struct ToolLine: View {
    let row: ToolCallRow

    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 6) {
                Text("arguments")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(pretty(row.arguments))
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)

                // The output. The row used to store only a 240-character
                // one-liner, so expanding it showed the arguments and never what
                // the tool returned — the half the user opens the row for.
                if case .done(let text) = row.state, !text.isEmpty {
                    Text("result")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
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
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            HStack(spacing: 6) {
                switch row.state {
                case .running:
                    ProgressView().controlSize(.small)
                case .done:
                    Image(systemName: "checkmark.circle").foregroundStyle(.green)
                case .failed:
                    Image(systemName: "xmark.circle").foregroundStyle(.red)
                }
                Text(row.name).font(.body.monospaced())
                Text(preview(of: row.state))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(8)
        .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 6))
    }
}

struct PermissionLine: View {
    let outcome: PermissionOutcome

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: outcome.granted ? "hand.raised.beam.fill" : "hand.raised.slash.fill")
                .foregroundStyle(outcome.granted ? .green : .red)
            Text("\(outcome.request.toolName) \(outcome.granted ? "allowed" : "refused")")
                .font(.callout)
            Text("(\(outcome.scope.rawValue))").font(.caption).foregroundStyle(.secondary)
            Text(outcome.reason).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }
    }
}

// MARK: - Composer

struct Composer: View {
    @EnvironmentObject var model: AppModel
    @Binding var draft: String

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask for something…", text: $draft, axis: .vertical)
                .lineLimit(1...6)
                .onSubmit(send)
                .disabled(model.busy)
            if model.transcript.state.isRunning {
                Button("Cancel") { model.cancel() }
            } else {
                Button("Send") { send() }
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(model.busy || model.workingDir.isEmpty)
            }
        }
        .padding(10)
    }

    private func send() {
        let text = draft
        draft = ""
        model.send(text)
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
                .foregroundStyle(.secondary)
            ScrollView {
                Text(pretty(request.arguments))
                    .font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
            .frame(maxHeight: 220)

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
            Text(detail).font(.callout).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

func pretty(_ value: JSONValue) -> String {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    guard let data = try? encoder.encode(value), let text = String(data: data, encoding: .utf8) else {
        return "\(value)"
    }
    return text
}

/// A tool row's label: the state in one line. The row keeps the full result and
/// the disclosure shows it; a label folds it, in the core, where a check can
/// reach the folding.
func preview(of state: ToolCallRow.State) -> String {
    switch state {
    case .running: return "running…"
    case .done(let text): return Transcript.oneLine(text)
    case .failed(let error): return Transcript.oneLine(error)
    }
}

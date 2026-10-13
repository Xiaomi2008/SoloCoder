import AgentAppCore
import AgentClient
import AppKit
import SwiftUI

// The app shell: a chat over the event stream, a tool timeline, a permission
// sheet, a model picker, a session browser.
//
// Everything here renders `AppModel`, and `AppModel` renders the transcript the
// service's events produced. There is no second source of truth for what
// happened in a run — which is why a view can be this thin.

/// A SwiftPM executable is not a bundle, so the process starts as a
/// background-only app: no Dock tile, no menu bar, and a window that opens
/// behind everything with nothing on screen saying anything launched. The user
/// sees a command that returned and no app.
///
/// `.regular` is the line that makes it a normal app; the delayed raise is
/// because SwiftUI has not created the window at `didFinishLaunching`.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // A process launched from a shell starts prohibited (no Dock tile, no
        // menu bar, no activation). `.regular` is the line that makes it a
        // normal app. The enum is 0=regular, 1=accessory, 2=prohibited — the
        // reverse of what the numbers suggest, and getting it backwards turns a
        // visible app into a background one.
        NSApp.setActivationPolicy(.regular)
        raiseWindows(after: 0.2)
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        raiseWindows(after: 0.05)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    /// Raise the app's own window, and only that one.
    ///
    /// SwiftUI keeps hidden helper windows in `NSApp.windows` — the alert host,
    /// the menu bar's window. Ordering *those* to the front shows them: a small
    /// empty panel on top of everything, which is what a user sees instead of
    /// the app. Titled and visible is the filter.
    private func raiseWindows(after delay: TimeInterval) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            let main = NSApp.windows.first { $0.isVisible && !$0.title.isEmpty }
            // `center()` centers on the screen the window is already on, which
            // for a window restored at a saved frame means a display the user is
            // not looking at. Center on the screen with the focus, near the top.
            if let main, let screen = NSScreen.main {
                let visible = screen.visibleFrame
                let origin = CGPoint(
                    x: visible.midX - main.frame.width / 2,
                    y: visible.maxY - main.frame.height - 40
                )
                main.setFrameOrigin(origin)
            }
            main?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}

@main
struct SoloCoderApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("SoloCoder") {
            RootView()
                .environmentObject(model)
                .task { model.start() }
        }
        .defaultSize(width: 900, height: 620)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New run") { model.newRun() }
                    .keyboardShortcut("n", modifiers: .command)
            }
        }
    }
}

// MARK: - Root

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var draft = ""

    var body: some View {
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
            .frame(width: 220)
            .help("The model for the next run. \(model.modelPolicy)")

            Picker("Tools", selection: $model.permissionMode) {
                Text("Ask").tag(PermissionMode.ask)
                Text("Read-only").tag(PermissionMode.allowReadonly)
                Text("Allow all").tag(PermissionMode.allowAll)
            }
            .pickerStyle(.segmented)
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

            Button("Working dir…") { chooseWorkingDir() }
                .help(model.workingDir.isEmpty ? "required — the scope the agent works in" : model.workingDir)
        }
    }

    private func chooseWorkingDir() {
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
            if !model.workingDir.isEmpty {
                Text(model.workingDir)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Button("Models") { Task { await model.refreshModels() } }
                .controlSize(.small)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }
}

// MARK: - Chat

struct ChatScroll: View {
    @EnvironmentObject var model: AppModel

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
                        LineView(line: line).id(line.id)
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .onChange(of: model.transcript.lines.count) { _, count in
                guard let last = model.transcript.lines.last?.id else { return }
                withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(last, anchor: .bottom) }
                _ = count
            }
        }
    }
}

struct LineView: View {
    let line: TranscriptLine

    var body: some View {
        switch line.kind {
        case .user(let text):
            Text(text)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .background(Color.accentColor.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))

        case .assistant(let text):
            if text.isEmpty {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("thinking…").foregroundStyle(.secondary)
                }
            } else {
                Text(text)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

        case .messageFailed(let error):
            Label(error, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
                .font(.callout)

        case .tool(let row):
            ToolLine(row: row)

        case .permission(let outcome):
            PermissionLine(outcome: outcome)

        case .compaction(let row):
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

        case .note(let text):
            Text(text)
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
    }
}

struct ToolLine: View {
    let row: ToolCallRow

    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 6) {
                Text(pretty(row.arguments))
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
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

func preview(of state: ToolCallRow.State) -> String {
    switch state {
    case .running: return "running…"
    case .done(let text): return text
    case .failed(let error): return error
    }
}

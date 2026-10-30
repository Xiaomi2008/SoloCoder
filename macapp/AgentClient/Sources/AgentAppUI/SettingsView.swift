import AgentAppCore
import AgentClient
import AppKit
import ApplicationServices
import SwiftUI

// The app's defaults, in one window.
//
// The composer owns the per-prompt controls; this window owns the same three
// choices as *defaults* for the next launch. The two read the same `AppModel`
// properties, so there is no second copy of a setting to drift apart.
//
// The model table is deliberately not a picker: it is the service's answer to
// "what can this machine run", with the capabilities the service actually
// reports. Choosing one still happens in the composer or the command palette.
public struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @State private var revealError: String?

    // The add-server form. It is deliberately one endpoint at a time: the
    // durable record is the config, and the model table is read back after the
    // service has discovered the new server.
    @State private var endpointName = ""
    @State private var endpointBaseURL = ""
    @State private var endpointLabel = ""
    @State private var endpointWireApi = "openai-chat"
    @State private var endpointLocal = true
    @State private var endpointRequiresKey = false
    @State private var endpointBusy = false
    @State private var endpointNotice: String?
    @State private var endpointError: String?

    private let endpointWires: [(String, String)] = [
        ("openai-chat", "OpenAI-compatible"),
        ("openai-responses", "OpenAI Responses"),
        ("anthropic-messages", "Anthropic-compatible"),
        ("ollama-chat", "Ollama"),
        ("gemini", "Gemini"),
    ]

    public init() {}

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                SettingsSection(title: "Connection") {
                    HStack(spacing: 8) {
                        Circle()
                            .fill(model.connected ? Theme.success : Theme.warning)
                            .frame(width: 8, height: 8)
                        Text(model.connected ? "connected" : "not connected")
                            .font(.callout)
                        Spacer()
                        Text(model.status)
                            .font(.caption2)
                            .foregroundStyle(Theme.faint)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .help(model.status)
                    }
                }

                SettingsSection(title: "Working directory") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(model.workingDir.isEmpty ? "No directory chosen" : model.workingDir)
                            .font(.system(.callout, design: .monospaced))
                            .foregroundStyle(model.workingDir.isEmpty ? Theme.warning : .primary)
                            .lineLimit(2)
                            .truncationMode(.middle)
                            .textSelection(.enabled)
                        HStack(spacing: 8) {
                            Button("Choose…") { chooseWorkingDir(model) }
                                .buttonStyle(.borderedProminent)
                            Button("Reveal in Finder") { revealWorkingDir() }
                                .buttonStyle(.bordered)
                                .disabled(model.workingDir.isEmpty)
                            Spacer()
                        }
                        if let revealError {
                            Text(revealError)
                                .font(.caption2)
                                .foregroundStyle(Theme.danger)
                        } else {
                            Text("The agent's tools work inside this directory.")
                                .font(.caption2)
                                .foregroundStyle(Theme.faint)
                        }
                    }
                }

                SettingsSection(title: "Defaults for the next run") {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 8) {
                            ModelMenu()
                            ThinkingMenu()
                            ModeMenu()
                            Spacer()
                        }
                        Text("These are remembered between launches. The composer shows the same values for the next prompt.")
                            .font(.caption2)
                            .foregroundStyle(Theme.faint)
                    }
                }

                SettingsSection(title: "Computer use") {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("Expose macOS GUI tools", isOn: $model.computerUse)
                            .disabled(model.busy)
                        if model.computerUse {
                            ComputerUsePermissions()
                        }
                        Text(
                            "Off by default. Adds screenshot, click, double_click, type_text, "
                                + "key_combination, move_mouse, scroll, and wait. "
                                + "Requires the computer-use dependencies and macOS Screen Recording "
                                + "and Accessibility permissions. Changing it restarts the service; "
                                + "stop a run first."
                        )
                        .font(.caption2)
                        .foregroundStyle(Theme.faint)
                        if !model.serviceAvailable {
                            Text(model.connected
                                ? "Preview mode: the toggle is saved, but this window has no service to restart."
                                : "The toggle is saved and applies when the app connects to its service.")
                                .font(.caption2)
                                .foregroundStyle(Theme.faint)
                        }
                    }
                }

                SettingsSection(title: "Browser use") {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("Let the agent use its own browser", isOn: $model.browserUse)
                            .disabled(model.busy)
                        Toggle("Remember sign-ins", isOn: $model.browserRemembersSignIns)
                            .disabled(model.busy || !model.browserUse)
                            .help("Keep the browser's profile in ~/.openagent/browser-profile. Sign in by hand in the agent's window; the agent never types passwords.")
                        Toggle("Use my installed Google Chrome", isOn: $model.browserUsesChrome)
                            .disabled(model.busy || !model.browserUse)
                            .help("Drive /Applications/Google Chrome instead of Playwright's Chrome for Testing. It gets its own profile; your Chrome windows and sign-ins are untouched.")
                        Text(
                            "Off by default. Adds browser_navigate, browser_snapshot, browser_find, browser_click, "
                                + "browser_type, browser_select_option, browser_press_key, browser_back, "
                                + "browser_screenshot, and browser_close. The agent gets a separate "
                                + "Chromium window with none of your sign-ins; public sites and localhost "
                                + "only. In Ask mode it asks before consequential actions: submitting forms, "
                                + "signing in, sending, buying, deleting, unlabelled buttons. Requires "
                                + "`pip install 'openagent[browser]'` and `playwright install chromium`. "
                                + "Changing it restarts the service; stop a run first."
                        )
                        .font(.caption2)
                        .foregroundStyle(Theme.faint)
                    }
                }

                SettingsSection(title: "Add a model server") {
                    VStack(alignment: .leading, spacing: 10) {
                        Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 8) {
                            GridRow {
                                Text("Name").font(.caption2).foregroundStyle(Theme.faint)
                                SettingsField(placeholder: "nas", text: $endpointName)
                            }
                            GridRow {
                                Text("Base URL").font(.caption2).foregroundStyle(Theme.faint)
                                SettingsField(placeholder: "http://192.168.4.34:8080/v1", text: $endpointBaseURL)
                            }
                            GridRow {
                                Text("Wire API").font(.caption2).foregroundStyle(Theme.faint)
                                Picker("Wire API", selection: $endpointWireApi) {
                                    ForEach(endpointWires, id: \.0) { wire in
                                        Text(wire.1).tag(wire.0)
                                    }
                                }
                                .labelsHidden()
                                .pickerStyle(.menu)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            GridRow {
                                Text("Label").font(.caption2).foregroundStyle(Theme.faint)
                                SettingsField(placeholder: "optional display name", text: $endpointLabel)
                            }
                        }

                        HStack(spacing: 14) {
                            Toggle("Local network", isOn: $endpointLocal)
                            Toggle("Requires API key", isOn: $endpointRequiresKey)
                            Spacer()
                            Button {
                                Task { await addEndpoint() }
                            } label: {
                                Label("Add & discover", systemImage: "plus")
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(
                                endpointBusy
                                    || endpointName.trimmingCharacters(in: .whitespaces).isEmpty
                                    || endpointBaseURL.trimmingCharacters(in: .whitespaces).isEmpty
                                    || !model.serviceAvailable
                            )
                        }

                        if endpointBusy {
                            HStack(spacing: 6) {
                                ProgressView().controlSize(.small)
                                Text("Adding and discovering…").font(.caption2).foregroundStyle(Theme.muted)
                            }
                        } else if let endpointError {
                            Text(endpointError).font(.caption2).foregroundStyle(Theme.danger)
                        } else if let endpointNotice {
                            Text(endpointNotice).font(.caption2).foregroundStyle(Theme.success)
                        } else if !model.serviceAvailable {
                            Text(model.connected
                                ? "Preview mode: this window has no service, so it cannot add or discover a server. Run the SoloCoder app for that."
                                : "Not connected to the service — add & discover needs the running SoloCoder service.")
                                .font(.caption2)
                                .foregroundStyle(Theme.faint)
                        } else {
                            Text("Base URL is the server root — usually ends in /v1 for OpenAI-compatible servers. The service appends its model-list path, discovers what is served, and refreshes the table below.")
                                .font(.caption2)
                                .foregroundStyle(Theme.faint)
                        }
                    }
                }

                SettingsSection(title: "Models", trailing: {
                    Button {
                        Task { await model.refreshModels() }
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                            .font(.caption)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Theme.accent)
                    .disabled(!model.connected)
                    .help(model.connected ? "Reload the model table" : "connect to the service first")
                }) {
                    if model.modelRows.isEmpty {
                        Text(model.connected ? "No models loaded." : "Connect to the service to load models.")
                            .font(.caption)
                            .foregroundStyle(Theme.faint)
                    } else {
                        ModelTable(rows: model.modelRows)
                    }
                }

                HStack {
                    Text(model.modelPolicy.isEmpty ? "no model policy reported" : model.modelPolicy)
                        .font(.caption2)
                        .foregroundStyle(Theme.faint)
                    Spacer()
                    Text("protocol 1")
                        .font(.caption2)
                        .foregroundStyle(Theme.faint)
                }
            }
            .padding(20)
        }
        .frame(width: 600, height: 740)
        .background(Theme.surface)
        .tint(Theme.accent)
    }

    private func addEndpoint() async {
        endpointBusy = true
        endpointNotice = nil
        endpointError = nil
        defer { endpointBusy = false }

        let outcome = await model.addEndpoint(
            name: endpointName.trimmingCharacters(in: .whitespaces),
            baseURL: endpointBaseURL.trimmingCharacters(in: .whitespaces),
            wireApi: endpointWireApi,
            label: endpointLabel.trimmingCharacters(in: .whitespaces),
            local: endpointLocal,
            requiresKey: endpointRequiresKey
        )
        guard let error = outcome.error else {
            guard let result = outcome.result else {
                endpointError = "the service answered without a result"
                return
            }
            if let discoveryError = result.discoveryError {
                endpointError = "added \(result.endpoint.name), but discovery failed: \(discoveryError)"
                return
            }
            let count = result.models.count
            endpointNotice = "added \(result.endpoint.name) · \(count) model\(count == 1 ? "" : "s") discovered"
            endpointName = ""
            endpointBaseURL = ""
            endpointLabel = ""
            return
        }
        endpointError = error
    }

    private func revealWorkingDir() {
        guard !model.workingDir.isEmpty else { return }
        let url = URL(fileURLWithPath: model.workingDir)
        guard FileManager.default.fileExists(atPath: model.workingDir) else {
            revealError = "that directory no longer exists"
            return
        }
        revealError = nil
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}

/// A settings field in the app's own language.
///
/// `.roundedBorder` is the system's field chrome: its focus ring is the system
/// accent, which is blue on a machine set to blue, so a focused field in a
/// violet window is the one blue thing on screen. The style here is the
/// composer's: a recessed fill, a hairline, and the app's accent on focus.
private struct SettingsField: View {
    let placeholder: String
    @Binding var text: String
    @FocusState private var focused: Bool

    var body: some View {
        TextField(placeholder, text: $text)
            .textFieldStyle(.plain)
            .font(.callout)
            .focused($focused)
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .background(
                Theme.surface,
                in: RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous)
                    .strokeBorder(focused ? Theme.accent : Theme.hairline, lineWidth: 1)
            )
            .frame(maxWidth: .infinity)
    }
}

/// A titled card, with an optional control in the title row.
/// Whether macOS has granted what the GUI tools need. Without Screen Recording
/// a screenshot shows only the wallpaper; without Accessibility every click and
/// keystroke is dropped. Both silently, so the agent cannot tell — the person
/// has to see it here. The grants belong to this app: the service it launches
/// inherits them. A rebuilt, unsigned development app is a new app to macOS
/// and loses both.
private struct ComputerUsePermissions: View {
    @State private var screen = CGPreflightScreenCaptureAccess()
    @State private var accessibility = AXIsProcessTrusted()

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            row("Screen Recording", granted: screen, pane: "Privacy_ScreenCapture") {
                _ = CGRequestScreenCaptureAccess()
            }
            row("Accessibility", granted: accessibility, pane: "Privacy_Accessibility") {
                // The value of `kAXTrustedCheckOptionPrompt`, which Swift 6
                // refuses to read as shared mutable state.
                _ = AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
            }
        }
        .font(.caption)
        // The grants change in System Settings, not here; look again whenever
        // the person comes back to this window.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            screen = CGPreflightScreenCaptureAccess()
            accessibility = AXIsProcessTrusted()
        }
    }

    private func row(_ name: String, granted: Bool, pane: String, request: @escaping () -> Void) -> some View {
        HStack(spacing: 6) {
            Image(systemName: granted ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(granted ? Color.green : Color.orange)
            Text(granted ? "\(name): allowed" : "\(name): not allowed")
            if !granted {
                Button("Open Settings") {
                    request()
                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
                        NSWorkspace.shared.open(url)
                    }
                }
                .controlSize(.small)
            }
        }
    }
}

private struct SettingsSection<Content: View, Trailing: View>: View {
    let title: String
    let trailing: Trailing
    let content: Content

    init(
        title: String,
        @ViewBuilder trailing: () -> Trailing = { EmptyView() },
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.trailing = trailing()
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(.headline)
                Spacer()
                trailing
            }
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surfaceRaised, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .hairline(Theme.radius, color: Theme.edge)
    }
}

/// The service's model rows, as a compact table.
private struct ModelTable: View {
    let rows: [ModelRow]

    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 0) {
            GridRow {
                Text("model").font(.caption2).foregroundStyle(Theme.faint)
                Text("provider").font(.caption2).foregroundStyle(Theme.faint)
                Text("context").font(.caption2).foregroundStyle(Theme.faint)
                Text("tools").font(.caption2).foregroundStyle(Theme.faint)
                Text("vision").font(.caption2).foregroundStyle(Theme.faint)
            }
            .padding(.bottom, 4)
            ForEach(rows, id: \.key) { row in
                GridRow {
                    Text(row.display)
                        .font(.callout)
                        .lineLimit(1)
                        .help(row.key)
                    Text(row.provider)
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(1)
                    Text("\(row.caps.contextWindow)")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(Theme.muted)
                        .monospacedDigit()
                    Text(row.caps.tools ? (row.caps.probed ? "yes" : "claimed") : "no")
                        .font(.caption)
                        .foregroundStyle(
                            row.caps.tools
                                ? (row.caps.probed ? Theme.success : Theme.warning)
                                : Theme.faint
                        )
                        .help(row.caps.probed ? "tool support observed" : "tool support claimed, not probed")
                    Text(row.caps.vision ? "yes" : "no")
                        .font(.caption)
                        .foregroundStyle(row.caps.vision ? Theme.success : Theme.faint)
                        .help(
                            row.caps.vision
                                ? "vision: the model can read images (screenshot → model)"
                                : "vision not declared for this endpoint"
                        )
                }
                .padding(.vertical, 3)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(Theme.hairline).frame(height: 1)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

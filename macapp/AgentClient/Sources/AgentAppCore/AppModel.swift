import AgentAppCore
import AgentClient
import AppKit
import Combine
import Foundation

/// The app's state, and the only thing the views talk to.
///
/// In `AgentAppCore`, not in the executable target: this is the app's logic, and
/// a target a test cannot import is a target a test cannot check. The views are
/// the only thing left in `AgentApp`.
///
/// Three rules this class's shape encodes:
///
/// * **One run per connection.** One client, and `busy` gating the composer.
///   The server refuses a second `run.start` on a busy connection; the app
///   refuses it first, with a reason the user can read.
/// * **The transcript is folded from events, never written by the UI.** Every
///   change goes through `Transcript.apply`, so what is on screen is what the
///   service said. The two exceptions are the user's own message (not in the
///   stream) and a call that never reached the service — `failLocally`.
/// * **The session id comes from the service.** The app does not invent one: it
///   stores `run.start`'s `session_id` and sends it on the next run, so the
///   conversation continues, and a relaunch can resume it by name.
@MainActor
public final class AppModel: ObservableObject {
    @Published public var transcript = Transcript()
    /// How full the conversation is, from the last `context_usage` (or the
    /// estimate `session.load` returns). Nil for a new, empty conversation.
    @Published public var contextUsed: Int?
    /// Where the service compacts, from the last run. Nil before one.
    @Published public var compactAt: Int?
    private var runContextWindow: Int?
    /// Mirrored from the transcript so `.sheet(item:)` has an `Identifiable`
    /// value to present.
    @Published public var promptToAnswer: PermissionRequest?
    @Published public var modelRows: [ModelRow] = []
    /// The agent's browser, live. Observed by the panel alone — see its type.
    public let browserPanel = BrowserPanelModel()
    @Published public var modelPolicy = ""
    @Published public var sessions: [SessionRow] = []
    /// The model the next run asks for, `""` meaning "the router's choice".
    /// Remembered between launches, and validated against the model table the
    /// first time it loads — see `restorableModel`.
    @Published public var selectedModel = "" {
        didSet {
            Self.store(selectedModel, forKey: Self.modelKey)
            clampEffort()
        }
    }

    /// The thinking levels the selected model takes: its own list when the
    /// service has one (the ChatGPT backend refuses anything else with a 400),
    /// otherwise the generic levels.
    public var availableEfforts: [ReasoningEffort] {
        let listed = modelRows.first { $0.key == selectedModel }?.caps.reasoningEfforts ?? []
        let efforts = ReasoningEffort.allCases.filter { listed.contains($0.rawValue) }
        return efforts.isEmpty ? ReasoningEffort.generic : efforts
    }

    /// A remembered level the newly chosen model cannot take falls back to its
    /// default, rather than failing the next run.
    private func clampEffort() {
        if let effort = reasoningEffort, !availableEfforts.contains(effort) {
            reasoningEffort = nil
        }
    }
    /// The scope the agent works in, remembered between launches.
    ///
    /// A launch with no directory refuses `run.start` — "choose a working
    /// directory — it is the scope the agent works in" — so every launch used to
    /// open with a modal the user clears before typing anything, and the first
    /// prompt of the session is the one that gets refused. The path is stored
    /// when it is set and restored at launch.
    @Published public var workingDir = "" {
        didSet {
            if workingDir.isEmpty {
                UserDefaults.standard.removeObject(forKey: Self.workingDirKey)
            } else {
                UserDefaults.standard.set(workingDir, forKey: Self.workingDirKey)
            }
        }
    }
    /// What a risky tool may do, remembered between launches. `ask` is the
    /// default and the stored value is only ever what the user picked in the
    /// toolbar, which is on screen the whole time it is in force.
    ///
    /// `.allowAll` is the unattended mode: the run starts with a gate that never
    /// asks, and a mode switched to it mid-run is enforced here too — a
    /// `permission_requested` is answered immediately instead of opening the
    /// sheet. It is not an OS sandbox; the built-in file tools are scoped to the
    /// working directory, but shell commands are not.
    @Published public var permissionMode: PermissionMode = .ask {
        didSet {
            UserDefaults.standard.set(permissionMode.rawValue, forKey: Self.modeKey)
            let mode = permissionMode
            // A run's gate and file-tool scope are fixed at `run.start`; the
            // service's `run.permission_mode` changes both for the live run.
            // Without it, switching to Auto mid-run would only answer prompts
            // while the built-in file tools kept their old scope.
            if let client, let runId = transcript.runId, transcript.state.isRunning, runId != "…" {
                Task {
                    do {
                        try await client.setPermissionMode(runId: runId, permissionMode: mode)
                    } catch {
                        banner = "the permission mode did not reach the service: \(Self.describe(error))"
                    }
                }
            }
            autoAnswerIfNeeded()
        }
    }
    /// How much the model may think before answering. `nil` means "model
    /// decides", which sends nothing — and on a reasoning model that default is
    /// the slow path: measured at ~10s to the first answer character, and in one
    /// run 686,439 characters of private reasoning with no answer at all.
    /// Remembered, because "model decides" is the setting that costs.
    @Published public var reasoningEffort: ReasoningEffort? = nil {
        didSet { Self.store(reasoningEffort?.rawValue, forKey: Self.effortKey) }
    }
    /// Whether the service exposes the macOS GUI tools (`screenshot`, `click`,
    /// ...). Off by default: every tool schema is prompt the model reads before
    /// its first token, and these tools drive the machine a person is using.
    /// Remembered, because it is a choice about what the agent may do, not a
    /// per-prompt setting.
    ///
    /// Changing it restarts the service: the tool set is fixed when `serve`
    /// starts, so a flag can only be passed by a new process. The conversation
    /// in the window survives the restart; the connection and any run do not.
    @Published public var computerUse = false {
        didSet {
            UserDefaults.standard.set(computerUse, forKey: Self.computerUseKey)
            guard computerUse != oldValue, client != nil else { return }
            Task { await restartService() }
        }
    }
    /// Whether the service exposes the browser tools (`browser_navigate`,
    /// `browser_click`, ...), which drive the agent's own Chromium window — a
    /// separate profile, not the user's browser. A launch flag, like
    /// `computerUse`, so changing it restarts the service the same way.
    @Published public var browserUse = false {
        didSet {
            UserDefaults.standard.set(browserUse, forKey: Self.browserUseKey)
            guard browserUse != oldValue, client != nil else { return }
            Task { await restartService(changed: "browser use", on: browserUse) }
        }
    }
    /// Whether the agent's browser keeps its profile between runs, so a site
    /// the user signed into by hand stays signed in. Off by default: a
    /// remembered session is something a misled agent can act with.
    @Published public var browserRemembersSignIns = false {
        didSet {
            UserDefaults.standard.set(browserRemembersSignIns, forKey: Self.browserProfileKey)
            guard browserRemembersSignIns != oldValue, client != nil, browserUse else { return }
            Task { await restartService(changed: "remembered browser sign-ins", on: browserRemembersSignIns) }
        }
    }
    /// Drive the installed Google Chrome instead of Playwright's Chrome for
    /// Testing — the ordinary browser, with a profile of its own, never the
    /// user's.
    @Published public var browserUsesChrome = false {
        didSet {
            UserDefaults.standard.set(browserUsesChrome, forKey: Self.browserChromeKey)
            guard browserUsesChrome != oldValue, client != nil, browserUse else { return }
            Task { await restartService(changed: "using Google Chrome", on: browserUsesChrome) }
        }
    }
    @Published public var status = "not connected"
    @Published public var busy = false
    @Published public var connected = false
    /// True when there is an actual client, not merely a preview's painted
    /// "connected" state. The preview sets `connected` for its visuals and has
    /// no service; actions that need the socket must use this instead.
    public var serviceAvailable: Bool { connected && client != nil }
    /// An error to show. Kept separate from `notice`: an informational line
    /// that pops a modal titled "the service said no" teaches the user that
    /// alerts here are noise.
    @Published public var banner: String?
    /// An informational line for the status bar.
    @Published public var notice: String?
    /// Whether the ⌘K command palette is showing. It is view state, but it
    /// lives here because the menu command that opens it belongs to the app
    /// scene, which has the model and not the view.
    @Published public var paletteOpen = false

    public private(set) var sessionId: String?
    private let sidecar = Sidecar()
    private var client: AgentServiceClient?
    private var streamTask: Task<Void, Never>?
    private var terminalTypes: Set<String> = Wire.terminalEventTypes
    /// Requests already answered by auto mode. A prompt can be applied again by
    /// the next non-delta event while the server still waits, so the id is the
    /// guard against a second `permission.respond`.
    private var autoAnsweredRequests: Set<String> = []

    private static let workingDirKey = "openagent.workingDir"
    private static let modelKey = "openagent.model"
    private static let modeKey = "openagent.permissionMode"
    private static let effortKey = "openagent.reasoningEffort"
    private static let computerUseKey = "openagent.computerUse"
    private static let browserUseKey = "openagent.browserUse"
    private static let browserProfileKey = "openagent.browserRemembersSignIns"
    private static let browserChromeKey = "openagent.browserUsesChrome"

    /// Store a choice, and remove the key when the choice is "no choice" — a
    /// stored empty string and no key at all mean the same thing, and only one
    /// of them survives a `defaults read`.
    private static func store(_ value: String?, forKey key: String) {
        if let value, !value.isEmpty { UserDefaults.standard.set(value, forKey: key) }
        else { UserDefaults.standard.removeObject(forKey: key) }
    }

    /// The stored scope, kept only while it is still a directory.
    ///
    /// A folder the user deleted over the weekend is not a scope, and restoring
    /// its path means the status bar names a folder that is gone and the run is
    /// refused by the service for a reason the screen does not show. An empty
    /// string asks for a folder, which is the honest state.
    public static func restorableWorkingDir(_ stored: String?) -> String {
        guard let stored, !stored.isEmpty else { return "" }
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: stored, isDirectory: &isDirectory),
            isDirectory.boolValue
        else { return "" }
        return stored
    }

    /// The stored model, kept only while the table says it exists.
    ///
    /// A model row is a `provider/model` key into a table that changes: the LAN
    /// box goes offline, a provider is removed, a name is retired. A restored
    /// key that is not in the table shows in the toolbar as a model the picker
    /// has no row for, and every run is refused for a reason the screen does not
    /// show — the same defect the credential store had, one layer up. An empty
    /// `rows` is "the table has not loaded", which is not evidence of anything,
    /// so the key survives until `refreshModels` can check it.
    public static func restorableModel(_ stored: String?, in rows: [ModelRow]) -> String {
        guard let stored, !stored.isEmpty else { return "" }
        guard !rows.isEmpty else { return stored }
        return rows.contains { $0.key == stored } ? stored : ""
    }

    /// The stored permission mode; anything the enum does not know is the default.
    public static func restorableMode(_ stored: String?) -> PermissionMode {
        PermissionMode(rawValue: stored ?? "") ?? .ask
    }

    /// The stored reasoning effort. `nil` is a real choice — "model decides" —
    /// so an absent key and a stored `"none"` are different answers.
    public static func restorableEffort(_ stored: String?) -> ReasoningEffort? {
        guard let stored, !stored.isEmpty else { return nil }
        return ReasoningEffort(rawValue: stored)
    }

    public init() {
        let stored = UserDefaults.standard
        workingDir = Self.restorableWorkingDir(stored.string(forKey: Self.workingDirKey))
        selectedModel = Self.restorableModel(stored.string(forKey: Self.modelKey), in: [])
        permissionMode = Self.restorableMode(stored.string(forKey: Self.modeKey))
        reasoningEffort = Self.restorableEffort(stored.string(forKey: Self.effortKey))
        computerUse = stored.bool(forKey: Self.computerUseKey)
        browserUse = stored.bool(forKey: Self.browserUseKey)
        browserRemembersSignIns = stored.bool(forKey: Self.browserProfileKey)
        browserUsesChrome = stored.bool(forKey: Self.browserChromeKey)
    }

    /// A non-bundled app has no Console entry that a developer finds quickly, so
    /// the app's own story goes to stderr — which is where `swift run` and the
    /// checks' log files already look.
    private func log(_ message: String) {
        let line = "[solocoder] \(Date()) \(message)\n"
        if let data = line.data(using: .utf8) { FileHandle.standardError.write(data) }
    }

    deinit {
        streamTask?.cancel()
        flushTask?.cancel()
    }

    // MARK: lifecycle

    public func start() {
        // Quitting takes the sidecar with it. An app that leaves a service bound
        // to a socket behind is an app whose next launch talks to the previous
        // launch's server — and the socket path is derived from the pid, so the
        // orphan is not even visible in the new launch's logs.
        //
        // `kill` is not covered: SIGTERM does not post this notification, so a
        // killed app still leaves its sidecar. The shipped answer is a watchdog
        // in the service that exits when its stdin closes (the parent's pipe
        // EOFs when the parent dies); that is a service-side change, not a
        // view-side one.
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.stop()
        }

        Task { await connectService() }
    }

    /// Apply a setting that is fixed at service launch.
    ///
    /// The tool set is decided when `serve` starts, so a new flag means a new
    /// process. A run in flight is left alone: toggling a setting must not
    /// silently cancel work. The banner says what to do instead.
    public func restartService() async {
        await restartService(changed: "computer use", on: computerUse)
    }

    func restartService(changed setting: String, on: Bool) async {
        guard client != nil else { return }
        if busy {
            banner = "stop the run before changing \(setting)"
            return
        }
        stop()
        await connectService()
        if connected {
            notice = "\(setting) is now \(on ? "on" : "off") — service restarted"
        }
    }

    private func connectService() async {
        do {
            try await sidecar.start(
                computerUse: computerUse, browserUse: browserUse,
                browserProfile: browserUse && browserRemembersSignIns,
                browserChrome: browserUse && browserUsesChrome
            )
            let client = AgentServiceClient(
                endpoint: ServiceEndpoint(socketPath: sidecar.socketPath, clientName: "solocoder-app")
            )
            self.client = client
            let info = try await client.connect()
            terminalTypes = await client.terminalEventTypes
            // A restart keeps the conversation on screen. A fresh launch has
            // no turns and gets the transcript built from the new connection.
            if transcript.turns.isEmpty {
                transcript = Transcript(terminalTypes: terminalTypes)
            }
            connected = true
            status = "\(info.server) \(info.serverVersion) · protocol \(info.protocolVersion)"
            banner = nil
            self.log("connected: \(self.status) socket=\(self.sidecar.socketPath) computer_use=\(self.computerUse)")
            consume(await client.events())
            await refreshModels()
            await refreshSessions()
        } catch {
            status = "not connected"
            banner = Self.describe(error)
            self.log("start failed: \(Self.describe(error))")
        }
    }

    public func stop() {
        streamTask?.cancel()
        streamTask = nil
        sidecar.stop()
        client = nil
        connected = false
        status = "not connected"
    }

    private func consume(_ stream: AsyncStream<AgentEvent>) {
        streamTask = Task { @MainActor in
            for await event in stream {
                applyEvent(event)
            }
            flushDeltas()
            busy = false
        }
    }

    /// Deltas are the flood.
    ///
    /// The model streams ~1.5 characters per chunk, so a 2k-character answer is
    /// ~1300 events. Folding one costs 6.5 microseconds — the cost is that each
    /// `@Published` write invalidates every view observing this model, which
    /// re-runs the toolbar, the model picker, the session menu and every visible
    /// line. Buffering deltas and publishing on a 60 ms cadence takes that from
    /// ~1300 invalidations a turn to ~17, which is the difference between a
    /// stream that looks like typing and one that stutters.
    ///
    /// Both delta kinds go in the buffer. The thinking stream is the longer flood
    /// — see `isDelta` — and a buffer that covers only the answer is a buffer
    /// that covers the short half of the run.
    ///
    /// Order is preserved: any non-delta event flushes the buffer first, so a
    /// `message_completed` (authoritative text), a permission request, a tool
    /// row, and the terminal event are all applied after the deltas they follow.
    private var pendingDeltas: [AgentEvent] = []
    /// How many times the transcript was published. Not `@Published`: a counter
    /// that invalidates views to report invalidations is useless. The checks use
    /// it to prove a run of 200 deltas does not cost 200 view updates.
    public private(set) var transcriptPublishes = 0
    private var flushTask: Task<Void, Never>?
    private static let flushWindow = Duration.milliseconds(60)

    /// Feed a stream of events as if it came from the service. The checks use
    /// this to drive the app's state machine without a service, a socket, or a
    /// model — the streaming-cost properties below are properties of this loop,
    /// not of the transport.
    public func ingest(_ stream: AsyncStream<AgentEvent>) {
        consume(stream)
    }

    /// The two delta kinds, and why they share one buffer.
    ///
    /// `message_delta` is the answer: ~1.5 characters per chunk. `reasoning_delta`
    /// is the model's private thinking, which arrives *before* the answer and is
    /// far longer than it — measured on `lan/swift-1.5-iq3_s`, ~1,900 reasoning
    /// tokens before the first answer character, and one run streamed 686,439
    /// characters of thinking and no answer at all. Buffering only the answer
    /// leaves the thinking at one published write per delta: thousands of view
    /// invalidations a turn, on the path that is longest. That is why a run felt
    /// fast while answering and slow while thinking.
    private static func isDelta(_ event: AgentEvent) -> Bool {
        switch event {
        case .messageDelta, .reasoningDelta: return true
        default: return false
        }
    }

    private func applyEvent(_ event: AgentEvent) {
        if case .contextUsage(let usage) = event {
            // Twice a turn and not conversation: no flush, no transcript publish.
            contextUsed = usage.usedTokens
            compactAt = usage.compactAt
            runContextWindow = usage.contextWindow
            return
        }
        if case .browserFrame(let frame) = event {
            // Not the conversation, and not a reason to flush the deltas or
            // republish the transcript four times a second.
            browserPanel.show(frame)
            return
        }
        if Self.isDelta(event) {
            pendingDeltas.append(event)
            if flushTask == nil {
                flushTask = Task { @MainActor [weak self] in
                    try? await Task.sleep(for: Self.flushWindow)
                    self?.flushDeltas()
                }
            }
            return
        }
        flushDeltas()
        transcript.apply(event)
        transcriptPublishes += 1
        let hadPrompt = promptToAnswer != nil
        promptToAnswer = transcript.pendingPermission
        autoAnswerIfNeeded()
        // The computer-use tools hide this app before they look or act, so its
        // window neither covers the screen nor takes the agent's typing. A
        // prompt hidden with it would wait forever, and a finished run's answer
        // belongs on screen.
        if (!hadPrompt && promptToAnswer != nil && permissionMode != .allowAll) || event.isTerminal {
            Self.revealIfHidden()
        }
        if event.isTerminal {
            busy = false
            // The sidebar lists what is on disk, and this run has just put a
            // session there. The refresh can arrive a moment before the service
            // writes it — the terminal event is sent, then the file is saved —
            // and that is self-healing: the row appears on the next refresh,
            // which the sidebar does whenever it appears.
            Task { await refreshSessions() }
        }
    }

    /// Apply everything buffered so far. The checks use it; the app uses it
    /// before reading the transcript (copying text, saving a session).
    public func quiesce() {
        flushDeltas()
    }

    private func flushDeltas() {
        flushTask?.cancel()
        flushTask = nil
        guard !pendingDeltas.isEmpty else { return }
        // One write, one invalidation. Assigning per delta inside the loop would
        // publish per delta again, which is the thing being avoided.
        var folded = transcript
        for event in pendingDeltas {
            folded.apply(event)
        }
        transcript = folded
        transcriptPublishes += 1
        pendingDeltas.removeAll(keepingCapacity: true)
    }

    // MARK: runs

    /// Why the composer cannot send.
    public enum SendBlock: Equatable, Sendable {
        case notConnected
        case busy
        case noWorkingDir
        case emptyDraft

        /// The user's words, shown next to the button. A greyed-out Send beside a
        /// typed prompt teaches the user that the app is broken; the same words
        /// teach them what to click.
        public var reason: String {
            switch self {
            case .notConnected: return "not connected to the service"
            case .busy: return "a run is already going"
            case .noWorkingDir: return "choose a working directory — it is the scope the agent works in"
            case .emptyDraft: return "nothing to send"
            }
        }
    }

    /// The reason Send is disabled, or nil when it is not.
    ///
    /// This is `send`'s own rule, in one place, so the button and the call cannot
    /// disagree about what is sendable. The composer used to disable Send with no
    /// explanation: a greyed button next to a finished prompt, and the way to
    /// learn why was to go looking for the empty working directory in the status
    /// bar.
    ///
    /// Order is the order of what the user can act on: a dead service, a run in
    /// flight, the scope nothing can run in, then an empty field.
    public func sendBlock(_ draft: String, hasImages: Bool = false) -> SendBlock? {
        if !connected { return .notConnected }
        if busy { return .busy }
        if workingDir.isEmpty { return .noWorkingDir }
        if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !hasImages {
            return .emptyDraft
        }
        return nil
    }

    /// Send a prompt. Returns the reason it was refused, or nil when it went.
    ///
    /// The answer is returned, not just shown, because the composer has to keep
    /// the words: a refused send used to clear the field on its way out, so the
    /// card named a prompt that was already gone and the timeline stayed at "No
    /// run yet" — no record of what was refused, and nothing to fix.
    @discardableResult
    public func send(_ prompt: String, images: [String] = []) -> SendBlock? {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if let block = sendBlock(trimmed, hasImages: !images.isEmpty) {
            // The composer disables Send for this, so arriving here means the
            // keyboard shortcut went past a disabled button. Say why, in the same
            // words the button uses.
            banner = block.reason
            return block
        }
        // `sendBlock` has already ruled out "not connected" and "a run is going";
        // this is the connection object itself, which only exists once there is a
        // connection to hold.
        guard let client else {
            banner = SendBlock.notConnected.reason
            return .notConnected
        }

        // The wire requires a non-empty prompt, so an image with no words gets
        // the question the image itself asks: what is in it. The timeline shows
        // that prompt too, because it is what the run was told.
        let text = trimmed.isEmpty ? "What is in this image?" : trimmed
        transcript.addUserMessage(text, images: images)
        transcript.beginRun(runId: "…")
        busy = true

        Task {
            do {
                let started = try await client.startRun(
                    prompt: text,
                    workingDir: workingDir,
                    model: selectedModel.isEmpty ? nil : selectedModel,
                    sessionId: sessionId,
                    permissionMode: permissionMode,
                    images: images,
                    reasoningEffort: reasoningEffort
                )
                sessionId = started.sessionId
                transcript.beginRun(runId: started.runId)
                banner = nil
                self.log("run started: \(started.runId) model=\(started.model) session=\(started.sessionId)")
            } catch {
                // The run never started, so no event will say so. Say it here.
                transcript.failLocally(Self.describe(error))
                busy = false
                banner = Self.describe(error)
                self.log("run.start refused: \(Self.describe(error))")
            }
        }
        return nil
    }

    /// Stop the agent and hand the user its browser.
    ///
    /// The run is cancelled first: two hands on one page is a page neither
    /// controls. With a window, it comes forward; headless, the page opens in
    /// the user's own browser — signed-out, but the place the agent got to.
    public func takeOver() {
        cancel()
        guard let client else { return }
        Task {
            do {
                let shown = try await client.showBrowser()
                if shown.open, !shown.headless, BrowserPanelModel.activateAgentBrowser(pid: shown.pid) { return }
                let target = shown.url.isEmpty ? browserPanel.url : shown.url
                if let url = URL(string: target), ["http", "https"].contains(url.scheme ?? "") {
                    NSWorkspace.shared.open(url)
                } else {
                    banner = "the agent has no page open"
                }
            } catch {
                banner = "could not reach the agent's browser: \(Self.describe(error))"
            }
        }
    }

    public func cancel() {
        guard let client, let runId = transcript.runId, transcript.state.isRunning else { return }
        Task {
            do {
                try await client.cancelRun(runId: runId)
            } catch {
                banner = Self.describe(error)
            }
        }
    }

    /// Unhide the app if computer use hid it. `NSApp` is nil in the checks,
    /// which run without an application.
    static func revealIfHidden() {
        guard let app = NSApp, app.isHidden else { return }
        app.unhide(nil)
        app.activate(ignoringOtherApps: true)
    }

    /// Answer a permission prompt. The scope decides how long the answer
    /// lasts: `.once` this call, `.session` for the rest of the session,
    /// `.never` to stop being asked by refusing that tool.
    public func answer(granted: Bool, scope: PermissionScope) {
        guard let client, let request = promptToAnswer else { return }
        Task {
            do {
                try await client.respondPermission(requestId: request.id, granted: granted, scope: scope)
            } catch {
                banner = "the answer did not reach the service: \(Self.describe(error))"
            }
        }
    }

    /// In auto mode, answer a pending prompt without showing the sheet.
    ///
    /// The server's gate is fixed when `run.start` arrives, so switching the
    /// mode while a run is in flight cannot change that gate. The client can
    /// still make the mode true for the run it owns: a `permission_requested`
    /// is granted immediately, and `.session` means the same tool is not asked
    /// again in this run.
    private func autoAnswerIfNeeded() {
        guard permissionMode == .allowAll, let request = promptToAnswer else { return }
        // The transcript's `pendingPermission` survives until the server's
        // `permission_resolved`; a later event would otherwise put the sheet
        // back on screen. Keep it dismissed.
        if autoAnsweredRequests.contains(request.id) {
            promptToAnswer = nil
            return
        }
        guard let client else { return }

        autoAnsweredRequests.insert(request.id)
        // Dismiss the sheet at once: the prompt is answered, not waiting for a
        // person. The timeline still records the grant when `permission_resolved`
        // arrives from the service.
        promptToAnswer = nil
        Task {
            do {
                try await client.respondPermission(requestId: request.id, granted: true, scope: .session)
                notice = "auto-approved \(request.toolName)"
            } catch {
                banner = "auto-approval did not reach the service: \(Self.describe(error))"
            }
        }
    }

    /// Start over. The event stream is *not* re-subscribed: an `AsyncStream`
    /// has one consumer for its life, and the connection's stream is already
    /// being consumed. Only the timeline resets.
    public func newRun() {
        sessionId = nil
        transcript = Transcript(terminalTypes: terminalTypes)
        resetContextMeter()
        promptToAnswer = nil
        autoAnsweredRequests.removeAll()
        busy = false
        banner = nil
        paletteOpen = false
    }

    /// The selected model's window. The selected model is what the next run
    /// uses, so it wins over the last run's window when the two differ; the
    /// run's is the fallback for the router's choice.
    public var contextWindow: Int? {
        let selected = modelRows.first { $0.key == selectedModel }?.caps.contextWindow
        return selected.flatMap { $0 > 0 ? $0 : nil } ?? runContextWindow
    }

    private func resetContextMeter() {
        contextUsed = nil
        compactAt = nil
        runContextWindow = nil
    }

    // MARK: models, sessions

    public func refreshModels() async {
        guard let client else { return }
        do {
            let list = try await client.models(discover: true)
            modelRows = list.models
            clampEffort()
            modelPolicy = list.policy
            self.log("models: \(list.models.count) rows, policy=\(list.policy), selected=\(list.selected ?? "none")")
            // The table is the only thing that can say a model is gone, so it is
            // where a restored key is checked — see `restorableModel`.
            let kept = Self.restorableModel(selectedModel, in: list.models)
            if kept != selectedModel {
                let dropped = selectedModel
                self.log("model: \(dropped) is not in the table, using \(kept.isEmpty ? "the router" : kept)")
                // The toolbar changes under the user; a line that says so beats
                // a picker that quietly disagrees with the one they remember.
                notice =
                    "the remembered model \(dropped) is not in the table — \(kept.isEmpty ? "the router chooses" : kept)"
                selectedModel = kept
            }
            if selectedModel.isEmpty { selectedModel = list.selected ?? list.defaultModel ?? "" }
        } catch {
            banner = Self.describe(error)
            self.log("model.list failed: \(Self.describe(error))")
        }
    }

    public func chooseModel(_ key: String) {
        selectedModel = key
        guard let client else { return }
        Task {
            do {
                _ = try await client.selectModel(key)
            } catch {
                banner = Self.describe(error)
            }
        }
    }

    /// Add a model server and immediately read the model table back.
    ///
    /// The service persists the endpoint and discovers it in one call; the app
    /// then calls `model.list` again because the table is the UI's source of
    /// truth. A reachable server with no models is not an add failure: the
    /// result carries a `discoveryError`, and the model list stays unchanged.
    public func addEndpoint(
        name: String,
        baseURL: String,
        wireApi: String,
        label: String,
        local: Bool,
        requiresKey: Bool
    ) async -> (result: EndpointAddResult?, error: String?) {
        guard let client else {
            return (nil, SendBlock.notConnected.reason)
        }
        do {
            let result = try await client.addEndpoint(
                name: name,
                baseURL: baseURL,
                wireApi: wireApi,
                label: label,
                local: local,
                requiresKey: requiresKey
            )
            await refreshModels()
            return (result, nil)
        } catch {
            return (nil, Self.describe(error))
        }
    }

    public func refreshSessions() async {
        guard let client else { return }
        do {
            sessions = try await client.sessions()
        } catch {
            banner = Self.describe(error)
        }
    }

    /// Resume a stored conversation. The event stream is not resumable; the
    /// session is, and the next run continues it.
    public func open(_ sessionId: String) {
        guard let client else { return }
        Task {
            do {
                let record = try await client.loadSession(sessionId)
                var history = Transcript(terminalTypes: terminalTypes)
                for message in record.messages {
                    guard let fields = message.objectValue else { continue }
                    let role = fields["role"]?.stringValue ?? "assistant"
                    let content = fields["content"] ?? .null
                    let text = Self.text(of: content)
                    let images = role == "user" ? Self.images(of: content) : []
                    if text.isEmpty, images.isEmpty { continue }
                    if role == "user" {
                        history.addUserMessage(text, images: images)
                    } else {
                        history.addAssistantMessage(text)
                    }
                }
                self.sessionId = record.sessionId
                transcript = history
                resetContextMeter()
                contextUsed = record.contextTokens
                promptToAnswer = nil
                busy = false
                self.notice = Self.resumedNotice(
                    sessionId: record.sessionId,
                    row: sessions.first { $0.sessionId == record.sessionId }
                )
            } catch {
                banner = Self.describe(error)
            }
        }
    }

    /// The line that confirms a resumed conversation.
    ///
    /// It names the session the way the sidebar names it — the sentence of its
    /// title — because the user clicked a row they read as a name, and a line
    /// quoting a 28-character string they never saw confirms a different thing
    /// from the one they did. `SessionTitle.label` already falls back to the id
    /// for a session with no title, so the id is the last answer, not the first.
    /// `nonisolated`: it reads its two arguments and nothing else, so the check
    /// suite can call it from outside the main actor.
    nonisolated public static func resumedNotice(sessionId: String, row: SessionRow?) -> String {
        if let row { return "resumed \(SessionTitle.label(row)) — the next run continues it" }
        return "resumed \(sessionId) — the next run continues it"
    }

    /// Whether the app may offer to delete a session.
    ///
    /// The server refuses a session with a run on it (`session_busy`), and it is
    /// right to: the run saves its session when it ends, so a file the user
    /// watched disappear would come back a second later. The app gets there first
    /// for its own run — a row you are watching should not offer to remove the
    /// thing writing it — and the server's refusal stays the backstop for a run
    /// started on the CLI, which the app cannot see.
    public func canDelete(_ sessionId: String) -> Bool {
        SessionDeletion.isOffered(sessionId, openSessionId: sessionId, running: busy)
    }

    /// Remove a session, then read the list back from the service.
    ///
    /// The refresh is not a tidy-up of local state: the server is what knows
    /// whether the file is gone. A sidebar that dropped the row itself would be
    /// reporting a deletion the server refused.
    public func delete(_ sessionId: String) async {
        guard let client else { return }
        let name = sessions.first { $0.sessionId == sessionId }.map(SessionTitle.label) ?? sessionId
        do {
            let removed = try await client.deleteSession(sessionId)
            if self.sessionId == sessionId {
                // The open conversation is gone, so the timeline goes with it.
                // Leaving it on screen is a transcript the user cannot resume.
                newRun()
            }
            sessions.removeAll { $0.sessionId == sessionId }
            notice = removed ? "deleted \(name)" : "\(name) was already gone"
        } catch {
            banner = Self.describe(error)
        }
        await refreshSessions()
    }

    // MARK: helpers

    /// A tool result's content is a string, a block list, or a dict. A line of
    /// history needs one string.
    /// The base64 of a stored message's images. The session file keeps each as
    /// `{"type":"image_url","image_url":{"url":"data:image/png;base64,…"}}`.
    ///
    /// The key arrives as `imageUrl`: `session.load` is decoded with
    /// `.convertFromSnakeCase`, and that strategy renames the keys inside a
    /// `JSONValue` too. Both spellings are read, so a decoder change cannot make
    /// a resumed session's images vanish again.
    nonisolated public static func images(of content: JSONValue) -> [String] {
        (content.arrayValue ?? []).compactMap { block in
            let fields = block.objectValue
            guard let url = (fields?["imageUrl"] ?? fields?["image_url"])?.objectValue?["url"]?.stringValue,
                url.hasPrefix("data:"), let comma = url.firstIndex(of: ",")
            else { return nil }
            return String(url[url.index(after: comma)...])
        }
    }

    private static func text(of content: JSONValue) -> String {
        if let text = content.stringValue { return text }
        if let blocks = content.arrayValue {
            return blocks.compactMap { block in
                block.stringValue
                    ?? block.objectValue?["text"]?.stringValue
                    ?? block.objectValue?["content"]?.stringValue
            }.joined(separator: "\n")
        }
        if let object = content.objectValue {
            return object["text"]?.stringValue ?? object["content"]?.stringValue ?? ""
        }
        return ""
    }

    /// What the user reads in the banner.
    ///
    /// A `ServiceError` says what the service said, which is the useful part.
    /// Everything else goes through `LocalizedError`, because every error this
    /// client raises has a sentence already and `"\(error)"` is the enum's mirror
    /// name: killing the service and sending once drew "notConnected" in the
    /// banner, which is a name for a state, not a sentence about what happened.
    /// An error that does not conform is left as `"\(error)"` — a plain `NSError`'s
    /// `localizedDescription` is "The operation couldn't be completed. (…)", which
    /// is worse than the mirror name, not better.
    public static func describe(_ error: any Error) -> String {
        if let service = error as? ServiceError {
            return "\(service.code) (\(service.code.rawCode)): \(service.message)"
        }
        if let localized = error as? LocalizedError,
            let prose = localized.errorDescription, !prose.isEmpty
        {
            return prose
        }
        return "\(error)"
    }
}

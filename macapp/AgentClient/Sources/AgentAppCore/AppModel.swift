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
    /// Mirrored from the transcript so `.sheet(item:)` has an `Identifiable`
    /// value to present.
    @Published public var promptToAnswer: PermissionRequest?
    @Published public var modelRows: [ModelRow] = []
    @Published public var modelPolicy = ""
    @Published public var sessions: [SessionRow] = []
    @Published public var selectedModel = ""
    @Published public var workingDir = ""
    @Published public var permissionMode: PermissionMode = .ask
    /// How much the model may think before answering. `nil` means "model
    /// decides", which sends nothing — and on a reasoning model that default is
    /// the slow path: measured at ~10s to the first answer character, and in one
    /// run 686,439 characters of private reasoning with no answer at all.
    @Published public var reasoningEffort: ReasoningEffort? = nil
    @Published public var status = "not connected"
    @Published public var busy = false
    @Published public var connected = false
    /// An error to show. Kept separate from `notice`: an informational line
    /// that pops a modal titled "the service said no" teaches the user that
    /// alerts here are noise.
    @Published public var banner: String?
    /// An informational line for the status bar.
    @Published public var notice: String?

    public private(set) var sessionId: String?
    private let sidecar = Sidecar()
    private var client: AgentServiceClient?
    private var streamTask: Task<Void, Never>?
    private var terminalTypes: Set<String> = Wire.terminalEventTypes

    public init() {}

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

        Task {
            do {
                try await sidecar.start()
                let client = AgentServiceClient(
                    endpoint: ServiceEndpoint(socketPath: sidecar.socketPath, clientName: "solocoder-app")
                )
                self.client = client
                let info = try await client.connect()
                terminalTypes = await client.terminalEventTypes
                transcript = Transcript(terminalTypes: terminalTypes)
                connected = true
                status = "\(info.server) \(info.serverVersion) · protocol \(info.protocolVersion)"
                banner = nil
                self.log("connected: \(self.status) socket=\(self.sidecar.socketPath)")
                consume(await client.events())
                await refreshModels()
                await refreshSessions()
            } catch {
                status = "not connected"
                banner = Self.describe(error)
                self.log("start failed: \(Self.describe(error))")
            }
        }
    }

    public func stop() {
        streamTask?.cancel()
        streamTask = nil
        sidecar.stop()
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
        promptToAnswer = transcript.pendingPermission
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
    public func sendBlock(_ draft: String) -> SendBlock? {
        if !connected { return .notConnected }
        if busy { return .busy }
        if workingDir.isEmpty { return .noWorkingDir }
        if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return .emptyDraft }
        return nil
    }

    public func send(_ prompt: String) {
        guard let client, !busy else { return }
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if let block = sendBlock(trimmed) {
            // The composer disables Send for this, so arriving here means the
            // keyboard shortcut went past a disabled button. Say why, in the same
            // words the button uses.
            banner = block.reason
            return
        }

        transcript.addUserMessage(trimmed)
        transcript.beginRun(runId: "…")
        busy = true

        Task {
            do {
                let started = try await client.startRun(
                    prompt: trimmed,
                    workingDir: workingDir,
                    model: selectedModel.isEmpty ? nil : selectedModel,
                    sessionId: sessionId,
                    permissionMode: permissionMode,
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

    /// Start over. The event stream is *not* re-subscribed: an `AsyncStream`
    /// has one consumer for its life, and the connection's stream is already
    /// being consumed. Only the timeline resets.
    public func newRun() {
        sessionId = nil
        transcript = Transcript(terminalTypes: terminalTypes)
        promptToAnswer = nil
        busy = false
        banner = nil
    }

    // MARK: models, sessions

    public func refreshModels() async {
        guard let client else { return }
        do {
            let list = try await client.models(discover: true)
            modelRows = list.models
            modelPolicy = list.policy
            self.log("models: \(list.models.count) rows, policy=\(list.policy), selected=\(list.selected ?? "none")")
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
                    let text = Self.text(of: fields["content"] ?? .null)
                    if text.isEmpty { continue }
                    if role == "user" {
                        history.addUserMessage(text)
                    } else {
                        history.addAssistantMessage(text)
                    }
                }
                self.sessionId = record.sessionId
                transcript = history
                promptToAnswer = nil
                busy = false
                self.notice = "resumed \(record.sessionId) — the next run continues it"
            } catch {
                banner = Self.describe(error)
            }
        }
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

    /// A `ServiceError` says what the service said, which is the useful part;
    /// everything else gets its own description.
    static func describe(_ error: any Error) -> String {
        if let service = error as? ServiceError {
            return "\(service.code) (\(service.code.rawCode)): \(service.message)"
        }
        return "\(error)"
    }
}

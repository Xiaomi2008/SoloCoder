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
    @Published public var status = "not connected"
    @Published public var busy = false
    @Published public var connected = false
    @Published public var banner: String?

    public private(set) var sessionId: String?
    private let sidecar = Sidecar()
    private var client: AgentServiceClient?
    private var streamTask: Task<Void, Never>?
    private var terminalTypes: Set<String> = Wire.terminalEventTypes

    public init() {}

    deinit {
        streamTask?.cancel()
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
                consume(await client.events())
                await refreshModels()
                await refreshSessions()
            } catch {
                status = "not connected"
                banner = Self.describe(error)
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
                transcript.apply(event)
                promptToAnswer = transcript.pendingPermission
                if event.isTerminal { busy = false }
            }
            busy = false
        }
    }

    // MARK: runs

    public func send(_ prompt: String) {
        guard let client, !busy else { return }
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard !workingDir.isEmpty else {
            banner = "Choose a working directory first — it is the scope the agent works in."
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
                    permissionMode: permissionMode
                )
                sessionId = started.sessionId
                transcript.beginRun(runId: started.runId)
                banner = nil
            } catch {
                // The run never started, so no event will say so. Say it here.
                transcript.failLocally(Self.describe(error))
                busy = false
                banner = Self.describe(error)
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
            if selectedModel.isEmpty { selectedModel = list.selected ?? list.defaultModel ?? "" }
        } catch {
            banner = Self.describe(error)
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
                banner = "resumed \(record.sessionId) — the next run continues it"
            } catch {
                banner = Self.describe(error)
            }
        }
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

import Foundation

/// The service process.
///
/// In development the sidecar is the repository's own venv, so the app runs
/// against exactly the code the Python suite runs against — the same `serve`, the
/// same socket, the same protocol. In a shipped `.app` it is the interpreter and
/// source under `Contents/Resources/sidecar/`, built by `scripts/build_app.sh`.
///
/// The socket lives in a short temp directory on purpose: macOS caps an
/// `AF_UNIX` path at ~104 bytes, and a deep container path fails at `connect`
/// in ways that look like a dead server.
@MainActor
public final class Sidecar {
    public enum Failure: LocalizedError {
        case noService
        case exited(Int32, String)
        case notReady(String, String)

        public var errorDescription: String? {
            switch self {
            case .noService:
                "no service to spawn — not a bundled sidecar, and no repository with "
                    + ".venv/bin/python (set AGENT_REPO_ROOT to the SoloCoder checkout)"
            case .exited(let code, let log):
                "the service exited with \(code)\n\(log)"
            case .notReady(let path, let log):
                "the service did not bind \(path)\n\(log)"
            }
        }
    }

    /// Where the service comes from.
    public enum Launch: Equatable, Sendable {
        /// A sidecar inside a `.app`: `Contents/Resources/sidecar/{python,app}`.
        case bundled(python: URL, app: URL)
        /// The repository's own venv, in development.
        case repository(root: URL)

        public var executable: URL {
            switch self {
            case .bundled(let python, _): python
            case .repository(let root): root.appendingPathComponent(".venv/bin/python")
            }
        }
        public var workingDirectory: URL {
            switch self {
            case .bundled(_, let app): app
            case .repository(let root): root
            }
        }
        public var script: URL { workingDirectory.appendingPathComponent("cli_coder.py") }
    }

    public let socketPath: String
    /// Replaced on every `start`: a `Process` launches once, and `run()` on one
    /// that already ran raises "task already launched" — which, thrown inside
    /// the restart task a settings toggle starts, left the concurrency runtime
    /// corrupt and crashed the next view update.
    private var process = Process()
    private let logPath: String
    /// Held for the process's life: closing it early would tell the service its
    /// parent is gone while the app is running fine.
    private var parentPipe: Pipe?
    public private(set) var running = false

    public init() {
        // `/tmp`, not `NSTemporaryDirectory()`, and short on purpose. macOS caps
        // an `AF_UNIX` path at ~104 bytes; under Gatekeeper translocation a
        // quarantined app's container temp dir is deep enough to fail at
        // `connect` in a way that reads as a dead server. `/tmp/oa-<uid>/<pid>/`
        // stays near 25 bytes, and the pid keeps two apps from sharing a socket.
        let directory = "/tmp/oa-\(getuid())/\(getpid())"
        socketPath = "\(directory)/a.sock"
        logPath = "\(directory)/a.log"
    }

    /// The service to spawn: a bundled sidecar, else `AGENT_REPO_ROOT`, else the
    /// current directory if it looks like the repository.
    public static func resolveLaunch() -> Launch? {
        // `bundleURL` and not `Bundle.main.resourceURL`: the latter is documented
        // to fall back to the current directory when it cannot resolve a
        // Resources directory, which is the development case — so a shipped app
        // that failed to find its sidecar would silently run the repository's.
        // `bundleURL` is the bundle, and under translocation it is the
        // read-only mount the app is actually running from.
        let manager = FileManager.default
        let sidecar = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/sidecar")
        let python = sidecar.appendingPathComponent("python/bin/python3")
        let app = sidecar.appendingPathComponent("app")
        if manager.isExecutableFile(atPath: python.path),
           manager.fileExists(atPath: app.appendingPathComponent("cli_coder.py").path) {
            return .bundled(python: python, app: app)
        }
        if let root = repositoryRoot() { return .repository(root: root) }
        return nil
    }

    /// The checkout to run the service from: `AGENT_REPO_ROOT`, else the current
    /// directory if it looks like the repository.
    ///
    /// The answer is always absolute and standardized. The root is used twice —
    /// as the child's `currentDirectoryURL` and as the base of the executable
    /// path — so a relative value is a hop applied to two things at once, and the
    /// spawn lands beside the cwd: `AGENT_REPO_ROOT=../..` from `macapp/AgentClient`
    /// looked for `.venv/bin/python` in `macapp/AgentClient` and the app-model
    /// check reported "not connected". `HANDOFF` documents that exact command.
    public static func repositoryRoot() -> URL? {
        let manager = FileManager.default
        if let configured = ProcessInfo.processInfo.environment["AGENT_REPO_ROOT"],
           !configured.isEmpty {
            // Resolved against *our* cwd, then `..` and `.` collapsed, so what
            // the launcher gets is a path with no hops left in it.
            let root = URL(fileURLWithPath: configured).standardizedFileURL
            if manager.fileExists(atPath: root.path) { return root }
        }
        let cwd = URL(fileURLWithPath: manager.currentDirectoryPath)
        if manager.fileExists(atPath: cwd.appendingPathComponent(".venv/bin/python").path) {
            return cwd
        }
        return nil
    }

    public var logTail: String {
        (try? String(contentsOfFile: logPath, encoding: .utf8)).map {
            $0.split(whereSeparator: \.isNewline).suffix(12).joined(separator: "\n")
        } ?? ""
    }

    public func start() async throws {
        if running { return }
        guard let launch = Self.resolveLaunch() else { throw Failure.noService }
        process = Process()
        let directory = URL(fileURLWithPath: socketPath).deletingLastPathComponent()
        sweepStaleDirectories(under: "/tmp/oa-\(getuid())")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        process.executableURL = launch.executable
        var arguments: [String] = []
        if case .bundled = launch {
            // `-I` ignores `PYTHON*` and the user site dir, so a `PYTHONPATH`
            // left in the developer's shell cannot point the bundled
            // interpreter at a tree outside the bundle — and `import openagent`
            // resolves from `sys.path[0]`, the script's own directory, which is
            // where the bundle puts the source. `-B` keeps it from writing
            // `__pycache__` into a bundle that is signed and, translocated,
            // read-only. `-u` unbuffers the log.
            arguments += ["-I", "-B", "-u"]
        }
        arguments += [launch.script.path, "serve", "--socket", socketPath, "--quiet"]
        process.arguments = arguments
        // The bundle is read-only under translocation, so the service's cwd is
        // the home directory — where its sessions and config already live. In
        // development the cwd is the checkout, as it has always been.
        process.currentDirectoryURL = switch launch {
        case .bundled: FileManager.default.homeDirectoryForCurrentUser
        case .repository(let root): root
        }
        // Off the main actor: this asks a login shell for its PATH, and a shell
        // that blocks on a prompt must not block the window.
        process.environment = await Task.detached(priority: .userInitiated) {
            serviceEnvironment(for: launch)
        }.value
        // A file, not a Pipe: a full pipe buffer blocks the writer, and a
        // service that blocks on its own stderr is a service that stops serving.
        FileManager.default.createFile(atPath: logPath, contents: nil)
        let log = try FileHandle(forWritingTo: URL(fileURLWithPath: logPath))
        process.standardOutput = log
        process.standardError = log
        // The service watches its stdin for parent death. This pipe's write end
        // closes when the app dies — by quit, by `kill`, by SIGKILL — the read
        // EOFs, and the service shuts itself down, draining its MCP subprocesses
        // on the way out.
        //
        // Without it, a killed app leaves its service bound to a socket named
        // after the dead app's pid: invisible in the next launch's logs, and
        // every relaunch adds another. Four of them had accumulated on this
        // machine before this line existed.
        let stdinPipe = Pipe()
        // `FD_CLOEXEC` on the write end, and nothing else, keeps the watchdog
        // honest. The service spawns its own children — MCP servers, the bash
        // tool — and a forked child inherits the write end. If it does, the read
        // end never sees EOF when the app dies, because a grandchild still holds
        // it open: the service that was supposed to shut itself down is serving
        // forever, in a socket directory named after a dead app.
        fcntl(stdinPipe.fileHandleForWriting.fileDescriptor, F_SETFD, FD_CLOEXEC)
        self.parentPipe = stdinPipe
        process.standardInput = stdinPipe

        try process.run()
        running = true

        // The socket file appearing is the server being ready to accept; the
        // banner is suppressed with --quiet, so there is nothing to read.
        let deadline = Date().addingTimeInterval(25)
        while !FileManager.default.fileExists(atPath: socketPath) {
            if !process.isRunning {
                throw Failure.exited(process.terminationStatus, logTail)
            }
            if Date() > deadline {
                throw Failure.notReady(socketPath, logTail)
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
    }

    public func stop() {
        guard process.isRunning else { running = false; return }
        process.terminate()
        // A SIGTERM'd service cancels its run, closes the provider and drains its
        // MCP subprocesses, so waiting here is the app's own teardown. Bounded,
        // because a service blocked in a provider call that ignores its
        // cancellation, or in a subprocess that will not die, would otherwise
        // hang app teardown forever — and this runs on the main actor, from
        // `applicationWillTerminate`. 2 s, then SIGKILL.
        let deadline = Date().addingTimeInterval(2)
        while process.isRunning, Date() < deadline {
            usleep(20_000)
        }
        if process.isRunning {
            kill(process.processIdentifier, SIGKILL)
        }
        process.waitUntilExit()
        // Release the write end last: the service's own shutdown path may
        // already be running from the EOF.
        parentPipe?.fileHandleForWriting.closeFile()
        parentPipe = nil
        running = false
    }
}

// MARK: - The service's process environment
//
// File scope, and not main-actor isolated, because the work is a subprocess:
// `start()` awaits it off the main actor.

/// A Finder-launched app gets `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, cwd `/`, and
/// no shell rc files — so the bash tool cannot find a Homebrew git, rg, node or
/// uv, and an MCP server configured as `npx`/`uvx` fails at `exec`. A terminal
/// launch hides all of that, because it inherits the shell. So the PATH is asked
/// of a login shell, and the environment is curated rather than inherited: a
/// stray `PYTHONPATH`, a `PYTHONHOME`, a `VIRTUAL_ENV`, a `DYLD_LIBRARY_PATH`
/// from the developer's shell all point the bundled interpreter at trees that are
/// not in the bundle.
func serviceEnvironment(for launch: Sidecar.Launch) -> [String: String] {
    var environment: [String: String] = [:]
    let inherited = ProcessInfo.processInfo.environment
    for key in ["HOME", "USER", "LOGNAME", "SHELL", "XDG_CONFIG_HOME", "LANG", "TMPDIR"] {
        if let value = inherited[key] { environment[key] = value }
    }
    if environment["HOME"] == nil {
        environment["HOME"] = FileManager.default.homeDirectoryForCurrentUser.path
    }
    if environment["TMPDIR"] == nil { environment["TMPDIR"] = NSTemporaryDirectory() }
    if environment["LANG"] == nil { environment["LANG"] = "en_US.UTF-8" }
    environment["PATH"] = loginShellPath()
    // The computer-use tools hide this app before they act, so the agent's
    // typing lands in the app the person was using and not in our composer.
    environment["OPENAGENT_HOST_PID"] = String(ProcessInfo.processInfo.processIdentifier)
    if case .repository(let root) = launch {
        // Development: the interpreter is the checkout's venv, and a venv needs
        // its `VIRTUAL_ENV` to find its own site-packages when the interpreter
        // is invoked by absolute path.
        environment["VIRTUAL_ENV"] = root.appendingPathComponent(".venv").path
    }
    return environment
}

/// The `PATH` of a login shell.
///
/// `-lc` and not `-lic`: an interactive zsh prints its banner, and the sentinel
/// keeps an rc file's own `echo` from being read as part of the value.
///
/// Called once per `start()`, not cached: the app starts its service once, and a
/// cache is a stale PATH after the user installs a tool.
func loginShellPath() -> String {
    let fallback = "/usr/bin:/bin:/usr/sbin:/sbin"
    let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
    let child = Process()
    child.executableURL = URL(fileURLWithPath: shell)
    child.arguments = ["-lc", "printf 'OA_PATH=%s' \"$PATH\""]
    let pipe = Pipe()
    child.standardOutput = pipe
    child.standardError = FileHandle.nullDevice
    var captured: String?
    let finished = DispatchSemaphore(value: 0)
    DispatchQueue.global(qos: .userInitiated).async {
        let data = try? pipe.fileHandleForReading.readToEnd()
        captured = data.flatMap { String(data: $0, encoding: .utf8) }
            .flatMap { $0.components(separatedBy: "OA_PATH=").last }
        finished.signal()
    }
    do {
        try child.run()
    } catch {
        return fallback
    }
    // A shell that blocks on a `read`, on a prompt, on a slow network mount: the
    // app must not wait for it. 2 s, then the LaunchServices default — the app
    // launches, and the bash tool reports a missing `rg` rather than the window
    // never appearing.
    if finished.wait(timeout: .now() + 2) == .timedOut {
        child.terminate()
        return fallback
    }
    guard let path = captured, !path.isEmpty else { return fallback }
    return path
}

/// Directories left by an app that died: `/tmp/oa-<uid>/<pid>/`.
///
/// The pid is in the name, so every launch adds one, and a killed app's service
/// is gone by the stdin watchdog but the directory is not. Sweep the ones whose
/// pid is no longer alive.
func sweepStaleDirectories(under directory: String) {
    let manager = FileManager.default
    guard let entries = try? manager.contentsOfDirectory(atPath: directory) else { return }
    for entry in entries {
        guard let pid = Int32(entry) else { continue }
        // `kill(pid, 0)` asks whether the process exists and is ours to signal:
        // ESRCH means it is gone, and the directory is litter. EPERM would mean
        // it exists and is not ours, so `== -1` alone is not enough.
        if kill(pid, 0) == -1, errno == ESRCH {
            try? manager.removeItem(atPath: "\(directory)/\(entry)")
        }
    }
}

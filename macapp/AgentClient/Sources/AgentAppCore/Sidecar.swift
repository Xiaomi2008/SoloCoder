import Foundation

/// The service process, in development.
///
/// A shipped app embeds a frozen build of the service. In development the
/// sidecar is the repository's own venv, so the app runs against exactly the
/// code the Python suite runs against — the same `serve`, the same socket, the
/// same protocol.
///
/// The socket lives in a short temp directory on purpose: macOS caps an
/// `AF_UNIX` path at ~104 bytes, and a deep container path fails at `connect`
/// in ways that look like a dead server.
@MainActor
public final class Sidecar {
    public enum Failure: LocalizedError {
        case noRepository
        case exited(Int32, String)
        case notReady(String, String)

        public var errorDescription: String? {
            switch self {
            case .noRepository:
                "no repository with .venv/bin/python — set AGENT_REPO_ROOT to the SoloCoder checkout"
            case .exited(let code, let log):
                "the service exited with \(code)\n\(log)"
            case .notReady(let path, let log):
                "the service did not bind \(path)\n\(log)"
            }
        }
    }

    public let socketPath: String
    private let process = Process()
    private let logPath: String
    public private(set) var running = false

    public init() {
        // Short on purpose. `oa<pid>.sock` under a temp dir stays well under the
        // AF_UNIX limit even when TMPDIR is a long container path.
        let folder = "oa\(getpid())"
        let directory = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent(folder)
        socketPath = directory.appendingPathComponent("a.sock").path
        logPath = directory.appendingPathComponent("a.log").path
    }

    /// The checkout to run the service from: `AGENT_REPO_ROOT`, else the current
    /// directory if it looks like the repository.
    public static func repositoryRoot() -> URL? {
        let manager = FileManager.default
        if let configured = ProcessInfo.processInfo.environment["AGENT_REPO_ROOT"],
           manager.fileExists(atPath: configured) {
            return URL(fileURLWithPath: configured)
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
        guard let root = Self.repositoryRoot() else { throw Failure.noRepository }
        let directory = URL(fileURLWithPath: socketPath).deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        process.executableURL = root.appendingPathComponent(".venv/bin/python")
        process.arguments = [
            root.appendingPathComponent("cli_coder.py").path,
            "serve",
            "--socket", socketPath,
            "--quiet",
        ]
        process.currentDirectoryURL = root
        // A file, not a Pipe: a full pipe buffer blocks the writer, and a
        // service that blocks on its own stderr is a service that stops serving.
        FileManager.default.createFile(atPath: logPath, contents: nil)
        let log = try FileHandle(forWritingTo: URL(fileURLWithPath: logPath))
        process.standardOutput = log
        process.standardError = log

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
        // A SIGTERM'd service cancels its run and closes the provider; waiting
        // here is the app's own teardown, not the user's problem.
        process.waitUntilExit()
        running = false
    }
}

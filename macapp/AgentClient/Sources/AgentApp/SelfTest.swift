import AgentAppCore
import AgentClient
import Foundation

/// The bundle's own gate: `SoloCoder --self-test`.
///
/// A `.app` is a directory of things that can be broken in ways `swift build`
/// cannot see: the sidecar's interpreter is not executable, `-I` does not find
/// `openagent`, the socket path runs past the `AF_UNIX` cap, the signature
/// rejects a Rust extension in site-packages, the bundle was copied somewhere
/// its `otool -L` load paths no longer resolve. None of it appears in the Swift
/// suite, and the user's first sight of it is a banner in a window.
///
/// So the app runs its own startup, headless, and prints each step.
/// `scripts/package_check.sh` runs it against a relocated, read-only copy of the
/// bundle — the shape a downloaded app actually launches in — and reads the exit
/// code.
///
/// It is the app doing this, not a shell script poking the sidecar by hand,
/// because the thing under test is *this app's* launch: `resolveLaunch()`, the
/// bundle-relative socket path, the curated environment, the flags. A script
/// that spawns the interpreter itself would pass on a bundle the app cannot run.
enum SelfTest {
    static let flag = "--self-test"

    static func requested(_ arguments: [String] = CommandLine.arguments) -> Bool {
        arguments.contains(flag)
    }

    /// Runs the app's startup against its own bundle. Returns the exit code.
    @MainActor
    static func run() async -> Int32 {
        var failures: [String] = []

        func step(_ name: String, _ body: () async throws -> String) async {
            do {
                print("ok   \(name): \(try await body())")
            } catch {
                failures.append(name)
                print("FAIL \(name): \(describe(error))")
            }
        }

        // Unbuffered, and it matters: the gate's output is read through a pipe,
        // where stdio is block-buffered, so a run that hangs and gets killed
        // would print nothing at all — and the answer to "which step hung?" is
        // the only thing the run was for.
        setvbuf(stdout, nil, _IONBF, 0)
        print("self-test \(Bundle.main.bundleURL.path)")
        let sidecar = Sidecar()

        let launch = Sidecar.resolveLaunch()
        await step("resolve", {
            guard let launch else { throw Sidecar.Failure.noService }
            switch launch {
            case .bundled(let python, _):
                // The gate's whole point: a bundle must find the interpreter it
                // ships, not a checkout. A `.repository` here means the copy
                // under test has no sidecar, and the app would be running the
                // developer's tree — a green run proving nothing.
                return "bundled python=\(python.lastPathComponent) app=\(Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/sidecar/app").path)"
            case .repository(let root):
                throw Failure("not a bundled sidecar; resolved the repository at \(root.path)")
            }
        })

        await step("start", {
            try await sidecar.start()
            return "socket=\(sidecar.socketPath) (\(sidecar.socketPath.utf8.count) bytes)"
        })

        let client = AgentServiceClient(
            endpoint: ServiceEndpoint(socketPath: sidecar.socketPath, clientName: "solocoder-selftest")
        )
        await step("connect", {
            let info = try await client.connect()
            return "\(info.server) \(info.serverVersion) protocol \(info.protocolVersion)"
        })
        await step("ping", {
            guard try await client.ping() else { throw Failure("the service did not answer ping") }
            return "pong"
        })
        await step("tool.list", {
            let rows = try await client.tools()
            if rows.isEmpty { throw Failure("the service reported no tools") }
            return "\(rows.count) tools"
        })
        await step("auth.status", {
            let status = try await client.authStatus()
            return "\(status.providers.count) providers"
        })
        await step("model.list", {
            let list = try await client.models()
            return "\(list.models.count) models, default \(list.defaultModel ?? "none")"
        })

        // Teardown is part of the contract: the app's exit must leave no service
        // and no bound socket, or every launch adds an orphan.
        await step("stop", {
            sidecar.stop()
            if FileManager.default.fileExists(atPath: sidecar.socketPath) {
                throw Failure("the socket is still bound after stop(): \(sidecar.socketPath)")
            }
            return "socket unbound"
        })

        if failures.isEmpty {
            print("self-test passed")
            return 0
        }
        print("self-test failed: \(failures.joined(separator: ", "))")
        // The service's own log, which is where the reason lives: a Python
        // import error, a socket bind failure, a signature rejection all say
        // something here that the Swift error cannot.
        let log = sidecar.logTail
        if !log.isEmpty { print("--- sidecar log ---\n\(log)") }
        return 1
    }

    struct Failure: Error {
        let message: String
        init(_ message: String) { self.message = message }
    }

    private static func describe(_ error: Error) -> String {
        if let failure = error as? Failure { return failure.message }
        return error.localizedDescription
    }
}

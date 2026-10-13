// swift-tools-version:6.0
import PackageDescription

// The Mac app talks to the Python agent service over a Unix socket. This package
// is the client half of dev_docs/agent-service-protocol.md and nothing else: no
// UI, no engine. Keeping it a plain SwiftPM package means the contract is
// testable from the command line without an Xcode project.
//
// Two suites, because the toolchains differ:
//
//   swift run agent-checks   runs on a Command Line Tools install (no Xcode),
//                            and is what CI and a developer machine can run today.
//   swift test               the XCTest/Testing suites in Tests/, which need the
//                            test libraries that ship inside Xcode.app.
let package = Package(
    name: "AgentClient",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(name: "AgentClient", targets: ["AgentClient"]),
        .executable(name: "agent-checks", targets: ["AgentClientChecks"]),
    ],
    targets: [
        .target(name: "AgentClient"),
        .executableTarget(
            name: "AgentClientChecks",
            dependencies: ["AgentClient"]
        ),
        .testTarget(
            name: "AgentClientTests",
            dependencies: ["AgentClient"]
        ),
    ]
)

// swift-tools-version:6.0
import PackageDescription

// The Mac app talks to the Python agent service over a Unix socket. This package
// is the client half of dev_docs/agent-service-protocol.md and the app's non-UI
// core: no views, no engine. Keeping it a plain SwiftPM package means the
// contract and the UI state machine are testable from the command line, without
// an Xcode project and without a screen.
//
//   AgentClient     wire protocol: framing, events, the socket client
//   AgentAppCore    event stream -> the timeline/state a view renders
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
        .library(name: "AgentAppCore", targets: ["AgentAppCore"]),
        .executable(name: "solocoder", targets: ["AgentApp"]),
        .executable(name: "ui-cost", targets: ["UICost"]),
        .executable(name: "agent-checks", targets: ["AgentClientChecks"]),
    ],
    targets: [
        .target(name: "AgentClient"),
        .target(name: "AgentAppCore", dependencies: ["AgentClient"]),
        .executableTarget(name: "AgentApp", dependencies: ["AgentClient", "AgentAppCore"]),
        .executableTarget(name: "UICost", dependencies: ["AgentClient", "AgentAppCore"]),
        .executableTarget(
            name: "AgentClientChecks",
            dependencies: ["AgentClient", "AgentAppCore"]
        ),
        .testTarget(
            name: "AgentClientTests",
            dependencies: ["AgentClient"]
        ),
    ]
)

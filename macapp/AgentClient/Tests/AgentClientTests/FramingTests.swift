import Testing
import Foundation
@testable import AgentClient

@Suite("NDJSON framing")
struct FramingTests {
    private func bytes(_ text: String) -> Data { Data(text.utf8) }

    @Test("complete frames come out in order")
    func completeFrames() throws {
        var decoder = FrameDecoder()
        let frames = try decoder.append(bytes("{\"a\":1}\n{\"b\":2}\n"))
        #expect(frames.count == 2)
        #expect(String(data: frames[0], encoding: .utf8) == "{\"a\":1}")
        #expect(String(data: frames[1], encoding: .utf8) == "{\"b\":2}")
    }

    @Test("a frame split across chunks reassembles")
    func splitFrame() throws {
        var decoder = FrameDecoder()
        let payload = bytes("{\"delta\":\"Writing — 中文 ✓\\n\"}\n")
        var frames: [Data] = []
        for byte in payload { frames.append(contentsOf: try decoder.append(Data([byte]))) }

        #expect(frames.count == 1)
        let message = try Frame.decode(frames[0])
        guard case .event(.messageDelta(let delta)) = message else {
            Issue.record("expected a message_delta event")
            return
        }
        #expect(delta.delta == "Writing — 中文 ✓\n")
    }

    @Test("a chunk boundary inside a multi-byte character is safe")
    func multiByteBoundary() throws {
        // The newline is the only split point, and it is a single byte, so a
        // chunk can end in the middle of 中 (three bytes). Decoding per chunk
        // would corrupt it; splitting bytes cannot.
        let text = "中"
        let encoded = Array(text.utf8)
        #expect(encoded.count == 3)

        var decoder = FrameDecoder()
        var frames: [Data] = []
        frames.append(contentsOf: try decoder.append(Data([0x7B, 0x22, 0x64, 0x22, 0x3A, 0x22])))
        frames.append(contentsOf: try decoder.append(Data([encoded[0]])))
        frames.append(contentsOf: try decoder.append(Data(encoded[1...])))
        frames.append(contentsOf: try decoder.append(Data([0x22, 0x7D, 0x0A])))

        #expect(frames.count == 1)
        let object = try JSONSerialization.jsonObject(with: frames[0]) as? [String: Any]
        #expect(object?["d"] as? String == text)
    }

    @Test("empty lines are ignored")
    func emptyLines() throws {
        var decoder = FrameDecoder()
        #expect(try decoder.append(bytes("\n\n{\"a\":1}\n\n")).count == 1)
    }

    @Test("an oversized frame refuses the connection")
    func oversizedFrame() throws {
        var decoder = FrameDecoder(maxBytes: 16)
        #expect(throws: ClientError.self) {
            _ = try decoder.append(bytes("{\"aaaaaaaaaaaaaaaaaaaa\":1}\n"))
        }
    }

    @Test("an oversized unterminated frame refuses the connection")
    func oversizedPartial() throws {
        var decoder = FrameDecoder(maxBytes: 8)
        #expect(throws: ClientError.self) {
            _ = try decoder.append(bytes("0123456789abcdef"))
        }
    }

    @Test("a partial frame at close is reported")
    func trailingPartial() throws {
        var decoder = FrameDecoder()
        _ = try decoder.append(bytes("{\"a\":1"))
        #expect(decoder.takeTrailingPartial() == Data("{\"a\":1".utf8))
        #expect(decoder.takeTrailingPartial() == nil)
    }

    @Test("a response frame decodes to a response")
    func responseFrame() throws {
        let message = try Frame.decode(bytes("{\"jsonrpc\":\"2.0\",\"id\":\"c1\",\"result\":{\"pong\":true}}"))
        guard case .response(let id, let result, let error) = message else {
            Issue.record("expected a response")
            return
        }
        #expect(id == "c1")
        #expect(error == nil)
        #expect(result?.objectValue?["pong"]?.boolValue == true)
    }

    @Test("an error frame decodes to a service error with its data")
    func errorFrame() throws {
        let message = try Frame.decode(
            bytes("{\"jsonrpc\":\"2.0\",\"id\":\"c2\",\"error\":{\"code\":-33005,\"message\":\"nope\",\"data\":{\"roots\":[\"/x\"]}}}")
        )
        guard case .response(let id, _, let error) = message else {
            Issue.record("expected a response")
            return
        }
        #expect(id == "c2")
        let service = try #require(error)
        #expect(service.code == .workingDirDenied)
        #expect(service.message == "nope")
        #expect(service.data?.objectValue?["roots"]?.arrayValue?.first?.stringValue == "/x")
    }

    @Test("a request from the server is visible, not ignored")
    func serverRequest() throws {
        let message = try Frame.decode(bytes("{\"jsonrpc\":\"2.0\",\"id\":\"s1\",\"method\":\"ping\"}"))
        guard case .serverRequest(let method, let id) = message else {
            Issue.record("a server request must not be silently dropped")
            return
        }
        #expect(method == "ping")
        #expect(id == "s1")
    }

    @Test("request encoding omits empty params")
    func requestEncoding() throws {
        let frame = try Frame.encode(RpcRequest(id: "c1", method: "ping", params: nil).wireObject())
        let object = try JSONSerialization.jsonObject(with: frame) as? [String: Any]
        #expect(object?["method"] as? String == "ping")
        #expect(object?["params"] == nil, "JSON-RPC wants params absent, not null")
    }
}

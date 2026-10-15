import Foundation
import AppKit
import Darwin

var count = 0
var cleanup: [() -> Void] = []
func fail(_ message: String) -> Never {
    fputs("FAILED: \(message)\n", stderr)
    for action in cleanup.reversed() { action() }
    exit(1)
}
func check(_ value: @autoclosure () -> Bool, _ message: String) {
    guard value() else { fail(message) }; count += 1
}
func rejects(_ message: String, _ action: () throws -> Void) {
    do { try action(); fail(message) } catch { count += 1 }
}
let root = URL(fileURLWithPath: "/tmp/manager unit space")
let config = ManagerConfiguration(root: root, baseURL: URL(string: "http://127.0.0.1:5055")!)
func record(pid: Int32 = 4242, binary: String? = nil, arguments: [String]? = nil, cwd: String? = nil, started: String = "Oct 11 12:00:00") -> ProcessRecord {
    ProcessRecord(pid: pid, executable: binary ?? config.binary.path,
                  arguments: arguments ?? [config.binary.path, "serve-dialogue", "--listen", "127.0.0.1:5055"],
                  directory: cwd ?? root.path, started: started, logFiles: [])
}
func state(_ process: ProcessRecord = record(), trusted: Bool = true, kind: ServiceKind = .server) -> ServiceSnapshot {
    ServiceSnapshot(kind: kind, state: "稼働中", message: "", process: process, trusted: trusted)
}
check(ServiceController.matches(record(), kind: .server, configuration: config), "identify expected server")
check(!ServiceController.matches(record(), kind: .voice, configuration: config), "server is not voice")
check(!ServiceController.matches(record(binary: "/tmp/other/dogido-rust"), kind: .server, configuration: config), "reject other executable")
check(!ServiceController.matches(record(cwd: "/tmp/other"), kind: .server, configuration: config), "reject other checkout")
check(!ServiceController.matches(record(arguments: [config.binary.path, "serve-dialogue", "--listen", "127.0.0.1:6060"]), kind: .server, configuration: config), "reject other endpoint")
check(!ServiceController.matches(record(arguments: [config.binary.path, "serve-dialogue", "--listen", "0.0.0.0:5055"]), kind: .server, configuration: config), "reject other bind host")
check(ServiceController.matches(record(arguments: [config.binary.path, "serve-dialogue", "--listen=127.0.0.1:5055"]), kind: .server, configuration: config), "equals listen option")
let voiceArgs = [config.binary.path, "voice-input", "--settings", "{\"base_url\":\"http://127.0.0.1:5055\",\"unused\":\"contains spaces\"}"]
check(ServiceController.matches(record(arguments: voiceArgs), kind: .voice, configuration: config), "JSON argument boundary preserved")
check(ServiceController.matches(record(arguments: [config.binary.path, "voice-input", "--settings=" + voiceArgs[3]]), kind: .voice, configuration: config), "equals settings option")
check(!ServiceController.matches(record(arguments: voiceArgs + ["--check"]), kind: .voice, configuration: config), "preflight is not a running mic")
check(!ServiceController.matches(record(arguments: [config.binary.path, "voice-input", "--settings", "invalid"]), kind: .voice, configuration: config), "bad settings not managed")
let stopPID = try ServiceController.validateStop(expected: state(), current: state())
check(stopPID == 4242, "same identity stop")
for changed in [state(record(pid: 4243)), state(record(started: "later")), state(record(cwd: "/tmp/other")),
                state(record(binary: "/tmp/other")), state(record(arguments: ["other"])), state(trusted: false), state(kind: .voice)] {
    rejects("changed identity cannot stop") { _ = try ServiceController.validateStop(expected: state(), current: changed) }
}
rejects("PID 1 cannot stop") { _ = try ServiceController.validateStop(expected: state(record(pid: 1)), current: state(record(pid: 1))) }
rejects("empty start time cannot stop") { _ = try ServiceController.validateStop(expected: state(record(started: "")), current: state(record(started: ""))) }
var argc: Int32 = 3
var bytes = withUnsafeBytes(of: &argc) { Array($0) }
bytes += Array("/tmp/native\0\0/tmp/native\0voice-input\0a b c\0SECRET=not-an-argument\0".utf8)
check(ProcessReader.decodeArguments(bytes) == ["/tmp/native", "voice-input", "a b c"], "decode only argv, never environment")
check(ProcessReader.decodeArguments([0, 0, 0, 0]) == nil, "reject incomplete procargs")
let environment = ServiceController.launchEnvironment(inheriting: ["PATH": "/custom/bin:/usr/bin", "KEEP": "ok"], configuration: config)
check(environment["PATH"]!.hasPrefix("/custom/bin:/usr/bin:"), "preserve PATH precedence")
check(environment["PATH"]!.contains("/opt/homebrew/bin"), "Finder can find Homebrew")
check(environment["KEEP"] == "ok" && environment["DOGIDO_RUST_SETTINGS_DIR"] == config.root.path, "environment and settings root")
let controller = ServiceController(configuration: config)
check(controller.snapshot(.server, records: [], listeners: [55]).state == "要確認", "foreign listener blocks operations")
check(!controller.snapshot(.server, records: [record(), record(pid: 4243)], listeners: [4242]).canStop, "ambiguous servers cannot stop")
check(controller.snapshot(.server, records: [], listeners: []).stopped, "no process means stopped")
let launcher = record(binary: "/bin/zsh", arguments: ["/bin/zsh", config.root.appendingPathComponent("dogido-rust/start_dialogue.command").path], cwd: "/tmp/elsewhere")
check(ServiceController.isLauncher(launcher, kind: .server, configuration: config), "absolute launcher from another cwd prevents duplicate build")

let sampleRows: [[String: Any]] = [
    ["utterance_id": "route-1", "turn_id": "route-1", "category": "routing", "created_at": "2026-10-15T15:00:00Z", "player_input_text": "ありがとう🐶\nもう来たね", "text": "", "source": "voice", "playback_status": "not_selected", "request_classification": ["prediction": ["label": "not_request", "probabilities": [0.91, 0.01, 0.02, 0.03, 0.03], "inference_ms": 4.5]], "forwarded_input": ["turn_id": "reply-1"]],
    ["utterance_id": "reply-1", "turn_id": "reply-1", "player_input_text": "ありがとう🐶\nもう来たね", "text": "来たで！", "playback_status": "completed"],
    ["utterance_id": "route-2", "turn_id": "route-2", "category": "routing", "player_input_text": "ありがとう🐶\nもう来たね", "text": "", "playback_status": "routing"],
    ["utterance_id": "warning-1", "turn_id": "warning-1", "text": "剣やな、持ち替えるで！", "playback_status": "audio_disabled"],
    ["utterance_id": "failed-1", "turn_id": "failed-1", "player_input_text": "何してる？", "playback_status": "failed", "error": "分類失敗"],
    ["utterance_id": "empty", "text": ""],
]
let log = try DialogueLog.decode(JSONSerialization.data(withJSONObject: ["utterances": sampleRows]))
check(log.entries.count == 5, "show inputs, replies, warnings, failures; skip empty rows")
check(log.entries.filter { $0.input == "ありがとう🐶\nもう来たね" }.count == 2, "keep identical successive inputs; suppress only explicitly forwarded duplicate")
check(log.entries.first?.classification == "not_request" && log.entries.first?.probability == 0.91, "read classifier label and its own probability")
check(log.entries[1].output == "来たで！" && log.entries[1].input.isEmpty, "forwarded reply remains visible")
check(log.entries[2].status == "routing" && log.entries[4].error == "分類失敗", "pending and failed input remain visible")
let rendered = DialogueLogStyle.render(log, notice: "fixture")
check(rendered.string.contains("あなた · 音声") && rendered.string.contains("ドギド · 配送完了"), "label input and actual output separately")
check(rendered.string.contains("91.00%") && rendered.string.contains("4.5 ms"), "classifier diagnostic values visible")
check(rendered.string.contains("ありがとう🐶\nもう来たね") && rendered.string.contains("エラー: 分類失敗"), "preserve unicode and multiline input and errors")
let badgeRange = (rendered.string as NSString).range(of: "依頼なし · not_request")
check(rendered.attribute(.backgroundColor, at: badgeRange.location, effectiveRange: nil) != nil, "classification has colored background")
let bodyRange = (rendered.string as NSString).range(of: "ありがとう")
check(rendered.attribute(.backgroundColor, at: bodyRange.location, effectiveRange: nil) == nil, "ordinary text is not classified by substring")
for status in ["generating", "quiet", "cancelled"] {
    var rows = sampleRows
    rows[1]["text"] = ""; rows[1]["playback_status"] = status
    let pending = try DialogueLog.decode(JSONSerialization.data(withJSONObject: ["utterances": rows]))
    check(pending.entries.count == 5 && pending.entries[1].input.isEmpty, "keep empty forwarded response: \(status)")
    check(DialogueLogStyle.render(pending, notice: "").string.contains("ドギド · " + DialogueLogStyle.status(status)), "show response progress: \(status)")
}
let names = ["not_request", "enemy_information", "equip_sword", "recall_mount", "other_request"]
check(Set(names.map { DialogueLogStyle.badge($0).color.description }).count == 5, "five distinct classification colors")
for (index, name) in names.enumerated() {
    var scores = [0.01, 0.01, 0.01, 0.01, 0.01]; scores[index] = 0.96
    let value = try DialogueLog.decode(JSONSerialization.data(withJSONObject: ["utterances": [["utterance_id": name, "request_classification": ["prediction": ["label": name, "probabilities": scores]]]]]))
    check(value.entries.first?.probability == 0.96 && DialogueLogStyle.render(value, notice: "").string.contains(name), "each label and probability mapping: \(name)")
}
let unknown = try DialogueLog.decode(Data(#"{"utterances":[{"utterance_id":"future","request_classification":{"prediction":{"label":"future_label","probabilities":[]}}}]}"#.utf8))
check(unknown.entries.first?.probability == nil && DialogueLogStyle.render(unknown, notice: "").string.contains("future_label"), "unknown label is explicit and neutral")
let empty = try DialogueLog.decode(Data(#"{"utterances":[]}"#.utf8))
check(empty.entries.isEmpty && DialogueLogStyle.render(empty, notice: "").string.contains("まだありません"), "empty snapshot message")
rejects("bad JSON rejected") { _ = try DialogueLog.decode(Data("invalid".utf8)) }
rejects("missing utterances rejected") { _ = try DialogueLog.decode(Data(#"{"ok":true}"#.utf8)) }
let many = (0..<240).map { ["utterance_id": "u\($0)", "text": "reply \($0)"] }
let bounded = try DialogueLog.decode(JSONSerialization.data(withJSONObject: ["utterances": many]))
check(bounded.entries.count == 200, "bounded dialogue rows")

print("\(count) unit checks passed"); fflush(stdout)

if CommandLine.arguments.contains("--integration") {
    let executable = URL(fileURLWithPath: CommandLine.arguments.last!)
    let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("dogido manager fixture " + UUID().uuidString).resolvingSymlinksInPath()
    try FileManager.default.createDirectory(at: temporary.appendingPathComponent("dogido-rust/target/release"), withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporary) }
    let native = temporary.appendingPathComponent("dogido-rust/target/release/dogido-rust")
    try FileManager.default.copyItem(at: executable, to: native)
    let socketFD = socket(AF_INET, SOCK_STREAM, 0)
    var address = sockaddr_in(); address.sin_family = sa_family_t(AF_INET); address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_addr.s_addr = inet_addr("127.0.0.1")
    let bound = withUnsafePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(socketFD, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
    check(bound == 0, "reserve isolated port")
    var length = socklen_t(MemoryLayout<sockaddr_in>.size)
    _ = withUnsafeMutablePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(socketFD, $0, &length) } }
    let port = Int(UInt16(bigEndian: address.sin_port)); close(socketFD)
    let configuration = ManagerConfiguration(root: temporary, baseURL: URL(string: "http://127.0.0.1:\(port)")!)
    for kind in ServiceKind.allCases {
        let arguments = kind == .server ? "serve-dialogue --listen 127.0.0.1:\(port)" : "voice-input --settings '{\"base_url\":\"http://127.0.0.1:\(port)\"}'"
        let script = "#!/bin/zsh\ncd -- \"${0:A:h:h}\"\nexec ./dogido-rust/target/release/dogido-rust \(arguments)\n"
        let path = temporary.appendingPathComponent("dogido-rust/" + kind.launcher)
        try script.write(to: path, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: path.path)
    }
    let manager = ServiceController(configuration: configuration)
    func awaitState(_ kind: ServiceKind, _ expected: String, using control: ServiceController? = nil) -> ServiceSnapshot {
        var last = ""
        for _ in 0..<10 {
            let found = (control ?? manager).snapshots().first { $0.kind == kind }!
            if found.state == expected { return found }
            last = found.state + ": " + found.message
            Thread.sleep(forTimeInterval: 0.1)
        }
        fail("Timed out waiting for \(kind): \(expected), last=\(last)")
    }
    check(manager.snapshots().allSatisfy(\.stopped), "fixture starts stopped")
    rejects("mic requires server") { try manager.start(.voice) }
    let output = temporary.appendingPathComponent("external.log")
    FileManager.default.createFile(atPath: output.path, contents: nil)
    let handle = try FileHandle(forWritingTo: output)
    let external = Process(); external.executableURL = native
    external.arguments = ["serve-dialogue", "--listen", "127.0.0.1:\(port)"]
    external.currentDirectoryURL = temporary; external.standardOutput = handle; external.standardError = handle
    try external.run(); try handle.close()
    cleanup.append { if external.isRunning { external.terminate(); external.waitUntilExit() } }
    defer { if external.isRunning { external.terminate(); external.waitUntilExit() } }
    let found = awaitState(.server, "稼働中")
    print("external fixture detected"); fflush(stdout)
    check(found.process?.pid == external.processIdentifier, "discover external server")
    check(manager.terminalOutput(found).contains("mock started"), "read externally captured stdout")
    let authenticated = ServiceController(configuration: ManagerConfiguration(root: temporary, baseURL: configuration.baseURL, authToken: "fixture-ok"))
    let remoteLog = try authenticated.dialogueLog()
    check(remoteLog.entries.first?.input == "ありがとう" && remoteLog.entries.first?.classification == "not_request", "fetch snapshot over existing authenticated API")
    check(remoteLog.entries.last?.output == "どういたしまして！", "fetch assistant output")
    rejects("missing API token rejected") { _ = try manager.dialogueLog() }
    let invalid = ServiceController(configuration: ManagerConfiguration(root: temporary, baseURL: configuration.baseURL, authToken: "fixture-invalid"))
    rejects("invalid snapshot response rejected") { _ = try invalid.dialogueLog() }
    let redirected = ServiceController(configuration: ManagerConfiguration(root: temporary, baseURL: configuration.baseURL, authToken: "fixture-redirect"))
    rejects("do not follow authenticated redirect") { _ = try redirected.dialogueLog() }

    rejects("duplicate external server") { try manager.start(.server) }
    let other = ManagerConfiguration(root: temporary.appendingPathComponent("wrong"), baseURL: configuration.baseURL)
    let foreign = ServiceController(configuration: other).snapshots().first { $0.kind == .server }!
    check(foreign.state == "要確認" && !foreign.canStop, "foreign checkout cannot stop")
    rejects("foreign port start rejected") { try ServiceController(configuration: other).start(.server) }
    try manager.stop(found); external.waitUntilExit()
    check(awaitState(.server, "停止中").stopped, "stop only isolated external server")
    try manager.start(.server)
    let own = awaitState(.server, "稼働中")
    cleanup.append { try? manager.stop(own) }
    defer { try? manager.stop(own) }
    try manager.start(.voice)
    let voice = awaitState(.voice, "稼働中")
    cleanup.append { try? manager.stop(voice) }
    defer { try? manager.stop(voice) }
    check(voice.process?.arguments[1] == "voice-input", "recognize portless voice")
    rejects("duplicate mic blocked") { try manager.start(.voice) }
    let independent = ServiceController(configuration: configuration)
    check(awaitState(.server, "稼働中", using: independent).process?.pid == own.process?.pid, "new manager discovers same server")
    check(independent.terminalOutput(voice).contains("mock started"), "another manager reads microphone output")
    try independent.stop(voice)
    check(awaitState(.voice, "停止中").stopped, "stop microphone separately")
    check(awaitState(.server, "稼働中").process?.pid == own.process?.pid, "microphone stop preserves server")
    try independent.stop(own)
    check(awaitState(.server, "停止中").stopped, "stop server from independent manager")
    let tail = try ServiceController.readTail(output, maximum: 12)
    check(tail.utf8.count <= 12 && tail.contains("stopped"), "bounded terminal output")
    rejects("directory is not terminal output") { _ = try ServiceController.readTail(temporary) }
}
print("\(count) checks passed")

// 既存の起動設定・プロセス状態・ログを読み、明示操作時だけ本体とマイクを起動停止する。
import Foundation
import Darwin

struct ManagerError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct ManagerConfiguration {
    let root: URL
    let baseURL: URL
    let authToken: String?
    init(root: URL, baseURL: URL, authToken: String? = nil) {
        self.root = root.standardizedFileURL.resolvingSymlinksInPath()
        self.baseURL = baseURL
        self.authToken = authToken
    }
    var binary: URL { root.appendingPathComponent("dogido-rust/target/release/dogido-rust") }
    var python: URL { root.appendingPathComponent("dogido-llm/bin/python") }
    var port: Int { baseURL.port ?? 5055 }

    static func load(root: URL) throws -> ManagerConfiguration {
        let helper = root.appendingPathComponent("apps/dogido-server-manager/Tools/read_settings.py")
        let output = try ProcessReader.command(root.appendingPathComponent("dogido-llm/bin/python").path,
                                               [helper.path, root.path], directory: root)
        guard let data = output.data(using: .utf8),
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let address = object["base_url"] as? String,
              let url = URL(string: address), url.scheme == "http",
              ["127.0.0.1", "localhost", "::1", "[::1]"].contains(url.host ?? ""),
              let port = url.port, (1...65535).contains(port) else {
            throw ManagerError(message: "本体の接続設定を読み取れませんでした。")
        }
        return ManagerConfiguration(root: root.standardizedFileURL.resolvingSymlinksInPath(), baseURL: url,
                                    authToken: object["auth_token"] as? String)
    }
}

enum ServiceKind: String, CaseIterable {
    case server, voice
    var title: String { self == .server ? "ドギド本体" : "マイク・音声入力" }
    var launcher: String { self == .server ? "start_dialogue.command" : "start_voice.command" }
    var subcommand: String { self == .server ? "serve-dialogue" : "voice-input" }
}

struct ProcessRecord: Equatable {
    let pid: Int32
    let executable: String
    let arguments: [String]
    let directory: String
    let started: String
    let logFiles: [String]
}

struct ServiceSnapshot {
    let kind: ServiceKind
    let state: String
    let message: String
    let process: ProcessRecord?
    let trusted: Bool
    var stopped: Bool { state == "停止中" }
    var canStop: Bool { trusted && process != nil && state != "起動準備中" }
}

enum ProcessReader {
    // stdout is consumed before waitUntilExit so larger ps output cannot fill a pipe.
    static func command(_ executable: String, _ arguments: [String], directory: URL? = nil) throws -> String {
        let process = Process(), pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = directory
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        let deadline = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 5, execute: deadline)
        let bytes = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        deadline.cancel()
        guard process.terminationReason == .exit, [0, 1].contains(process.terminationStatus) else {
            throw ManagerError(message: "状態の読み取りに失敗しました。")
        }
        return String(decoding: bytes, as: UTF8.self)
    }

    static func listeners(port: Int) throws -> Set<Int32> {
        let output = try command("/usr/sbin/lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-t"])
        return Set(output.split(separator: "\n").compactMap { Int32($0) })
    }

    // KERN_PROCARGS2 preserves argument boundaries, including spaces in project paths.
    // Neither arguments nor environment values are shown or written to logs by the manager.
    static func decodeArguments(_ data: [UInt8]) -> [String]? {
        guard data.count > MemoryLayout<Int32>.size else { return nil }
        let argc = data.withUnsafeBytes { $0.loadUnaligned(as: Int32.self) }
        guard argc > 0, argc < 16384 else { return nil }
        var offset = MemoryLayout<Int32>.size
        while offset < data.count && data[offset] != 0 { offset += 1 }
        while offset < data.count && data[offset] == 0 { offset += 1 }
        var arguments: [String] = []
        for _ in 0..<argc {
            guard offset < data.count, let end = data[offset...].firstIndex(of: 0) else { return nil }
            arguments.append(String(decoding: data[offset..<end], as: UTF8.self))
            offset = end + 1
        }
        return arguments
    }

    static func record(pid: Int32, configuration: ManagerConfiguration? = nil) throws -> ProcessRecord? {
        var mib = [CTL_KERN, KERN_PROCARGS2, pid]
        var size = 0
        guard sysctl(&mib, 3, nil, &size, nil, 0) == 0, size > 0, size <= 1_048_576 else { return nil }
        var data = [UInt8](repeating: 0, count: size)
        guard sysctl(&mib, 3, &data, &size, nil, 0) == 0,
              let arguments = decodeArguments(Array(data.prefix(size))) else { return nil }
        var path = [CChar](repeating: 0, count: 4096)
        guard proc_pidpath(pid, &path, UInt32(path.count)) > 0 else { return nil }
        let executable = String(cString: path)
        if let configuration {
            let knownScripts = ["dogido-rust/start_dialogue.command", "dogido-rust/start_voice.command", "dogido-rust/scripts/launch_dialogue.py"]
            let isScript = arguments.prefix(4).contains { argument in
                knownScripts.contains { argument == configuration.root.appendingPathComponent($0).path || argument == $0 || argument == "./" + $0 }
            }
            // Inspect cwd/FDs only for this project's native binary or launchers.
            // Running lsof once per unrelated Python/shell process makes polling slow.
            if URL(fileURLWithPath: executable).resolvingSymlinksInPath() != configuration.binary.resolvingSymlinksInPath() && !isScript { return nil }
        }
        let files = try command("/usr/sbin/lsof", ["-nP", "-a", "-p", String(pid), "-d", "cwd,1,2", "-Fftn"])
        var fd = "", type = "", cwd = "", logs: [String] = []
        for line in files.split(separator: "\n") {
            let value = String(line.dropFirst())
            switch line.first {
            case "f": fd = value; type = ""
            case "t": type = value
            case "n":
                if fd == "cwd" { cwd = value }
                if ["1", "2"].contains(fd), type == "REG", value.hasPrefix("/"), !logs.contains(value) { logs.append(value) }
            default: break
            }
        }
        let started = try command("/bin/ps", ["-p", String(pid), "-o", "lstart="]).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !started.isEmpty, !cwd.isEmpty else { return nil }
        return ProcessRecord(pid: pid, executable: executable, arguments: arguments,
                             directory: cwd, started: started, logFiles: logs)
    }

    static func records(additional: Set<Int32>, configuration: ManagerConfiguration) throws -> [ProcessRecord] {
        let output = try command("/bin/ps", ["-axo", "pid=,comm="])
        guard !output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw ManagerError(message: "プロセス一覧を読み取れませんでした。")
        }
        var ids = additional
        for row in output.split(separator: "\n") {
            let parts = row.split(maxSplits: 1, whereSeparator: { $0.isWhitespace })
            guard parts.count == 2, let pid = Int32(parts[0]) else { continue }
            let name = URL(fileURLWithPath: String(parts[1])).lastPathComponent.lowercased()
            if name == "dogido-rust" || name.hasPrefix("python") || ["zsh", "nohup", "start_dialogue.command", "start_voice.command"].contains(name) {
                ids.insert(pid)
            }
        }
        return try ids.sorted().compactMap { try record(pid: $0, configuration: additional.contains($0) ? nil : configuration) }
    }
}

final class ServiceController {
    let configuration: ManagerConfiguration
    let logDirectory: URL
    private var launched: [ServiceKind: Process] = [:]
    private var launchErrors: [ServiceKind: String] = [:]

    init(configuration: ManagerConfiguration, logDirectory: URL? = nil) {
        self.configuration = configuration
        self.logDirectory = logDirectory ?? configuration.root.appendingPathComponent(".dogido_tools/server-manager/logs")
    }

    static func option(_ name: String, in arguments: [String]) -> String? {
        var values: [String] = []
        for (index, value) in arguments.enumerated() {
            if value.hasPrefix(name + "=") { values.append(String(value.dropFirst(name.count + 1))) }
            else if value == name, arguments.indices.contains(index + 1) { values.append(arguments[index + 1]) }
        }
        return values.count == 1 ? values.first : nil
    }

    static func sameEndpoint(_ left: URL, _ right: URL) -> Bool {
        func host(_ url: URL) -> String {
            let host = (url.host ?? "").lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
            return host == "localhost" ? "127.0.0.1" : host
        }
        return left.scheme == "http" && right.scheme == "http" && host(left) == host(right)
            && left.port == right.port && ["", "/"].contains(left.path) && ["", "/"].contains(right.path)
    }

    static func matches(_ process: ProcessRecord, kind: ServiceKind, configuration: ManagerConfiguration) -> Bool {
        guard process.pid > 1, URL(fileURLWithPath: process.directory).resolvingSymlinksInPath() == configuration.root,
              URL(fileURLWithPath: process.executable).standardizedFileURL.resolvingSymlinksInPath() == configuration.binary.resolvingSymlinksInPath(),
              process.arguments.count >= 2, process.arguments[1] == kind.subcommand,
              !process.arguments.contains("--check") else { return false }
        if kind == .server {
            guard let listen = option("--listen", in: process.arguments), let endpoint = URL(string: "http://" + listen) else { return false }
            return sameEndpoint(endpoint, configuration.baseURL)
        }
        guard let value = option("--settings", in: process.arguments), let data = value.data(using: .utf8),
              let settings = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let base = settings["base_url"] as? String, let url = URL(string: base) else { return false }
        return sameEndpoint(url, configuration.baseURL)
    }

    static func isLauncher(_ process: ProcessRecord, kind: ServiceKind, configuration: ManagerConfiguration) -> Bool {
        guard !process.arguments.contains("--check") else { return false }
        let script = configuration.root.appendingPathComponent("dogido-rust/" + kind.launcher).path
        if process.arguments.prefix(3).contains(script) { return true }
        guard URL(fileURLWithPath: process.directory).resolvingSymlinksInPath() == configuration.root else { return false }
        if process.arguments.prefix(3).contains(where: { $0 == "dogido-rust/" + kind.launcher || $0 == "./dogido-rust/" + kind.launcher }) { return true }
        let pythonScript = configuration.root.appendingPathComponent("dogido-rust/scripts/launch_dialogue.py").path
        return process.arguments.prefix(4).contains(pythonScript) && process.arguments.contains("--voice") == (kind == .voice)
    }

    func snapshots() -> [ServiceSnapshot] {
        do {
            let listeners = try ProcessReader.listeners(port: configuration.port)
            let records = try ProcessReader.records(additional: listeners.union(launched.values.filter(\.isRunning).map(\.processIdentifier)), configuration: configuration)
            return ServiceKind.allCases.map { kind in
                if let process = launched[kind], !process.isRunning {
                    if process.terminationStatus != 0 { launchErrors[kind] = "前回の起動が終了しました（終了コード \(process.terminationStatus)）。ターミナル出力を確認してください。" }
                    launched.removeValue(forKey: kind)
                }
                let value = snapshot(kind, records: records, listeners: listeners)
                if kind == .server, value.state == "稼働中", !healthReady() {
                    return ServiceSnapshot(kind: kind, state: "応答待ち", message: "本体プロセスはありますが、会話APIの応答を確認できません。", process: value.process, trusted: value.trusted)
                }
                return value
            }
        } catch {
            return ServiceKind.allCases.map { ServiceSnapshot(kind: $0, state: "確認できません", message: error.localizedDescription, process: nil, trusted: false) }
        }
    }

    private func healthReady() -> Bool {
        let url = configuration.baseURL.appendingPathComponent("healthz").absoluteString
        guard let output = try? ProcessReader.command("/usr/bin/curl", ["--noproxy", "*", "--connect-timeout", "1", "--max-time", "2", "-fsS", url]),
              let data = output.data(using: .utf8), let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return value["ok"] as? Bool == true && value["runtime"] as? String == "rust" && value["dialogue_ready"] as? Bool == true
    }

    /// バックグラウンド更新から既存表示APIを読む。認証値は引数・ログ・永続キャッシュへ渡さない。
    func dialogueLog() throws -> DialogueLog {
        var request = URLRequest(url: configuration.baseURL.appendingPathComponent("api/v1/display/snapshot"))
        request.timeoutInterval = 2
        if let token = configuration.authToken, !token.isEmpty {
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        let settings = URLSessionConfiguration.ephemeral
        settings.connectionProxyDictionary = [:]
        settings.timeoutIntervalForResource = 3
        let response = SnapshotResponse()
        let session = URLSession(configuration: settings, delegate: response, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let done = DispatchSemaphore(value: 0)
        session.dataTask(with: request) { data, reply, error in
            response.data = data; response.reply = reply as? HTTPURLResponse; response.error = error
            done.signal()
        }.resume()
        guard done.wait(timeout: .now() + 3) == .success else {
            throw ManagerError(message: "対話の更新がタイムアウトしました。")
        }
        guard response.error == nil, let reply = response.reply else {
            throw ManagerError(message: "対話の更新に接続できませんでした。")
        }
        guard reply.statusCode == 200 else {
            throw ManagerError(message: reply.statusCode == 401 ? "対話の読み取り認証に失敗しました。設定を確認してください。" : "対話の読み取りに失敗しました（HTTP \(reply.statusCode)）。")
        }
        return try DialogueLog.decode(response.data ?? Data())
    }

    func snapshot(_ kind: ServiceKind, records: [ProcessRecord], listeners: Set<Int32>) -> ServiceSnapshot {
        let candidates = records.filter { Self.matches($0, kind: kind, configuration: configuration) }
        let foreign = kind == .server && !listeners.subtracting(candidates.map(\.pid)).isEmpty
        if foreign || candidates.count > 1 {
            return ServiceSnapshot(kind: kind, state: "要確認", message: foreign ? "接続先ポートを別のプロセスが使っています。" : "同じ本体の候補が複数あります。", process: nil, trusted: false)
        }
        if let found = candidates.first {
            return ServiceSnapshot(kind: kind, state: kind == .server && !listeners.contains(found.pid) ? "起動中" : "稼働中",
                                   message: "PID \(found.pid) · \(found.started)", process: found, trusted: true)
        }
        if records.contains(where: { Self.isLauncher($0, kind: kind, configuration: configuration) }) || launched[kind]?.isRunning == true {
            return ServiceSnapshot(kind: kind, state: "起動準備中", message: "ビルド・起動の進行はターミナル出力で確認できます。", process: nil, trusted: false)
        }
        return ServiceSnapshot(kind: kind, state: "停止中", message: launchErrors[kind] ?? "", process: nil, trusted: false)
    }

    static func validateStop(expected: ServiceSnapshot, current: ServiceSnapshot) throws -> Int32 {
        guard expected.kind == current.kind, expected.canStop, current.canStop,
              let old = expected.process, let now = current.process, old.pid > 1,
              old.pid == now.pid, !old.started.isEmpty, old.started == now.started,
              old.executable == now.executable, old.arguments == now.arguments, old.directory == now.directory else {
            throw ManagerError(message: "表示後に対象が変わりました。状態を更新してから操作してください。")
        }
        return old.pid
    }

    static func launchEnvironment(inheriting source: [String: String], configuration: ManagerConfiguration) -> [String: String] {
        var result = source
        var paths = (source["PATH"].flatMap { $0.isEmpty ? nil : $0 } ?? "/usr/bin:/bin:/usr/sbin:/sbin").split(separator: ":", omittingEmptySubsequences: false).map(String.init)
        for path in ["/opt/homebrew/bin", "/usr/local/bin", FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".cargo/bin").path] where !paths.contains(path) { paths.append(path) }
        result["PATH"] = paths.joined(separator: ":")
        result["DOGIDO_RUST_SETTINGS_DIR"] = configuration.root.path
        result["DOGIDO_PYTHON"] = configuration.python.path
        result["PYTHONUNBUFFERED"] = "1"
        return result
    }

    func start(_ kind: ServiceKind) throws {
        let states = snapshots()
        guard states.first(where: { $0.kind == kind })?.stopped == true else { throw ManagerError(message: "すでに起動しているか、状態の確認が必要です。") }
        if kind == .voice, states.first(where: { $0.kind == .server })?.state != "稼働中" { throw ManagerError(message: "先にドギド本体を起動してください。") }
        let script = configuration.root.appendingPathComponent("dogido-rust/" + kind.launcher)
        guard FileManager.default.isExecutableFile(atPath: script.path) else { throw ManagerError(message: "既存の起動ファイルが見つかりません。") }
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
        let url = logDirectory.appendingPathComponent(kind.rawValue + ".log")
        if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
        let log = try FileHandle(forWritingTo: url)
        defer { try? log.close() }
        try log.seekToEnd()
        try log.write(contentsOf: Data("\n--- \(ISO8601DateFormatter().string(from: Date())) \(kind.title) 起動 ---\n".utf8))
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/nohup")
        process.arguments = [script.path]
        process.currentDirectoryURL = configuration.root
        process.environment = Self.launchEnvironment(inheriting: ProcessInfo.processInfo.environment, configuration: configuration)
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = log
        process.standardError = log
        try process.run()
        launched[kind] = process
        launchErrors.removeValue(forKey: kind)
    }

    func stop(_ expected: ServiceSnapshot) throws {
        guard let current = snapshots().first(where: { $0.kind == expected.kind }) else { throw ManagerError(message: "状態を確認できません。") }
        if current.stopped { return }
        let pid = try Self.validateStop(expected: expected, current: current)
        guard Darwin.kill(pid, SIGTERM) == 0 else { throw ManagerError(message: "停止要求を送れませんでした。") }
    }

    static func readTail(_ path: URL, maximum: UInt64 = 96000) throws -> String {
        let resource = try path.resourceValues(forKeys: [.isRegularFileKey])
        guard resource.isRegularFile == true else { throw ManagerError(message: "通常のログファイルではありません。") }
        let file = try FileHandle(forReadingFrom: path)
        defer { try? file.close() }
        let size = try file.seekToEnd()
        try file.seek(toOffset: size > maximum ? size - maximum : 0)
        let text = String(decoding: try file.read(upToCount: Int(maximum)) ?? Data(), as: UTF8.self)
        return text.replacingOccurrences(of: "\u{001B}\\[[0-?]*[ -/]*[@-~]", with: "", options: .regularExpression)
    }

    func terminalOutput(_ snapshot: ServiceSnapshot) -> String {
        let own = logDirectory.appendingPathComponent(snapshot.kind.rawValue + ".log")
        let paths: [URL]
        if let process = snapshot.process {
            guard !process.logFiles.isEmpty else {
                return "このプロセスは外部ターミナルに直接出力しています。出力履歴は起動元のターミナルで確認できます。\nアプリから起動した出力は、この画面に自動更新で表示します。"
            }
            paths = process.logFiles.map { URL(fileURLWithPath: $0) }
        } else { paths = [own] }
        return paths.map { url in
            guard FileManager.default.fileExists(atPath: url.path) else { return "まだ出力はありません。" }
            do { return "出力元: \(url.path)\n\n" + (try Self.readTail(url)) }
            catch { return "出力を読み取れませんでした。\(error.localizedDescription)" }
        }.joined(separator: "\n\n")
    }
}

// 一回の読み取り結果をsemaphoreの完了後に参照する。転送応答には認証値を引き継がない。
private final class SnapshotResponse: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    var data: Data?
    var reply: HTTPURLResponse?
    var error: Error?
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

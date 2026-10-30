import AppKit
import UniformTypeIdentifiers

final class DropArea: NSView {
    var receive: (([URL]) -> Void)?
    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL])
        wantsLayer = true
        layer?.cornerRadius = 16
        layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
    }
    required init?(coder: NSCoder) { fatalError() }
    func urls(_ sender: NSDraggingInfo) -> [URL] {
        (sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? [])
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        let values = urls(sender)
        return !values.isEmpty && values.allSatisfy { $0.pathExtension.lowercased() == "csv" } ? .copy : []
    }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let values = urls(sender)
        guard !values.isEmpty, values.allSatisfy({ $0.pathExtension.lowercased() == "csv" }) else { return false }
        receive?(values)
        return true
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var status: NSTextView!
    var pending: [[URL]] = []
    var working = false
    var lastOutput: URL?
    let workspace = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "DogidoTrainingWorkspace") as! String)

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "ドギド 学習教材を終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 690, height: 490),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "ドギド 学習教材"
        window.center()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 690, height: 490))
        window.contentView = root
        func label(_ value: String, _ frame: NSRect, _ size: CGFloat, _ bold: Bool = false) {
            let text = NSTextField(wrappingLabelWithString: value)
            text.frame = frame
            text.font = bold ? .boldSystemFont(ofSize: size) : .systemFont(ofSize: size)
            root.addSubview(text)
        }
        label("CSVから、無料T4向けの学習教材を作る", NSRect(x: 28, y: 427, width: 640, height: 32), 23, true)
        label("CSVをこの画面かアプリアイコンへドロップしてください。", NSRect(x: 30, y: 393, width: 625, height: 24), 14)
        let drop = DropArea(frame: NSRect(x: 28, y: 252, width: 634, height: 123))
        let hint = NSTextField(labelWithString: "↓  CSVをここへドロップ")
        hint.font = .systemFont(ofSize: 24, weight: .medium)
        hint.alignment = .center
        hint.frame = NSRect(x: 30, y: 44, width: 574, height: 35)
        drop.addSubview(hint)
        drop.receive = { [weak self] urls in self?.enqueue(urls) }
        root.addSubview(drop)
        func button(_ title: String, _ x: CGFloat, _ width: CGFloat, _ selector: Selector) {
            let control = NSButton(title: title, target: self, action: selector)
            control.bezelStyle = .rounded
            control.frame = NSRect(x: x, y: 202, width: width, height: 33)
            root.addSubview(control)
        }
        button("CSVを選ぶ…", 25, 128, #selector(choose))
        button("入力フォルダ", 160, 130, #selector(openWorkspace))
        button("使い方", 296, 90, #selector(openGuide))
        button("出力を開く", 392, 125, #selector(openOutput))
        let scroll = NSScrollView(frame: NSRect(x: 28, y: 26, width: 634, height: 160))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        status = NSTextView(frame: scroll.bounds)
        status.isEditable = false
        status.font = .systemFont(ofSize: 13)
        status.textContainerInset = NSSize(width: 10, height: 10)
        status.string = "TablecruncherでCSVに発話と正解分類を追加して保存します。\nCSVと同じ場所の「出力」フォルダにNotebookとZIPを作ります。\nこのアプリはMacで教材を作ります。学習はColabで実行してください。"
        scroll.documentView = status
        root.addSubview(scroll)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        next()
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        enqueue(filenames.map { URL(fileURLWithPath: $0) })
        sender.reply(toOpenOrPrint: .success)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func enqueue(_ values: [URL]) {
        pending.append(values)
        if window != nil { next() }
    }
    func next() {
        guard window != nil, !working, !pending.isEmpty else { return }
        let urls = pending.removeFirst()
        working = true
        status.string = "教材を作成しています…\n" + urls.map(\.lastPathComponent).joined(separator: "\n")
        let resources = Bundle.main.resourceURL!
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [resources.appendingPathComponent("builder.py").path,
                                 "--assets", resources.appendingPathComponent("assets").path] + urls.map(\.path)
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            var message = ""
            var success = false
            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                message = String(data: data, encoding: .utf8) ?? "処理結果を読み取れませんでした。"
                success = process.terminationStatus == 0
            } catch { message = "教材を作成できませんでした: \(error.localizedDescription)" }
            let resultMessage = message
            let succeeded = success
            DispatchQueue.main.async { [self] in
                if succeeded {
                    let path = resultMessage.trimmingCharacters(in: .whitespacesAndNewlines)
                    lastOutput = URL(fileURLWithPath: path)
                    status.string = "作成しました。\n\(path)\n\nドギド_T4学習.ipynbをColabで開き、同じフォルダの教材.zipをアップロードしてください。"
                    NSWorkspace.shared.activateFileViewerSelecting([lastOutput!])
                } else {
                    status.string = "CSVを確認してください。\n\n" + resultMessage
                }
                working = false
                next()
            }
        }
    }
    @objc func choose() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.commaSeparatedText]
        panel.allowsMultipleSelection = true
        panel.directoryURL = workspace
        if panel.runModal() == .OK { enqueue(panel.urls) }
    }
    @objc func openWorkspace() { NSWorkspace.shared.open(workspace) }
    @objc func openGuide() { NSWorkspace.shared.open(workspace.appendingPathComponent("使い方.md")) }
    @objc func openOutput() {
        let folder = lastOutput ?? workspace.appendingPathComponent("出力")
        if FileManager.default.fileExists(atPath: folder.path) { NSWorkspace.shared.open(folder) }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()

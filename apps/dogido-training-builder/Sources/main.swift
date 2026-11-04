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
    var pending: [(files: [URL], project: URL)] = []
    var projectMenu: NSPopUpButton!
    var labelsButton: NSButton!
    var projectFolders: [URL] = []
    var projectAccess: URL?
    var working = false
    var lastOutput: URL?
    let workspace = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "DogidoTrainingWorkspace") as! String)

    var projectsRoot: URL { URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "TrainingProjectsRoot") as! String) }
    var selectedProject: URL { projectMenu != nil && projectMenu.indexOfSelectedItem >= 0 ? projectFolders[projectMenu.indexOfSelectedItem] : workspace }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let bookmark = UserDefaults.standard.data(forKey: "ProjectFolderAccess") {
            var stale = false
            if let url = try? URL(resolvingBookmarkData: bookmark, options: .withSecurityScope,
                                  relativeTo: nil, bookmarkDataIsStale: &stale) {
                _ = url.startAccessingSecurityScopedResource()
                projectAccess = url
            }
        }
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "ModernBERT 学習教材を終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 690, height: 560),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "ModernBERT 学習教材"
        window.center()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 690, height: 560))
        window.contentView = root
        func label(_ value: String, _ frame: NSRect, _ size: CGFloat, _ bold: Bool = false) {
            let text = NSTextField(wrappingLabelWithString: value)
            text.frame = frame
            text.font = bold ? .boldSystemFont(ofSize: size) : .systemFont(ofSize: size)
            root.addSubview(text)
        }
        label("CSVから、無料T4向けの学習教材を作る", NSRect(x: 28, y: 497, width: 640, height: 32), 23, true)
        label("CSVをこの画面かアプリアイコンへドロップしてください。", NSRect(x: 30, y: 463, width: 625, height: 24), 14)
        projectMenu = NSPopUpButton(frame: NSRect(x: 28, y: 420, width: 270, height: 30))
        projectMenu.target = self
        projectMenu.action = #selector(projectChanged)
        root.addSubview(projectMenu)
        let create = NSButton(title: "新規プロジェクト…", target: self, action: #selector(newProject))
        create.bezelStyle = .rounded
        create.frame = NSRect(x: 310, y: 420, width: 170, height: 30)
        root.addSubview(create)
        labelsButton = NSButton(title: "分類一覧を開く", target: self, action: #selector(openLabels))
        labelsButton.bezelStyle = .rounded
        labelsButton.frame = NSRect(x: 490, y: 420, width: 170, height: 30)
        root.addSubview(labelsButton)
        reloadProjects(select: pending.first?.project ?? workspace)
        let drop = DropArea(frame: NSRect(x: 28, y: 280, width: 634, height: 123))
        let hint = NSTextField(labelWithString: "↓  CSVをここへドロップ")
        hint.font = .systemFont(ofSize: 24, weight: .medium)
        hint.alignment = .center
        hint.frame = NSRect(x: 30, y: 70, width: 574, height: 35)
        drop.addSubview(hint)
        let generate = NSButton(title: "教材ZIPを作る", target: self, action: #selector(buildSelectedDataset))
        generate.bezelStyle = .rounded
        generate.frame = NSRect(x: 215, y: 20, width: 205, height: 34)
        drop.addSubview(generate)
        drop.receive = { [weak self] urls in self?.enqueue(urls) }
        root.addSubview(drop)
        func button(_ title: String, _ x: CGFloat, _ width: CGFloat, _ selector: Selector) {
            let control = NSButton(title: title, target: self, action: selector)
            control.bezelStyle = .rounded
            control.frame = NSRect(x: x, y: 230, width: width, height: 33)
            root.addSubview(control)
        }
        button("CSVを選ぶ…", 25, 128, #selector(choose))
        button("入力フォルダ", 160, 130, #selector(openWorkspace))
        button("使い方", 296, 90, #selector(openGuide))
        button("出力を開く", 392, 125, #selector(openOutput))
        button("プロジェクトを開く…", 520, 145, #selector(openProject))
        let scroll = NSScrollView(frame: NSRect(x: 28, y: 26, width: 634, height: 188))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        status = NSTextView(frame: scroll.bounds)
        status.isEditable = false
        status.font = .systemFont(ofSize: 13)
        status.textContainerInset = NSSize(width: 10, height: 10)
        status.string = "Excelで分類一覧と教材CSVを編集し、CSV UTF-8で保存します。\nCSVと同じ場所の「出力」フォルダにNotebookとZIPを作ります。\nこのアプリはMacで教材を作ります。学習はColabで実行してください。"
        scroll.documentView = status
        root.addSubview(scroll)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if projectAccess == nil {
            openProject()
        } else { next() }
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        enqueue(filenames.map { URL(fileURLWithPath: $0) })
        sender.reply(toOpenOrPrint: .success)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func enqueue(_ values: [URL]) {
        let adjacent = values.map { $0.deletingLastPathComponent() }.filter {
            FileManager.default.fileExists(atPath: $0.appendingPathComponent("project.json").path)
        }
        let project = adjacent.first ?? selectedProject
        if projectMenu != nil, !adjacent.isEmpty { reloadProjects(select: project) }
        pending.append((files: values, project: project))
        if window != nil { next() }
    }
    func next() {
        guard window != nil, !working, !pending.isEmpty else { return }
        let request = pending.removeFirst()
        let urls = request.files
        working = true
        status.string = "教材を作成しています…\n" + urls.map(\.lastPathComponent).joined(separator: "\n")
        let resources = Bundle.main.resourceURL!
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [resources.appendingPathComponent("builder.py").path,
                                 "--assets", resources.appendingPathComponent("assets").path,
                                 "--project", request.project.path] + urls.map(\.path)
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
                    status.string = "作成しました。\n\(path)\n\n同じフォルダのNotebook（.ipynb）をColabで開き、教材.zipをアップロードしてください。"
                    NSWorkspace.shared.activateFileViewerSelecting([lastOutput!])
                } else {
                    status.string = "CSVを確認してください。\n\n" + resultMessage
                }
                working = false
                next()
            }
        }
    }
    func reloadProjects(select target: URL) {
        projectFolders = [workspace]
        if let folders = try? FileManager.default.contentsOfDirectory(at: projectsRoot, includingPropertiesForKeys: nil) {
            projectFolders += folders.filter { FileManager.default.fileExists(atPath: $0.appendingPathComponent("project.json").path) }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        }
        func sameFolder(_ first: URL, _ second: URL) -> Bool {
            first.resolvingSymlinksInPath().standardizedFileURL.path.precomposedStringWithCanonicalMapping ==
                second.resolvingSymlinksInPath().standardizedFileURL.path.precomposedStringWithCanonicalMapping
        }
        if !projectFolders.contains(where: { sameFolder($0, target) }) { projectFolders.append(target) }
        projectMenu.removeAllItems()
        for folder in projectFolders {
            let data = try? Data(contentsOf: folder.appendingPathComponent("project.json"))
            let config = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            projectMenu.addItem(withTitle: config?["name"] as? String ?? (folder == workspace ? "ドギド" : folder.lastPathComponent))
        }
        projectMenu.selectItem(at: projectFolders.firstIndex(where: { sameFolder($0, target) }) ?? 0)
        updateProjectControls()
    }
    var selectedConfig: [String: Any] {
        guard let data = try? Data(contentsOf: selectedProject.appendingPathComponent("project.json")),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return value
    }
    var isSpanProject: Bool { selectedConfig["schema"] as? String == "edit_span_project.v1" }
    var selectedDataset: URL {
        let name = selectedConfig["dataset_file"] as? String ?? (selectedConfig["id"] as? String == "dogido" ? "追加データ.csv" : "教材.csv")
        return selectedProject.appendingPathComponent(name)
    }
    func updateProjectControls() {
        labelsButton?.title = isSpanProject ? "教材CSVを開く" : "分類一覧を開く"
        window?.title = isSpanProject ? "学習教材 — ドギド・語句抽出" : "ModernBERT 学習教材"
    }
    @objc func projectChanged() {
        lastOutput = nil
        updateProjectControls()
    }
    @objc func buildSelectedDataset() {
        let dataset = selectedDataset
        guard FileManager.default.fileExists(atPath: dataset.path) else {
            status.string = "教材CSVが見つかりません。CSVを選ぶか、この画面へドロップしてください。\n" + dataset.path
            return
        }
        enqueue([dataset])
    }
    @objc func openProject() {
        let panel = NSOpenPanel()
        panel.title = "プロジェクトフォルダを選択"
        panel.prompt = "このフォルダを使う"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.directoryURL = workspace
        guard panel.runModal() == .OK, let folder = panel.url else { return }
        projectAccess?.stopAccessingSecurityScopedResource()
        _ = folder.startAccessingSecurityScopedResource()
        projectAccess = folder
        if let bookmark = try? folder.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil) {
            UserDefaults.standard.set(bookmark, forKey: "ProjectFolderAccess")
        }
        reloadProjects(select: folder)
        lastOutput = nil
        next()
    }
    @objc func openLabels() {
        NSWorkspace.shared.open(isSpanProject ? selectedDataset : selectedProject.appendingPathComponent("分類一覧.csv"))
    }
    @objc func newProject() {
        let alert = NSAlert()
        alert.messageText = "新規プロジェクト"
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 25))
        field.placeholderString = "プロジェクト名"
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        alert.addButton(withTitle: "作成")
        alert.addButton(withTitle: "キャンセル")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let name = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !name.contains("/"), !name.contains(":"), !name.hasPrefix(".") else {
            status.string = "プロジェクト名を確認してください。"
            return
        }
        let folder = projectsRoot.appendingPathComponent(name)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [Bundle.main.resourceURL!.appendingPathComponent("builder.py").path,
                             "--create-project", folder.path, "--name", name]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            if process.terminationStatus == 0 {
                reloadProjects(select: folder)
                lastOutput = nil
                status.string = "作成しました。\n" + folder.path
                NSWorkspace.shared.open(folder)
            } else { status.string = String(data: data, encoding: .utf8) ?? "作成できませんでした。" }
        } catch { status.string = error.localizedDescription }
    }
    @objc func choose() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.commaSeparatedText]
        panel.allowsMultipleSelection = true
        panel.directoryURL = selectedProject
        if panel.runModal() == .OK { enqueue(panel.urls) }
    }
    @objc func openWorkspace() { NSWorkspace.shared.open(selectedProject) }
    @objc func openGuide() { NSWorkspace.shared.open(Bundle.main.resourceURL!.appendingPathComponent(isSpanProject ? "語句抽出の使い方.md" : "使い方.md")) }
    @objc func openOutput() {
        let folder = lastOutput ?? selectedProject.appendingPathComponent("出力")
        if FileManager.default.fileExists(atPath: folder.path) { NSWorkspace.shared.open(folder) }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()

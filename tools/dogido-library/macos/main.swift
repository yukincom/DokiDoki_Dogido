import AppKit
import WebKit
import UniformTypeIdentifiers

// A dedicated local window. Only its own bookshelf child is stopped on exit.
final class Bookshelf: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKScriptMessageHandlerWithReply {
    private var window: NSWindow!
    private var web: WKWebView!
    private var child: Process?
    private var output = ""
    private var launched = false
    private var mayClose = false
    private var closing = false
    private var baseURL: URL?
    private var choosingResearchDirectory = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "本棚を終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let top = NSMenuItem(); top.submenu = appMenu; menu.addItem(top)
        let fileMenu = NSMenu(title: "ファイル")
        let save = fileMenu.addItem(withTitle: "変更を保存", action: #selector(saveChanges(_:)), keyEquivalent: "s")
        save.target = self
        let file = NSMenuItem(); file.title = "ファイル"; file.submenu = fileMenu; menu.addItem(file)
        let editMenu = NSMenu(title: "編集")
        for (title, selector, key) in [("コピー", "copy:", "c"), ("貼り付け", "paste:", "v"), ("切り取り", "cut:", "x"), ("すべて選択", "selectAll:", "a"), ("取り消す", "undo:", "z")] {
            editMenu.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        let redo = editMenu.addItem(withTitle: "やり直す", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        let edit = NSMenuItem(); edit.title = "編集"; edit.submenu = editMenu; menu.addItem(edit)
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "ドギドの本棚"
        window.minSize = NSSize(width: 650, height: 600)
        window.delegate = self
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, name: "dogidoPhotoFolder")
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "dogidoExport")
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "dogidoResearchDirectory")
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        window.contentView = web
        web.loadHTMLString("<html lang='ja'><body style='background:#f8f0df;font:22px sans-serif;padding:50px'>ドギドの本棚を開いています…</body></html>", baseURL: nil)
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        guard let root = Bundle.main.object(forInfoDictionaryKey: "DogidoProjectRoot") as? String else {
            fail("起動元が見つかりません。本棚アプリを作り直してください。"); return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = [root + "/scripts/start_dogido_library.command", "--port", "0"]
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.standardError
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            let chunk = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async { self?.readOutput(chunk) }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self = self, !self.mayClose else { return }
                self.fail("本棚の保存処理が終了しました。アプリを開き直してください。")
            }
        }
        do { try process.run(); child = process }
        catch { fail("本棚を起動できませんでした。実行環境を確認してください。") }
    }

    @objc private func saveChanges(_ sender: Any?) {
        guard launched, web.url?.host == baseURL?.host, web.url?.port == baseURL?.port else { return }
        web.evaluateJavaScript("window.dogidoLibrarySave?.(); void 0")
    }

    private func readOutput(_ chunk: String) {
        guard !launched else { return }
        output += chunk
        for line in output.components(separatedBy: "\n") where line.hasPrefix("DOGIDO_LIBRARY_URL=") {
            guard let url = URL(string: String(line.dropFirst("DOGIDO_LIBRARY_URL=".count))), url.host == "127.0.0.1", url.port != nil else { continue }
            launched = true; baseURL = url; awaitServer(url, attempts: 40); return
        }
    }

    private func awaitServer(_ url: URL, attempts: Int) {
        var request = URLRequest(url: url); request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self = self, !self.mayClose else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 { self.web.load(URLRequest(url: url)); return }
                if attempts <= 0 { self.fail("本棚の起動を確認できませんでした。アプリを開き直してください。"); return }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.awaitServer(url, attempts: attempts - 1) }
            }
        }.resume()
    }

    private func fail(_ message: String) {
        let alert = NSAlert(); alert.messageText = message; alert.runModal()
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        let local = url.scheme == "http" && url.host == "127.0.0.1" && url.port == baseURL?.port
        decisionHandler(local || url.scheme == "about" ? .allow : .cancel)
    }

    // 本棚の主フレームからの固定操作だけ。Web側からパスや任意URLを受け取らない。
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.name == "dogidoPhotoFolder",
              message.webView === web,
              message.frameInfo.isMainFrame,
              message.body as? String == "showMinecraftScreenshots",
              let port = baseURL?.port,
              origin.protocol == "http", origin.host == "127.0.0.1", origin.port == port else { return }
        let folder = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/minecraft/screenshots", isDirectory: true)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: folder.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            fail("Minecraft標準の写真フォルダが見つかりません。撮影前、またはゲームディレクトリを変更している可能性があります。"); return
        }
        if !NSWorkspace.shared.open(folder) { fail("写真の場所をFinderで開けませんでした。") }
    }

    private func requestClose() {
        if closing { return }; closing = true
        web.evaluateJavaScript("Boolean(window.dogidoLibraryHasUnsaved?.())") { [weak self] value, _ in
            guard let self = self else { return }; self.closing = false
            if value as? Bool == true {
                let alert = NSAlert(); alert.messageText = "保存していない変更があります。"
                alert.informativeText = "保存するには本棚に戻って「変更を保存」を押してください。"
                alert.addButton(withTitle: "本棚に戻る"); alert.addButton(withTitle: "保存せず終了")
                if alert.runModal() != .alertSecondButtonReturn { return }
            }
            self.mayClose = true; NSApp.terminate(nil)
        }
    }
    // 保存先はユーザーが選択する。Web側からパスを受け取らない。
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        if message.name == "dogidoResearchDirectory" {
            chooseResearchDirectory(message, replyHandler: replyHandler); return
        }
        let origin = message.frameInfo.securityOrigin
        guard message.name == "dogidoExport", message.webView === web, message.frameInfo.isMainFrame,
              let port = baseURL?.port, origin.protocol == "http", origin.host == "127.0.0.1", origin.port == port,
              let text = message.body as? String, let data = text.data(using: .utf8), data.count <= 32 * 1024 * 1024,
              let document = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              document["schemaVersion"] as? Int == 1, document["poems"] is [Any] else {
            replyHandler(nil, "書き出すJSONを確認できませんでした（上限32MB）。"); return
        }
        let panel = NSSavePanel(); panel.allowedContentTypes = [.json]
        panel.nameFieldStringValue = "dogido-poems.json"
        panel.canCreateDirectories = true; panel.prompt = "保存"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { replyHandler("cancelled", nil); return }
            do { try data.write(to: url, options: .atomic); replyHandler("saved", nil) }
            catch { replyHandler(nil, "JSONを保存できませんでした。保存先を確認してください。") }
        }
    }
    // 本棚の主フレームからの固定要求だけを受け、選ばれたディレクトリのパスだけを返す。
    // ファイルの作成・移動・削除や設定保存はしない。Web側の明示保存へ分ける。
    private func chooseResearchDirectory(_ message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        let origin = message.frameInfo.securityOrigin
        guard message.webView === web, message.frameInfo.isMainFrame,
              message.body as? String == "chooseResearchDirectory",
              let port = baseURL?.port, origin.protocol == "http", origin.host == "127.0.0.1", origin.port == port else {
            replyHandler(nil, "本棚からのフォルダ選択要求を確認できませんでした。"); return
        }
        guard !choosingResearchDirectory else {
            replyHandler(nil, "保存先フォルダはすでに選択中です。"); return
        }
        choosingResearchDirectory = true
        let panel = NSOpenPanel()
        panel.title = "研究データの保存先"
        panel.message = "研究データを保存する最後のフォルダそのものを選んでください。"
        panel.prompt = "このフォルダを選択"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self = self else { replyHandler(nil, "本棚が閉じられました。"); return }
            self.choosingResearchDirectory = false
            guard self.web.url?.scheme == "http", self.web.url?.host == "127.0.0.1", self.web.url?.port == port else {
                replyHandler(nil, "本棚のページが変わったため、選択を取り消しました。"); return
            }
            guard response == .OK, let url = panel.url, url.isFileURL else { replyHandler(NSNull(), nil); return }
            replyHandler(url.path, nil)
        }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard frame.request.url?.host == baseURL?.host, frame.request.url?.port == baseURL?.port else {
            completionHandler(nil); return
        }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.prompt = "追加"
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { if !mayClose { requestClose() }; return mayClose }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if mayClose { return .terminateNow }; requestClose(); return .terminateCancel
    }
    func applicationWillTerminate(_ notification: Notification) {
        mayClose = true
        if child?.isRunning == true { child?.terminate() }
    }
}

let application = NSApplication.shared
let delegate = Bookshelf()
application.delegate = delegate
application.run()

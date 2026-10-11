import AppKit
import WebKit

func ink(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: 1)
}

func label(_ text: String, size: CGFloat = 14, bold: Bool = false) -> NSTextField {
    let view = NSTextField(wrappingLabelWithString: text)
    view.font = bold ? .boldSystemFont(ofSize: size) : .systemFont(ofSize: size)
    view.textColor = ink(0x233835)
    view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    return view
}

final class ServiceCard: NSView {
    let state = label("確認中", size: 19, bold: true)
    let detail = label("", size: 12)
    let start = NSButton(title: "起動", target: nil, action: nil)
    let stop = NSButton(title: "停止", target: nil, action: nil)
    var onStart: (() -> Void)?
    var onStop: (() -> Void)?

    init(kind: ServiceKind) {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = ink(kind == .server ? 0xe0efdf : 0xe0ecef).cgColor
        layer?.cornerRadius = 12
        let title = label(kind.title, size: 18, bold: true)
        for button in [start, stop] { button.bezelStyle = .rounded; button.target = self }
        start.action = #selector(startClicked)
        stop.action = #selector(stopClicked)
        let buttons = NSStackView(views: [start, stop])
        buttons.spacing = 10
        let column = NSStackView(views: [title, state, detail, buttons])
        column.orientation = .vertical
        column.alignment = .leading
        column.spacing = 10
        column.translatesAutoresizingMaskIntoConstraints = false
        addSubview(column)
        NSLayoutConstraint.activate([
            column.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 18),
            column.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -18),
            column.topAnchor.constraint(equalTo: topAnchor, constant: 16),
            column.bottomAnchor.constraint(lessThanOrEqualTo: bottomAnchor, constant: -14),
            heightAnchor.constraint(greaterThanOrEqualToConstant: 190),
        ])
        start.isEnabled = false
        stop.isEnabled = false
    }
    required init?(coder: NSCoder) { fatalError("init(coder:)") }
    @objc private func startClicked() { onStart?() }
    @objc private func stopClicked() { onStop?() }

    func update(_ value: ServiceSnapshot, busy: Bool, serverRunning: Bool) {
        state.stringValue = value.state
        state.textColor = value.state == "稼働中" ? ink(0x226c3b) : ink(0x655237)
        detail.stringValue = value.message
        start.isEnabled = !busy && value.stopped && (value.kind == .server || serverRunning)
        stop.isEnabled = !busy && value.canStop
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var historyWindow: NSWindow?
    private var cards: [ServiceKind: ServiceCard] = [:]
    private var controller: ServiceController?
    private var snapshots: [ServiceKind: ServiceSnapshot] = [:]
    private var outputs: [ServiceKind: String] = [:]
    private var busy = false, polling = false
    private var timer: Timer?
    private let worker = DispatchQueue(label: "local.dogido.server-manager")
    private let notice = label("状態を確認しています。", size: 13)
    private let location = label("", size: 11)
    private let history = NSButton(title: "発言履歴を開く ↗", target: nil, action: nil)
    private let terminal = NSTextView()
    private let follow = NSButton(checkboxWithTitle: "末尾を追う", target: nil, action: nil)
    private let tabs = NSSegmentedControl(labels: ["本体の出力", "マイクの出力"], trackingMode: .selectOne, target: nil, action: nil)

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menus = NSMenu()
        let applicationItem = NSMenuItem(), applicationMenu = NSMenu()
        applicationMenu.addItem(withTitle: "ドギド サーバーを終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = applicationMenu; menus.addItem(applicationItem)
        let editItem = NSMenuItem(), edit = NSMenu(title: "編集")
        for (title, action, key) in [("コピー", "copy:", "c"), ("ペースト", "paste:", "v"), ("すべて選択", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = edit; menus.addItem(editItem); NSApp.mainMenu = menus

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "ドギド サーバー"
        window.minSize = NSSize(width: 780, height: 660)
        window.isReleasedWhenClosed = false
        window.backgroundColor = ink(0xf5f5ed)
        let panel = NSView(); window.contentView = panel
        let title = label("ドギド サーバー", size: 27, bold: true)
        let row = NSStackView(); row.orientation = .horizontal; row.distribution = .fillEqually; row.spacing = 16
        for kind in ServiceKind.allCases {
            let card = ServiceCard(kind: kind)
            card.onStart = { [weak self] in self?.perform(kind, stopping: false) }
            card.onStop = { [weak self] in self?.perform(kind, stopping: true) }
            cards[kind] = card; row.addArrangedSubview(card)
        }
        history.target = self; history.action = #selector(openHistory); history.bezelStyle = .rounded; history.isEnabled = false
        let refresh = NSButton(title: "状態を更新", target: self, action: #selector(refreshClicked)); refresh.bezelStyle = .rounded
        let actions = NSStackView(views: [history, refresh]); actions.spacing = 14
        tabs.selectedSegment = 0; tabs.target = self; tabs.action = #selector(logTabChanged)
        follow.state = .on
        let logActions = NSStackView(views: [label("ターミナル出力", size: 16, bold: true), tabs, follow]); logActions.spacing = 18
        terminal.isEditable = false; terminal.isSelectable = true
        terminal.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        terminal.textColor = ink(0xe1eee6); terminal.backgroundColor = ink(0x142722)
        terminal.textContainerInset = NSSize(width: 14, height: 14)
        terminal.isVerticallyResizable = true; terminal.isHorizontallyResizable = false
        terminal.autoresizingMask = [.width]; terminal.textContainer?.widthTracksTextView = true
        terminal.minSize = .zero; terminal.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.documentView = terminal
        let column = NSStackView(views: [title, notice, row, actions, logActions, scroll, location])
        column.orientation = .vertical; column.alignment = .leading; column.spacing = 14
        column.translatesAutoresizingMaskIntoConstraints = false; panel.addSubview(column)
        NSLayoutConstraint.activate([
            column.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 24),
            column.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -24),
            column.topAnchor.constraint(equalTo: panel.topAnchor, constant: 24),
            column.bottomAnchor.constraint(equalTo: panel.bottomAnchor, constant: -18),
            row.widthAnchor.constraint(equalTo: column.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: column.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 200),
        ])
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        loadConfiguration()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.refresh() }
    }

    private func loadConfiguration() {
        guard !polling else { return }; polling = true
        let rootPath = Bundle.main.object(forInfoDictionaryKey: "DogidoProjectRoot") as? String
        worker.async {
            do {
                guard let rootPath else { throw ManagerError(message: "アプリの本体フォルダ設定がありません。") }
                let configuration = try ManagerConfiguration.load(root: URL(fileURLWithPath: rootPath))
                let controller = ServiceController(configuration: configuration)
                DispatchQueue.main.async {
                    self.controller = controller; self.polling = false
                    self.location.stringValue = configuration.root.path + " · " + configuration.baseURL.absoluteString
                    self.notice.stringValue = "起動元に関係なく状態を表示します。アプリを閉じても本体は動き続けます。"
                    self.refresh()
                }
            } catch {
                let message = error.localizedDescription
                DispatchQueue.main.async { self.polling = false; self.notice.stringValue = message }
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }
    func applicationWillTerminate(_ notification: Notification) { timer?.invalidate() }
    @objc private func refreshClicked() { if controller == nil { loadConfiguration() } else { refresh() } }
    @objc private func logTabChanged() { renderLog() }

    private func refresh() {
        guard !polling, !busy, let controller else { return }; polling = true
        worker.async {
            let states = controller.snapshots()
            let logs = Dictionary(uniqueKeysWithValues: states.map { ($0.kind, controller.terminalOutput($0)) })
            DispatchQueue.main.async {
                self.snapshots = Dictionary(uniqueKeysWithValues: states.map { ($0.kind, $0) })
                self.outputs = logs; self.polling = false; self.render()
            }
        }
    }

    private func render() {
        let running = snapshots[.server]?.state == "稼働中"
        for (kind, value) in snapshots { cards[kind]?.update(value, busy: busy, serverRunning: running) }
        history.isEnabled = running; renderLog()
    }

    private func renderLog() {
        let kind: ServiceKind = tabs.selectedSegment == 1 ? .voice : .server
        let value = outputs[kind] ?? "出力を確認しています…"
        guard terminal.string != value else { return }
        let offset = terminal.enclosingScrollView?.contentView.bounds.origin
        terminal.string = value
        if follow.state == .on { terminal.scrollToEndOfDocument(nil) }
        else if let offset { terminal.enclosingScrollView?.contentView.scroll(to: offset) }
    }

    private func perform(_ kind: ServiceKind, stopping: Bool) {
        guard !busy, let controller, let expected = snapshots[kind] else { return }
        busy = true; render(); tabs.selectedSegment = kind == .server ? 0 : 1
        notice.stringValue = stopping ? "\(kind.title)へ停止を依頼しています…" : "\(kind.title)を起動しています…"
        worker.async {
            let message: String
            do {
                if stopping { try controller.stop(expected) } else { try controller.start(kind) }
                message = stopping ? "停止要求を送りました。状態が「停止中」になるまでお待ちください。" : "起動を開始しました。ターミナル出力を確認できます。"
            } catch { message = error.localizedDescription }
            DispatchQueue.main.async { self.busy = false; self.notice.stringValue = message; self.refresh() }
        }
    }

    @objc private func openHistory() {
        guard let controller else { return }
        if let historyWindow { historyWindow.makeKeyAndOrderFront(nil); return }
        let configuration = WKWebViewConfiguration()
        // Share the display mode with the server. Injecting the same idempotent
        // script also supports a server started before this UI-only update.
        if let path = Bundle.main.url(forResource: "utterance-view", withExtension: "js"),
           let script = try? String(contentsOf: path, encoding: .utf8) {
            configuration.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        }
        let web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self; web.uiDelegate = self
        let child = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 950, height: 760),
                             styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        child.title = "ドギド 発言履歴"; child.isReleasedWhenClosed = false; child.contentView = web
        historyWindow = child; child.center(); child.makeKeyAndOrderFront(nil)
        let url = controller.configuration.baseURL.appendingPathComponent("dogido")
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "view", value: "utterances")]
        web.load(URLRequest(url: components.url!))
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, let base = controller?.configuration.baseURL else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.scheme == base.scheme && url.host == base.host && url.port == base.port) { decisionHandler(.allow) }
        else { decisionHandler(.cancel); if ["http", "https"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) } }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.runModal(); completionHandler()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()

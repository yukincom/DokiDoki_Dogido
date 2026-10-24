// 既存display snapshotの対話行を読み、入力・分類・返答をログ用に整形する。
// APIの行IDと明示された転送先だけで入力・分類の重複を除き、本文や本体の状態は変更しない。
import AppKit

struct DialogueLogEntry {
    let id: String
    let timestamp: String
    let input: String
    let output: String
    let isResponse: Bool
    let source: String
    let status: String
    let classifierMode: String?
    let classification: String?
    let probability: Double?
    let inferenceMS: Double?
    let error: String?
}

struct DialogueLog {
    let entries: [DialogueLogEntry]

    /// 現在のsnapshotを毎回置き換える。同文の別入力や進行中の行更新を落とさない。
    static func decode(_ data: Data) throws -> DialogueLog {
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = value["utterances"] as? [[String: Any]] else {
            throw ManagerError(message: "対話データの形式を読み取れませんでした。")
        }
        let visibleRows = rows.suffix(200)
        var forwarded = Set<String>()
        var forwardedClassifications: [String: NSDictionary] = [:]
        for row in visibleRows {
            guard row["utterance_id"] is String,
                  !(row["player_input_text"] as? String ?? "").isEmpty,
                  row["category"] as? String == "routing",
                  let turn = (row["forwarded_input"] as? [String: Any])?["turn_id"] as? String else { continue }
            forwarded.insert(turn)
            if let classification = row["request_classification"] as? [String: Any] {
                forwardedClassifications[turn] = NSDictionary(dictionary: classification)
            }
        }
        let entries = visibleRows.compactMap { row -> DialogueLogEntry? in
            guard let id = row["utterance_id"] as? String else { return nil }
            let turn = row["turn_id"] as? String ?? ""
            let isForwarded = row["category"] as? String != "routing" && forwarded.contains(turn)
            let input = isForwarded ? "" : (row["player_input_text"] as? String ?? "")
            let output = row["text"] as? String ?? ""
            let classification = row["request_classification"] as? [String: Any]
            let mode = classification?["mode"] as? String ?? row["classifier_mode"] as? String
            // 表示中の受付行と同じ結果を引き継いだ返答行だけ、分類の再表示を省く。
            let repeatedClassification = isForwarded && classification.map {
                forwardedClassifications[turn]?.isEqual(to: $0) == true
            } == true
            let prediction = repeatedClassification ? nil : classification?["prediction"] as? [String: Any]
            let name = prediction?["label"] as? String
            let probabilities = prediction?["probabilities"] as? [Double]
            let labels = DialogueLogStyle.labels(for: mode)
            let probability = name.flatMap { name in labels.firstIndex { $0.name == name } }.flatMap { index -> Double? in
                guard let probabilities, probabilities.count == labels.count,
                      probabilities[index].isFinite, (0...1).contains(probabilities[index]) else { return nil }
                return probabilities[index]
            }
            let error = row["error"] as? String
            guard !input.isEmpty || !output.isEmpty || name != nil || error != nil || isForwarded else { return nil }
            return DialogueLogEntry(id: id, timestamp: row["created_at"] as? String ?? "",
                                    input: input, output: output, isResponse: isForwarded, source: row["source"] as? String ?? "",
                                    status: row["playback_status"] as? String ?? "", classifierMode: mode, classification: name,
                                    probability: probability, inferenceMS: prediction?["inference_ms"] as? Double,
                                    error: error)
        }
        return DialogueLog(entries: entries)
    }
}

enum DialogueLogStyle {
    static let foreground = NSColor(srgbRed: 0.88, green: 0.94, blue: 0.90, alpha: 1)
    static let muted = NSColor(srgbRed: 0.64, green: 0.74, blue: 0.69, alpha: 1)

    // 本体のMode::labelsと採用モデルのid2label順。日本語名と確率の対応を同じ表で扱う。
    // 色の番号は領域ごとに0へ戻し、川柳と通常対話の間では再利用する。
    private static let normalLabels: [(name: String, title: String)] = [
        ("not_target", "対象外・その他"),
        ("enemy_information", "モブ情報（敵・友好）"),
        ("equip_sword", "剣に持ち替え"),
        ("recall_mount", "乗用動物を呼び寄せ"),
        ("dogido_recall", "ドギドを呼び寄せ"),
        ("look_direction", "指定方向を向く"),
        ("look_relative_right", "右を向く"),
        ("look_target", "指定した対象を見る"),
        ("look_player", "プレイヤーを見る"),
        ("look_relative_front", "前を向く"),
        ("look_relative_back", "後ろを向く"),
        ("look_relative_left", "左を向く"),
        ("look_relative_front_left", "左前を向く"),
        ("look_relative_front_right", "右前を向く"),
        ("look_relative_back_left", "左後ろを向く"),
        ("look_relative_back_right", "右後ろを向く"),
    ]
    private static let senryuLabels: [(name: String, title: String)] = [
        ("not_target", "対象外・その他"),
        ("senryu_line_upper", "上五の行指定"),
        ("senryu_line_middle", "中七の行指定"),
        ("senryu_line_lower", "下五の行指定"),
        ("senryu_change_upper", "上五の具体変更"),
        ("senryu_change_middle", "中七の具体変更"),
        ("senryu_change_lower", "下五の具体変更"),
        ("senryu_end", "川柳相談の終了"),
    ]
    // 領域のなかった旧snapshotの既存表示だけを維持する。現在の分類先は推測しない。
    private static let legacyLabels: [(name: String, title: String)] = [
        ("not_request", "依頼なし"), ("enemy_information", "敵情報"),
        ("equip_sword", "剣に持ち替え"), ("recall_mount", "呼び寄せ"),
        ("other_request", "その他の依頼"),
    ]
    private static let palette: [UInt32] = [
        0xc3ccc8, 0xa9d9ff, 0xf6d590, 0xd5c3ff,
        0xf2a9a1, 0x8fe2df, 0xe7ec8b, 0xeebcca,
        0x9dcc91, 0xaab7f0, 0xf5b875, 0x8fcbd0,
        0xd3b98c, 0xc2afe0, 0xf2aedd, 0xb8d9f2,
    ]

    static func labels(for mode: String?) -> [(name: String, title: String)] {
        switch mode {
        case "normal": return normalLabels
        case "senryu": return senryuLabels
        case nil: return legacyLabels
        default: return []
        }
    }

    static func classificationTitle(_ mode: String?) -> String {
        switch mode {
        case "normal": return "分類［通常対話］"
        case "senryu": return "分類［川柳］"
        case nil: return "分類"
        default: return "分類［領域不明］"
        }
    }

    /// 構造化された分類欄だけを着色する。本文に同じ単語があっても色は変えない。
    static func badge(_ name: String, mode: String? = nil) -> (title: String, color: NSColor) {
        let definitions = labels(for: mode)
        let index = definitions.firstIndex { $0.name == name }
        let title = index.map { definitions[$0].title } ?? "未対応ラベル"
        let rgb = index.map { palette[$0] } ?? 0xd0d5d2
        return (" \(title) · \(name) ", NSColor(srgbRed: CGFloat((rgb >> 16) & 255) / 255,
                green: CGFloat((rgb >> 8) & 255) / 255, blue: CGFloat(rgb & 255) / 255, alpha: 1))
    }

    static func status(_ raw: String) -> String {
        switch raw {
        case "routing": return "分類中"
        case "generating": return "返答を生成中"
        case "quiet": return "発話なし"
        case "queued": return "配送待ち"
        case "started": return "再生中"
        case "completed": return "配送完了"
        case "audio_disabled": return "音声なし"
        case "cancelled": return "取消"
        case "failed": return "失敗"
        case "not_selected": return "判定済み"
        default: return raw
        }
    }

    static func time(_ raw: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = parser.date(from: raw)
        if date == nil { parser.formatOptions = [.withInternetDateTime]; date = parser.date(from: raw) }
        guard let date else { return raw.isEmpty ? "時刻なし" : raw }
        let formatter = DateFormatter(); formatter.dateFormat = "HH:mm:ss"; formatter.locale = Locale(identifier: "ja_JP")
        return formatter.string(from: date)
    }

    static func render(_ log: DialogueLog?, notice: String) -> NSAttributedString {
        let result = NSMutableAttributedString(string: "")
        let font = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        func append(_ text: String, color: NSColor = foreground, bold: Bool = false) {
            result.append(NSAttributedString(string: text, attributes: [
                .font: bold ? NSFont.monospacedSystemFont(ofSize: 13, weight: .semibold) : font,
                .foregroundColor: color,
            ]))
        }
        if !notice.isEmpty { append(notice + "\n\n", color: muted) }
        guard let log, !log.entries.isEmpty else {
            append("対話はまだありません。本体の起動後、入力・分類・返答をここに表示します。\n", color: muted)
            return result
        }
        for entry in log.entries {
            let stamp = time(entry.timestamp)
            if !entry.input.isEmpty {
                let source = entry.source == "voice" ? "音声" : entry.source == "text" ? "文字" : entry.source
                append("\(stamp)  あなた" + (source.isEmpty ? "" : " · \(source)") + "\n", bold: true)
                append(entry.input + "\n")
            }
            if let name = entry.classification {
                append(classificationTitle(entry.classifierMode) + "  ", color: muted)
                let value = badge(name, mode: entry.classifierMode)
                result.append(NSAttributedString(string: value.title, attributes: [
                    .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .semibold),
                    .foregroundColor: NSColor(srgbRed: 0.08, green: 0.16, blue: 0.13, alpha: 1),
                    .backgroundColor: value.color,
                ]))
                if let probability = entry.probability { append(String(format: "  %.2f%%", probability * 100), color: muted) }
                if let ms = entry.inferenceMS, ms.isFinite, ms >= 0 { append(String(format: " · %.1f ms", ms), color: muted) }
                if ["cancelled", "failed"].contains(entry.status) { append(" · " + status(entry.status), color: muted) }
                append("\n")
            } else if !entry.input.isEmpty, !entry.status.isEmpty, entry.output.isEmpty {
                let domain = entry.classifierMode == nil ? "" : classificationTitle(entry.classifierMode) + " · "
                append(domain + status(entry.status) + "\n", color: muted)
            }
            if !entry.output.isEmpty || entry.isResponse {
                append("\(stamp)  ドギド" + (entry.status.isEmpty ? "" : " · " + status(entry.status)) + "\n", bold: true)
                if !entry.output.isEmpty { append(entry.output + "\n") }
            }
            if let error = entry.error { append("エラー: \(error)\n", color: .systemOrange) }
            append("\n")
        }
        return result
    }
}

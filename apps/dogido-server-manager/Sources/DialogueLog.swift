// 既存display snapshotの対話行を読み、入力・分類・返答をログ用に整形する。
// APIの行IDと明示された転送先だけで入力の重複を除き、本文や本体の状態は変更しない。
import AppKit

struct DialogueLogEntry {
    let id: String
    let timestamp: String
    let input: String
    let output: String
    let isResponse: Bool
    let source: String
    let status: String
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
        let forwarded = Set(rows.compactMap { row -> String? in
            guard !(row["player_input_text"] as? String ?? "").isEmpty,
                  row["category"] as? String == "routing" else { return nil }
            return (row["forwarded_input"] as? [String: Any])?["turn_id"] as? String
        })
        let entries = rows.suffix(200).compactMap { row -> DialogueLogEntry? in
            guard let id = row["utterance_id"] as? String else { return nil }
            let isForwarded = row["category"] as? String != "routing" && forwarded.contains(row["turn_id"] as? String ?? "")
            let input = isForwarded ? "" : (row["player_input_text"] as? String ?? "")
            let output = row["text"] as? String ?? ""
            let prediction = (row["request_classification"] as? [String: Any])?["prediction"] as? [String: Any]
            let name = prediction?["label"] as? String
            let probabilities = prediction?["probabilities"] as? [Double]
            let labels = ["not_request", "enemy_information", "equip_sword", "recall_mount", "other_request"]
            let probability = name.flatMap { labels.firstIndex(of: $0) }.flatMap { index -> Double? in
                guard let probabilities, probabilities.indices.contains(index),
                      probabilities[index].isFinite, (0...1).contains(probabilities[index]) else { return nil }
                return probabilities[index]
            }
            let error = row["error"] as? String
            guard !input.isEmpty || !output.isEmpty || name != nil || error != nil || isForwarded else { return nil }
            return DialogueLogEntry(id: id, timestamp: row["created_at"] as? String ?? "",
                                    input: input, output: output, isResponse: isForwarded, source: row["source"] as? String ?? "",
                                    status: row["playback_status"] as? String ?? "", classification: name,
                                    probability: probability, inferenceMS: prediction?["inference_ms"] as? Double,
                                    error: error)
        }
        return DialogueLog(entries: entries)
    }
}

enum DialogueLogStyle {
    static let foreground = NSColor(srgbRed: 0.88, green: 0.94, blue: 0.90, alpha: 1)
    static let muted = NSColor(srgbRed: 0.64, green: 0.74, blue: 0.69, alpha: 1)

    /// 構造化された分類欄だけを着色する。本文に同じ単語があっても色は変えない。
    static func badge(_ name: String) -> (title: String, color: NSColor) {
        let title: String, rgb: UInt32
        switch name {
        case "not_request": title = "依頼なし"; rgb = 0xb8daca
        case "enemy_information": title = "敵情報"; rgb = 0xa9d9ff
        case "equip_sword": title = "剣に持ち替え"; rgb = 0xf6d590
        case "recall_mount": title = "呼び寄せ"; rgb = 0xd5c3ff
        case "other_request": title = "その他の依頼"; rgb = 0xeebcca
        default: title = "未対応ラベル"; rgb = 0xd0d5d2
        }
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
                append("分類  ", color: muted)
                let value = badge(name)
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
                append(status(entry.status) + "\n", color: muted)
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

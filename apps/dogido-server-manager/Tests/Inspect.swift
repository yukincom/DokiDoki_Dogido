import Foundation

@main struct Inspect {
    static func main() throws {
        let configuration = try ManagerConfiguration.load(root: URL(fileURLWithPath: CommandLine.arguments[1]))
        let control = ServiceController(configuration: configuration)
        for item in control.snapshots() {
            print("\(item.kind.rawValue): \(item.state), pid=\(item.process?.pid ?? 0), trusted=\(item.trusted), logFiles=\(item.process?.logFiles.count ?? 0)")
        }
    }
}

import Foundation
import XCTest
@testable import KitApp

final class Recorder {
    var opened: [URL] = []
    var revealed: [URL] = []
    var shownGame = 0
}

struct Reply: Encodable {
    var match: [String]
    var unless: [String]? = nil
    var stdout: [String] = []
    var stderr: String? = nil
    var exit: Int = 0
    var delay: Double = 0
    var hang: Bool = false
    var grandchild: Bool = false
    var files: [String: String]? = nil
}

func settingsJSON(game: String = "/games/vtmb", mode: String = "player", sound: String = "on") -> String {
    "{\"aspect\": null, \"display\": null, \"extra\": \"\", \"game\": \"\(game)\", \"intros\": \"play\", \"mode\": \"\(mode)\", \"sound\": \"\(sound)\", \"subtitles\": \"on\"}"
}

func step(_ name: String, n: Int, of: Int, fraction: Double?) -> String {
    var o: [String: Any] = ["step": name, "n": n, "of": of, "elapsed": 1.5]
    if let fraction { o["fraction"] = fraction }
    return String(decoding: try! JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]), as: UTF8.self)
}

@MainActor
final class Fake {
    static let script = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("fake_cli.py").path
    let dir: URL
    let recorder = Recorder()
    let model: KitModel
    var bundle: String { model.config.bundle }
    var work: String { model.config.work }

    init(installed: Bool = true, tools: Bool = true, replies: [Reply] = []) {
        dir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("kitapp-fake").appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let bundle = dir.appendingPathComponent(Names.appName).path
        if installed { try! FileManager.default.createDirectory(atPath: bundle, withIntermediateDirectories: true) }
        let config = KitConfig(
            launcher: Fake.script, kit: Fake.script, bundle: bundle, work: dir.appendingPathComponent("work").path,
            xcodeSelect: [KitConfig.python, "-I", Fake.script, "xcode-select"],
            environment: ["FAKE_DIR": dir.path]
        )
        let rec = recorder
        let shell = KitShell(open: { rec.opened.append($0) }, reveal: { rec.revealed.append($0) }, showGame: { rec.shownGame += 1 }, screens: { ["Studio Display", "LG UltraFine"] })
        model = KitModel(config: config, shell: shell)
        var all = replies
        if !tools { all.insert(Reply(match: ["xcode-select", "-p"], exit: 2), at: 0) }
        all.append(Reply(match: ["status"], stdout: ["state: ready", "subtitles: on", "aspect: default", "unofficial patch: off"]))
        all.append(Reply(match: ["set"], stdout: [settingsJSON()]))
        write(all)
    }

    deinit {
        try? FileManager.default.removeItem(at: dir)
    }

    func write(_ replies: [Reply]) {
        try! JSONEncoder().encode(replies).write(to: dir.appendingPathComponent("replies.json"))
    }

    var log: [[String: Any]] {
        guard let s = try? String(contentsOf: dir.appendingPathComponent("argv.log"), encoding: .utf8) else { return [] }
        return s.split(separator: "\n").compactMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any] }
    }

    var argvs: [[String]] { log.compactMap { $0["argv"] as? [String] } }

    func commands(_ name: String) -> [[String]] { argvs.filter { $0.first == name } }

    func release() { FileManager.default.createFile(atPath: dir.appendingPathComponent("release").path, contents: nil) }

    func clearLog() { try? FileManager.default.removeItem(at: dir.appendingPathComponent("argv.log")) }

    func waitFor(_ cond: @escaping () -> Bool, timeout: Double = 10) async {
        let end = Date().addingTimeInterval(timeout)
        while !cond() && Date() < end { try? await Task.sleep(for: .milliseconds(20)) }
        XCTAssertTrue(cond(), "condition not met in time")
    }

    func assertNoGameStarted() {
        for a in argvs {
            XCTAssertFalse(a.contains("--run"))
            XCTAssertFalse(a.contains("--alert"))
            XCTAssertFalse(a.contains { $0.contains("bloodlines-native") })
            if let i = a.firstIndex(of: "--unofficial-patch"), a[i + 1] == "on" { XCTAssertTrue(a.contains("--up-installer"), "\(a)") }
        }
    }
}

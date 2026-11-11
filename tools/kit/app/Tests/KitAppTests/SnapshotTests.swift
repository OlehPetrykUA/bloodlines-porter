import AppKit
import SwiftUI
import XCTest
@testable import KitApp

@MainActor
final class SnapshotTests: XCTestCase {
    static let out = URL(fileURLWithPath: ProcessInfo.processInfo.environment["KIT_SHOTS"] ?? "")
    nonisolated(unsafe) static var alive: [Fake] = []

    func model(installed: Bool = true, canBuild: Bool = true, status: String = "ready", _ edit: (KitModel) -> Void = { _ in }) -> KitModel {
        let f = Fake(installed: installed)
        Self.alive.append(f)
        var config = f.model.config
        config.canBuild = canBuild
        let m = KitModel(config: config)
        m.status = StatusInfo(lines: ["state: " + status, "subtitles: on", "aspect: default", "unofficial patch: off"])
        var s = Settings()
        s.game = "~/Steam/Bloodlines"
        m.settings = s
        m.checkLine = "game ok: patch 1.2, 1,204 files (38 s)"
        edit(m)
        return m
    }

    func render<V: View>(_ view: V, _ name: String) async throws {
        for (suffix, look) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            let host = NSHostingView(rootView: view)
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 400), styleMask: [.titled], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.title = Names.kitTitle
            window.appearance = NSAppearance(named: look)
            window.contentView = host
            try await Task.sleep(for: .milliseconds(150))
            let size = host.fittingSize
            window.setContentSize(size)
            host.frame = NSRect(origin: .zero, size: size)
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(150))
            guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { XCTFail(name); return }
            host.cacheDisplay(in: host.bounds, to: rep)
            let png = try XCTUnwrap(rep.representation(using: .png, properties: [:]))
            try png.write(to: Self.out.appendingPathComponent("\(name)-\(suffix).png"))
            window.close()
        }
    }

    func testRenderEveryState() async throws {
        try XCTSkipIf(ProcessInfo.processInfo.environment["KIT_SHOTS"] == nil, "set KIT_SHOTS to a folder to render")
        try FileManager.default.createDirectory(at: Self.out, withIntermediateDirectories: true)
        _ = NSApplication.shared
        var build = ProgressInfo()
        build.step = "recompile"; build.n = 3; build.of = 6; build.fraction = 0.41; build.elapsed = 312; build.lastLog = "  3.1s [last log line]"
        var link = ProgressInfo()
        link.step = "package"; link.n = 4; link.of = 6; link.elapsed = 1180; link.lastLog = "+ [last command line]"
        var verify = ProgressInfo()
        verify.step = "hash"; verify.done = 612; verify.total = 1204; verify.fraction = 0.51
        var up = ProgressInfo()
        up.step = "unofficial-patch"; up.n = 3; up.of = 8; up.fraction = 0.35
        up.logs = ["unofficial patch installer: VTMBup120.8.exe MD5 dd8951ce97fb61aaddec2ad73607d201 ok", "extracting VTMBup120.8.exe (this takes a few minutes)"]
        up.lastLog = up.logs[1]
        let md5 = "dd8951ce97fb61aaddec2ad73607d201"
        let shots: [(String, KitModel)] = [
            ("setup-1-no-game", model(status: "no-game") { $0.settings.game = ""; $0.checkLine = nil }),
            ("setup-2-folder-invalid", model(status: "check-failed") { $0.folderFailure = ("~/Steam/Bloodlines", "is not a complete, unmodified Bloodlines 1.2 copy", ["missing: Vampire/pack000.vpk", "changed: Bin/engine.dll", "unreadable: Bin/[file]"]) }),
            ("setup-3-verifying", model { $0.activity = .verifying; $0.progress = verify }),
            ("setup-4-needs-tools", model { $0.toolsMissing = true; $0.settings.game = ""; $0.checkLine = nil }),
            ("build-1-not-built", model(installed: false)),
            ("build-1b-needs-kit-folder", model(installed: false, canBuild: false)),
            ("build-2-stopped", model(installed: false) { $0.stopped = true }),
            ("build-3-building", model(installed: false) { $0.activity = .building; $0.progress = build }),
            ("build-4-building-no-fraction", model(installed: false) { $0.activity = .building; $0.progress = link; $0.noProgress = true }),
            ("build-5-failed", model(installed: false) { $0.buildFailure = (4, (1...20).map { "[build.log, line \($0) of the last 20]" }) }),
            ("play-1-ready", model()),
            ("play-2-personal", model { $0.status.personal = true }),
            ("play-3-running", model(status: "running")),
            ("play-4-last-run-failed", model(status: "last-run-failed") { $0.status.why = "exit 3 (bloodlines-native-2026-10-28-143012.ips); Collect logs offered" }),
            ("options-1-developer", model { $0.settings.mode = "developer"; $0.settings.sound = "off"; $0.settings.subtitles = "off"; $0.settings.intros = "skip"; $0.settings.display = 1; $0.settings.aspect = "21:9" }),
            ("options-2-set-error", model { $0.settings.mode = "developer"; $0.notes["sound"] = "[stderr of set --sound, verbatim]"; $0.extraDraft = "[the text as typed]"; $0.notes["extra"] = "invalid value for extra" }),
            ("patch-1-building", model(installed: false) { $0.activity = .building; $0.patchOn = true; $0.progress = up }),
            ("patch-2-two-installers", model { $0.upNote = "2 installers in the game folder: choose one" }),
            ("patch-3-chosen", model { $0.usePatchInstaller("~/Games/Vampire Bloodlines (Windows)/VTMBup120.8.exe") }),
            ("patch-4-on", model { $0.patchOn = true; $0.status.upLines = ["unofficial patch: on", "unofficial patch build: sha256 [sha]"] }),
            ("patch-5-failed", model(installed: false) { $0.patchOn = true; $0.buildFailure = (buildExitPatch, ["[kit.log, earlier lines]", "unofficial patch: unknown installer: MD5 [found] found, MD5 " + md5 + " expected (VTMBup120.8.exe is the build this kit knows)"]) }),
            ("play-5-installed", model { $0.justInstalled = true }),
            ("play-6-installed-patch", model { $0.justInstalled = true; $0.usePatchInstaller("~/Downloads/VTMBup120.8.exe") }),
            ("logs-1-collecting", model { $0.collecting = true; $0.footerNote = "Collecting logs…" }),
            ("logs-2-saved", model { $0.footerNote = "Saved VampireBloodlines-logs-20261028T143012Z.zip to the Desktop" }),
        ]
        for (name, m) in shots {
            try await render(ContentView(model: m), name)
        }
        let paths = ["~/Games/Vampire Bloodlines.app", "~/Library/Caches/Vampire Bloodlines", "~/Library/Application Support/Vampire Bloodlines/cache/dxvk"]
        let data = ["/files", "/registry.reg", "/logs", "/launcher.json", "/backups", "/game-check.json"].map { "~/Library/Application Support/Vampire Bloodlines" + $0 }
        try await render(UninstallView(model: model { $0.uninstall = UninstallSheet(paths: paths) }), "uninstall-1-box-off")
        try await render(UninstallView(model: model { $0.uninstall = UninstallSheet(paths: paths + data, removeData: true) }), "uninstall-2-box-on")
    }
}

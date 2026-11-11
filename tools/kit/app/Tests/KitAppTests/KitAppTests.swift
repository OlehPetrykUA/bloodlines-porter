import SwiftUI
import XCTest
@testable import KitApp

@MainActor
final class ArgvTests: XCTestCase {
    func testOpenRunsSessionStartOnceThenStatus() async {
        let f = Fake()
        await f.model.open()
        await f.model.refresh()
        XCTAssertEqual(f.argvs.first, ["xcode-select", "-p"])
        XCTAssertEqual(f.commands("session-start"), [["session-start", "--bundle", f.bundle]])
        XCTAssertEqual(f.commands("status").first, ["status", "--bundle", f.bundle])
        XCTAssertEqual(f.model.state, .ready)
        f.assertNoGameStarted()
    }

    func testEachControlRunsItsCommand() async {
        let f = Fake(replies: [
            Reply(match: ["check"], stdout: ["{\"log\": \"game ok: patch 1.2, 1204 files (38.0 s)\"}", "{\"result\": \"pass\", \"exit\": 0, \"message\": \"\"}"]),
            Reply(match: ["collect-logs"], stdout: ["/Users/x/Desktop/BloodlinesNative-logs-20261028T143012Z.zip"]),
        ])
        await f.model.open()
        let b = f.bundle
        let cases: [(String, () async -> Void, [String])] = [
            ("choose", { await f.model.choose(folder: "/games/vtmb") }, ["set", "--game", "/games/vtmb", "--bundle", b]),
            ("verify", { await f.model.verify() }, ["check", "--progress", "--bundle", b]),
            ("developer", { await f.model.set("mode", "developer") }, ["set", "--mode", "developer", "--bundle", b]),
            ("player", { await f.model.set("mode", "player") }, ["set", "--mode", "player", "--bundle", b]),
            ("sound off", { await f.model.set("sound", "off") }, ["set", "--sound", "off", "--bundle", b]),
            ("sound on", { await f.model.set("sound", "on") }, ["set", "--sound", "on", "--bundle", b]),
            ("intros skip", { await f.model.set("intros", "skip") }, ["set", "--intros", "skip", "--bundle", b]),
            ("intros play", { await f.model.set("intros", "play") }, ["set", "--intros", "play", "--bundle", b]),
            ("subtitles", { await f.model.set("subtitles", "off") }, ["set", "--subtitles", "off", "--bundle", b]),
            ("aspect", { await f.model.set("aspect", "21:9") }, ["set", "--aspect", "21:9", "--bundle", b]),
            ("display auto", { await f.model.set("display", "auto") }, ["set", "--display", "auto", "--bundle", b]),
            ("display 1", { await f.model.set("display", "1") }, ["set", "--display", "1", "--bundle", b]),
            ("extra", { await f.model.set("extra", "-dev +map x") }, ["set", "--extra", "-dev +map x", "--bundle", b]),
            ("collect", { await f.model.collectLogs() }, ["collect-logs", "--bundle", b]),
            ("play", { await f.model.play() }, ["run", "--bundle", b]),
            ("aspect default", { await f.model.setAspect("") }, ["set", "--aspect", "default", "--bundle", b]),
            ("aspect 16:9", { await f.model.setAspect("16:9") }, ["set", "--aspect", "16:9", "--bundle", b]),
            ("build", { await f.model.build() }, ["build", "--game", "/games/vtmb", "--work", f.work, "--app", b, "--unofficial-patch", "off", "--progress"]),
            ("build patched", { f.model.usePatchInstaller("/dl/VTMBup120.8.exe"); await f.model.build() }, ["build", "--game", "/games/vtmb", "--work", f.work, "--app", b, "--unofficial-patch", "on", "--up-installer", "/dl/VTMBup120.8.exe", "--progress"]),
            ("collect after build", { await f.model.collectLogs() }, ["collect-logs", "--work", f.work, "--bundle", b]),
        ]
        for (name, action, expected) in cases {
            f.clearLog()
            await action()
            XCTAssertTrue(f.argvs.contains(expected), "\(name): \(f.argvs)")
        }
        f.clearLog()
        await f.model.choose(folder: "/games/vtmb")
        XCTAssertEqual(f.argvs.first, ["check", "--game", "/games/vtmb", "--progress", "--bundle", b])
        f.assertNoGameStarted()
    }

    func testToolsControls() async {
        let f = Fake(tools: false)
        await f.model.open()
        XCTAssertEqual(f.model.state, .needsTools)
        await f.model.installTools()
        XCTAssertEqual(f.argvs.last, ["xcode-select", "--install"])
        XCTAssertTrue(f.commands("session-start").isEmpty)
        XCTAssertTrue(f.commands("status").isEmpty)
    }

    func testDisplayMenuListsScreensByLocalizedName() {
        let f = Fake()
        XCTAssertEqual(f.model.displayChoices.map(\.label), ["Automatic", "Studio Display", "LG UltraFine"])
        XCTAssertEqual(f.model.displayChoices.map(\.value), ["auto", "0", "1"])
    }

    func testEachPickerHasItsOwnMessage() {
        XCTAssertEqual(Set(Pick.allCases.map(\.message)).count, Pick.allCases.count)
        XCTAssertEqual(Pick.installer.message, pickInstallerPrompt)
        XCTAssertEqual(Pick.logFolder.message, "Choose a folder for the log bundle")
        XCTAssertTrue(Pick.gameFolder.message.contains("Bloodlines 1.2"))
        XCTAssertEqual(Pick.allCases.map(\.folders), [true, false, true])
    }

    func testCollectLogsShortcut() {
        XCTAssertEqual(KitCommands.collectShortcut.key, KeyEquivalent("l"))
        XCTAssertEqual(KitCommands.collectShortcut.modifiers, [.command, .shift])
    }
}

@MainActor
final class StateTests: XCTestCase {
    func state(status: String, installed: Bool = true) async -> (KitState, Fake) {
        let f = Fake(installed: installed, replies: [Reply(match: ["status"], stdout: [status, "subtitles: on"])])
        await f.model.open()
        return (f.model.state, f)
    }

    func testStatusLines() async {
        let cases: [(String, Bool, KitState)] = [
            ("state: no-game - pick the game folder", true, .noGame),
            ("state: check-failed - missing: Vampire/pack000.vpk; changed: Bin/engine.dll", true, .folderInvalid),
            ("state: running - Play brings the game to the front; Collect logs is on", true, .running),
            ("state: last-run-failed - exit 3 (bloodlines-native-2026.ips); Collect logs offered", true, .lastRunFailed),
            ("state: ready", true, .ready),
            ("state: ready", false, .notBuilt),
        ]
        for (line, installed, expected) in cases {
            let (s, f) = await state(status: line, installed: installed)
            XCTAssertEqual(s, expected, line)
            f.assertNoGameStarted()
        }
        let (_, f) = await state(status: "state: check-failed - missing: a; changed: b")
        XCTAssertEqual(f.model.status.why.components(separatedBy: "; "), ["missing: a", "changed: b"])
        let (_, g) = await state(status: "state: last-run-failed - exit 3; Collect logs offered")
        XCTAssertEqual(g.model.look.detail, "exit 3; Collect logs offered")
    }

    func testEnabledControlsPerState() {
        let all = Set(Control.allCases)
        let expected: [KitState: Set<Control>] = [
            .needsTools: [.install, .checkAgain],
            .building: [.cancel, .showLog, .collect],
            .verifying: [.cancel, .collect],
            .noGame: [.choose, .collect],
            .folderInvalid: [.choose, .verify, .collect],
            .notBuilt: [.choose, .verify, .build, .options, .collect],
            .buildFailed: [.showLog, .collect, .build, .options],
            .running: [.showGame, .collect],
            .lastRunFailed: [.play, .build, .choose, .verify, .options, .collect],
            .ready: [.choose, .verify, .build, .options, .play, .collect],
        ]
        for s in KitState.allCases {
            XCTAssertEqual(KitModel.controls(for: s), expected[s], s.rawValue)
            XCTAssertTrue(KitModel.controls(for: s).isSubset(of: all))
        }
        XCTAssertFalse(KitModel.controls(for: .noGame).contains(.play))
        XCTAssertTrue(KitModel.controls(for: .notBuilt).contains(.verify))
        XCTAssertTrue(KitModel.controls(for: .lastRunFailed).contains(.choose))
        XCTAssertFalse(KitModel.controls(for: .needsTools).contains(.collect))
        for s in KitState.allCases where s != .needsTools {
            XCTAssertTrue(KitModel.controls(for: s).contains(.collect), s.rawValue)
        }
    }

    func testCheckExits() async {
        let ok = Fake(replies: [Reply(match: ["check"], stdout: ["{\"log\": \"game ok: patch 1.2, 1204 files (38.0 s)\"}"])])
        await ok.model.open()
        await ok.model.verify()
        XCTAssertEqual(ok.model.state, .ready)
        XCTAssertEqual(ok.model.checkLine, "game ok: patch 1.2, 1204 files (38.0 s)")

        let bad = Fake(replies: [Reply(match: ["check"], stdout: ["{\"log\": \"game check failed: 2 of 1204 files\"}", "{\"log\": \"  missing: Vampire/pack000.vpk\"}", "{\"log\": \"  changed: Bin/engine.dll\"}", "{\"result\": \"fail\", \"exit\": 6, \"message\": \"\"}"], exit: 6)])
        await bad.model.open()
        await bad.model.choose(folder: "/other")
        XCTAssertEqual(bad.model.state, .folderInvalid)
        XCTAssertEqual(bad.model.folderFailure?.lines, ["missing: Vampire/pack000.vpk", "changed: Bin/engine.dll"])
        XCTAssertEqual(bad.model.folderFailure?.path, "/other")
        XCTAssertTrue(bad.commands("set").allSatisfy { !$0.contains("--game") }, "path not saved")
        XCTAssertEqual(bad.model.settings.game, "/games/vtmb")
    }

    func testBuildExits() async {
        for code in 0...7 {
            var replies = [Reply(match: ["build"], stdout: [step("recompile", n: 3, of: 5, fraction: 0.4),"{\"log\": \"line \(code)\"}"], exit: code)]
            if code == 5 { replies.append(Reply(match: ["xcode-select", "-p"], unless: [], exit: 0)) }
            let f = Fake(installed: false, replies: replies)
            await f.model.open()
            XCTAssertEqual(f.model.state, .notBuilt)
            if code == 5 { f.write([Reply(match: ["build"], exit: 5), Reply(match: ["xcode-select", "-p"], exit: 1), Reply(match: ["set"], stdout: [settingsJSON()]), Reply(match: ["status"], stdout: ["state: ready"])]) }
            await f.model.build()
            switch code {
            case 0: XCTAssertEqual(f.model.state, .notBuilt)
            case 5: XCTAssertEqual(f.model.state, .needsTools)
            case 6: XCTAssertEqual(f.model.state, .folderInvalid)
            default:
                XCTAssertEqual(f.model.state, .buildFailed)
                XCTAssertEqual(f.model.look.title, buildExitMessages[Int32(code)] ?? "Build failed")
                XCTAssertEqual(f.model.buildFailure?.lines.last, "line \(code)")
            }
            f.assertNoGameStarted()
        }
    }

    func testRunExits() async {
        let ok = Fake(replies: [Reply(match: ["run"], stdout: ["already running: nothing started"])])
        await ok.model.open()
        await ok.model.play()
        XCTAssertEqual(ok.recorder.shownGame, 1)
        XCTAssertEqual(ok.model.state, .ready)
        XCTAssertNil(ok.model.alert)

        let two = Fake(replies: [Reply(match: ["run"], stderr: "Unofficial Patch is on but not set up (state: not-set-up).", exit: 2)])
        await two.model.open()
        await two.model.play()
        XCTAssertEqual(two.model.alert?.title, "Play failed")
        XCTAssertEqual(two.model.alert?.message, "Unofficial Patch is on but not set up (state: not-set-up).")

        let gone = Fake(replies: [Reply(match: ["run"], stderr: "cannot start the game: [Errno 2] No such file", exit: 127)])
        await gone.model.open()
        await gone.model.play()
        XCTAssertEqual(gone.model.alert?.message, "cannot start the game: [Errno 2] No such file")

        let crash = Fake(replies: [Reply(match: ["run"], exit: 3)])
        await crash.model.open()
        crash.write([Reply(match: ["status"], stdout: ["state: last-run-failed - exit 3; Collect logs offered"]), Reply(match: ["set"], stdout: [settingsJSON()])])
        await crash.model.play()
        XCTAssertNil(crash.model.alert)
        XCTAssertEqual(crash.model.state, .lastRunFailed)
    }

    func testRunningWhilePlayIsAlive() async {
        let f = Fake(replies: [Reply(match: ["run"], hang: true)])
        await f.model.open()
        let t = Task { await f.model.play() }
        await f.waitFor { f.model.activity == .playing }
        XCTAssertEqual(f.model.state, .running)
        f.release()
        await t.value
    }

    func testFailureShowsAndOpensFreshBuildLog() async {
        let lines = (1...25).map { "build line \($0)" }.joined(separator: "\n") + "\n"
        let f = Fake(replies: [Reply(match: ["build"], exit: 4, files: ["work/build.log": lines, "work/kit.log": "kit line\n"])])
        await f.model.open()
        await f.model.build()
        XCTAssertEqual(f.model.buildFailure?.lines, (6...25).map { "build line \($0)" })
        XCTAssertEqual(f.model.look.detail, "Exit 4. The installed app, if any, is unchanged.\nThe last 20 lines of build.log:")
        f.model.showLog()
        XCTAssertEqual(f.recorder.opened, [URL(fileURLWithPath: f.work).appendingPathComponent("build.log")])
    }

    func testStaleBuildLogFallsBackToKitLog() async throws {
        let f = Fake(replies: [Reply(match: ["build"], exit: 4, files: ["work/kit.log": "kit 1\nkit 2\n"])])
        let work = URL(fileURLWithPath: f.work)
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        let old = work.appendingPathComponent("build.log")
        try Data("old build\n".utf8).write(to: old)
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(-3600)], ofItemAtPath: old.path)
        await f.model.open()
        await f.model.build()
        XCTAssertEqual(f.model.buildFailure?.lines, ["kit 1", "kit 2"])
        XCTAssertTrue(f.model.look.detail.hasSuffix("The last 20 lines of kit.log:"))
        f.model.showLog()
        XCTAssertEqual(f.recorder.opened, [work.appendingPathComponent("kit.log")])
    }

    func testSetErrorRevertsAndShowsStderr() async {
        let f = Fake(replies: [Reply(match: ["set", "--extra"], stderr: "invalid value for extra: '\"'", exit: 2), Reply(match: ["set", "--sound"], stderr: "bad sound", exit: 2)])
        await f.model.open()
        await f.model.set("sound", "off")
        XCTAssertEqual(f.model.settings.sound, "on")
        XCTAssertEqual(f.model.notes["sound"], "bad sound")
        f.model.extraDraft = "\""
        await f.model.set("extra", "\"")
        XCTAssertEqual(f.model.extraDraft, "\"")
        XCTAssertEqual(f.model.notes["extra"], "invalid value for extra: '\"'")
    }
}

@MainActor
final class ProgressTests: XCTestCase {
    func testFractionsAndAccessibilityValue() async {
        let f = Fake(replies: [Reply(match: ["build"], stdout: [step("doctor", n: 1, of: 5, fraction: 0.1), step("recompile", n: 3, of: 5, fraction: 0.5), step("install", n: 5, of: 5, fraction: 1.0)], delay: 0.3, hang: true)])
        await f.model.open()
        let t = Task { await f.model.build() }
        for (pct, name) in [(10, "Doctor"), (50, "Recompile"), (100, "Install")] {
            await f.waitFor { f.model.progress.percent == pct }
            XCTAssertEqual(f.model.progress.fraction, Double(pct) / 100)
            XCTAssertTrue(f.model.progress.accessibilityValue.hasPrefix(name))
            XCTAssertTrue(f.model.progress.accessibilityValue.hasSuffix("\(pct) percent"), f.model.progress.accessibilityValue)
        }
        XCTAssertEqual(f.model.progress.accessibilityValue, "Install, step 5 of 5, 100 percent")
        f.model.cancel()
        await t.value
    }

    func testNoFractionIsIndeterminate() {
        let f = Fake()
        f.model.consume(step("package", n: 4, of: 5, fraction: nil))
        XCTAssertNil(f.model.progress.fraction)
        XCTAssertEqual(f.model.progress.accessibilityValue, "Package, step 4 of 5")
    }

    func testFractionNeverGoesDown() {
        let f = Fake()
        f.model.consume(step("a", n: 1, of: 2, fraction: 0.6))
        f.model.consume(step("a", n: 1, of: 2, fraction: 0.4))
        XCTAssertEqual(f.model.progress.fraction, 0.6)
    }

    func testNoProgressNoticeAfter61s() {
        let f = Fake()
        f.model.activity = .building
        f.model.consume(step("recompile", n: 3, of: 5, fraction: nil))
        f.model.tick(now: Date().addingTimeInterval(30))
        XCTAssertFalse(f.model.noProgress)
        f.model.tick(now: Date().addingTimeInterval(61))
        XCTAssertTrue(f.model.noProgress)
        f.model.consume("{\"log\": \"x\"}")
        XCTAssertFalse(f.model.noProgress)
    }

    func testParse() {
        XCTAssertEqual(ProgressLine.parse("{\"result\": \"fail\", \"exit\": 4, \"message\": \"link\"}"), .result(pass: false, exit: 4, message: "link"))
        XCTAssertEqual(ProgressLine.parse("plain"), .log("plain"))
    }
}

@MainActor
final class CancelTests: XCTestCase {
    func testCancelSignalsTheWholeProcessGroup() async {
        let f = Fake(installed: false, replies: [Reply(match: ["build"], stdout: [step("recompile", n: 3, of: 5, fraction: 0.4)], hang: true, grandchild: true)])
        await f.model.open()
        XCTAssertEqual(f.model.state, .notBuilt)
        let t = Task { await f.model.build() }
        await f.waitFor { f.model.progress.fraction == 0.4 }
        XCTAssertEqual(f.model.state, .building)
        f.model.cancel()
        await t.value
        XCTAssertNotNil(f.log.first { $0["signal"] as? String == "SIGINT" && $0["who"] as? String == "child" })
        await f.waitFor { f.log.contains { $0["signal"] as? String == "SIGINT" && $0["who"] as? String == "grandchild" } }
        XCTAssertEqual(f.model.state, .notBuilt)
        XCTAssertEqual(f.model.look.title, "Build stopped")
        XCTAssertNil(f.model.buildFailure)
    }

    func testCancelAfterOtherCommandsStillStopsTheBuild() async {
        let f = Fake(installed: false, replies: [Reply(match: ["build"], stdout: [step("recompile", n: 3, of: 5, fraction: 0.4)], hang: true)])
        await f.model.open()
        let t = Task { await f.model.build() }
        await f.waitFor { f.model.progress.fraction == 0.4 }
        f.clearLog()
        await f.model.refresh()
        XCTAssertTrue(f.commands("status").isEmpty, "no refresh while building")
        _ = await f.model.runner.run(f.model.config.launcherArgv(["status"]))
        await f.model.collectLogs()
        f.model.cancel()
        let timeout = Task { try? await Task.sleep(for: .seconds(5)); f.release() }
        await t.value
        timeout.cancel()
        XCTAssertTrue(f.model.stopped, "Cancel reached the build")
        XCTAssertNotNil(f.log.first { $0["signal"] as? String == "SIGINT" })
    }

    func testInterruptedChooseShowsNoError() async {
        let f = Fake(replies: [Reply(match: ["check"], stdout: ["{\"log\": \"hashing\"}"], hang: true)])
        await f.model.open()
        let t = Task { await f.model.choose(folder: "/other") }
        await f.waitFor { f.model.progress.logs == ["hashing"] }
        f.model.cancel()
        await t.value
        XCTAssertNil(f.model.alert)
        XCTAssertNil(f.model.folderFailure)
        XCTAssertEqual(f.model.state, .ready)
        XCTAssertTrue(f.commands("set").allSatisfy { !$0.contains("--game") })
    }
}

@MainActor
final class ConcurrencyTests: XCTestCase {
    func testCommandsReturnWhileASilentRunIsAlive() async {
        let f = Fake(replies: [
            Reply(match: ["run"], hang: true),
            Reply(match: ["collect-logs"], stdout: ["/Users/x/Desktop/BloodlinesNative-logs-20261028T143012Z.zip"]),
        ])
        await f.model.open()
        let safety = Task { try? await Task.sleep(for: .seconds(8)); f.release() }
        defer { safety.cancel() }
        let play = Task { await f.model.play() }
        await f.waitFor { f.model.activity == .playing && !f.commands("run").isEmpty }
        f.clearLog()
        let started = Date()
        await f.model.collectLogs()
        let s = await f.model.runner.run(f.model.config.launcherArgv(["status"]))
        XCTAssertLessThan(Date().timeIntervalSince(started), 4, "status and collect-logs must not wait for run")
        XCTAssertEqual(s.stdout.first, "state: ready")
        XCTAssertEqual(f.model.footerNote, "Saved BloodlinesNative-logs-20261028T143012Z.zip to the Desktop")
        f.clearLog()
        await f.waitFor({ f.commands("status").count >= 1 }, timeout: 5)
        XCTAssertTrue(f.commands("set").isEmpty, "no settings read while playing")
        f.release()
        await play.value
    }
}

@MainActor
final class CollectLogsTests: XCTestCase {
    func testZipIsRevealedAndFooterSaysSaved() async {
        let f = Fake(replies: [Reply(match: ["collect-logs"], stdout: ["/Users/x/Desktop/BloodlinesNative-logs-20261028T143012Z.zip"])])
        await f.model.open()
        await f.model.collectLogs()
        XCTAssertEqual(f.recorder.revealed, [URL(fileURLWithPath: "/Users/x/Desktop/BloodlinesNative-logs-20261028T143012Z.zip")])
        XCTAssertEqual(f.model.footerNote, "Saved BloodlinesNative-logs-20261028T143012Z.zip to the Desktop")
        await f.model.set("sound", "off")
        XCTAssertNil(f.model.footerNote)
    }

    func testFailureAsksForFolderAndRetriesOnce() async {
        let f = Fake(replies: [Reply(match: ["collect-logs"], stderr: "Desktop is not writable", exit: 1)])
        await f.model.open()
        await f.model.collectLogs()
        XCTAssertEqual(f.model.alert?.message, "Desktop is not writable")
        XCTAssertEqual(f.model.alert?.retryFolder, true)
        f.clearLog()
        await f.model.collectLogs(outDir: "/tmp/out")
        XCTAssertEqual(f.argvs, [["collect-logs", "--out-dir", "/tmp/out", "--bundle", f.bundle]])
        XCTAssertEqual(f.model.alert?.retryFolder, false)
    }

    func testCollectOnWhileBuildingVerifyingAndSettingUp() async {
        let f = Fake()
        for a in [Activity.building, .verifying] {
            f.model.activity = a
            XCTAssertTrue(f.model.enabled(.collect))
        }
        f.model.activity = nil
        f.model.toolsMissing = true
        XCTAssertFalse(f.model.enabled(.collect))
    }
}

@MainActor
final class PatchTests: XCTestCase {
    func gameFolder(_ installers: [String]) throws -> String {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("Vampire Bloodlines (Windows)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        for n in installers + ["vampire.exe"] { FileManager.default.createFile(atPath: dir.appendingPathComponent(n).path, contents: Data("x".utf8)) }
        addTeardownBlock { try? FileManager.default.removeItem(at: dir.deletingLastPathComponent()) }
        return dir.path
    }

    func testOneInstallerTurnsThePatchOnWithoutAPicker() throws {
        let f = Fake()
        f.model.settings.game = try gameFolder(["VTMBup120.8.exe"])
        XCTAssertFalse(f.model.setPatch(true))
        XCTAssertTrue(f.model.patchOn)
        XCTAssertEqual(f.model.patchInstaller, f.model.settings.game + "/VTMBup120.8.exe")
        XCTAssertTrue(f.model.buildCommand.contains("--up-installer"))
        XCTAssertEqual(f.model.buildCommand[f.model.buildCommand.firstIndex(of: "--game")! + 1], f.model.settings.game)
    }

    func testNoInstallerAsksForOne() throws {
        let f = Fake()
        f.model.settings.game = try gameFolder([])
        XCTAssertTrue(f.model.setPatch(true))
        XCTAssertFalse(f.model.patchOn)
        f.model.usePatchInstaller("/Users/x/Downloads/VTMBup120.8.exe")
        XCTAssertTrue(f.model.patchOn)
        XCTAssertEqual(f.model.buildCommand.suffix(4), ["on", "--up-installer", "/Users/x/Downloads/VTMBup120.8.exe", "--progress"])
    }

    func testTwoInstallersAskWithANote() throws {
        let f = Fake()
        f.model.settings.game = try gameFolder(["VTMBup120.8.exe", "vtmbup11.exe"])
        XCTAssertTrue(f.model.setPatch(true))
        XCTAssertFalse(f.model.patchOn)
        XCTAssertEqual(f.model.upNote, "2 installers in the game folder: choose one")
    }

    func testOffDropsTheInstallerAndBuildsPlain() throws {
        let f = Fake()
        f.model.settings.game = try gameFolder(["VTMBup120.8.exe"])
        _ = f.model.setPatch(true)
        XCTAssertFalse(f.model.setPatch(false))
        XCTAssertNil(f.model.patchInstaller)
        XCTAssertFalse(f.model.buildCommand.contains("--up-installer"))
        XCTAssertTrue(f.model.buildCommand.contains("off"))
    }

    func testInstalledPatchedAppShowsTheSwitchOn() async {
        let f = Fake(replies: [Reply(match: ["status"], stdout: ["state: ready", "unofficial patch: on", "unofficial patch build: sha256 ab"])])
        await f.model.open()
        XCTAssertTrue(f.model.patchOn)
        XCTAssertFalse(f.model.buildCommand.contains("--up-installer"))
    }

    func testRefreshKeepsTheUsersPatchChoice() async {
        let f = Fake(replies: [Reply(match: ["status"], stdout: ["state: ready", "unofficial patch: on", "unofficial patch build: sha256 ab"])])
        await f.model.open()
        XCTAssertTrue(f.model.patchOn)
        XCTAssertFalse(f.model.setPatch(false))
        await f.model.refresh()
        XCTAssertFalse(f.model.patchOn)
        XCTAssertFalse(f.model.buildCommand.contains("--up-installer"))
    }

    func testAFailedMigrationShowsAnAlert() async {
        let f = Fake(replies: [Reply(match: ["status"], stderr: "could not move your saves", exit: 8)])
        await f.model.open()
        XCTAssertEqual(f.model.alert?.title, "Could not move your saves")
        XCTAssertEqual(f.model.alert?.message, "could not move your saves")
    }

    func testTheDiskAndTimeTextFollowsThePatchSwitch() {
        let f = Fake(installed: false)
        f.model.status = StatusInfo(lines: ["state: ready"])
        XCTAssertTrue(f.model.look.detail.contains("about 25 min and 3 GB"))
        f.model.usePatchInstaller("/x/VTMBup120.8.exe")
        XCTAssertTrue(f.model.look.detail.contains("about 45 min and 6 GB"))
    }

    func testInstallerVerifiedLineFromTheBuildLog() {
        XCTAssertEqual(installerVerified(["unofficial patch installer: VTMBup120.8.exe MD5 dd8951ce97fb61aaddec2ad73607d201 ok"]), "Installer verified: VTMBup120.8.exe, MD5 dd8951ce97fb61aaddec2ad73607d201")
    }
}

@MainActor
final class FinalScreenTests: XCTestCase {
    func testFinalRowsAreTheFourApprovedOnes() {
        let home = "/Users/x"
        XCTAssertEqual(finalRows(app: Names.app(home: home), game: "/Users/x/Steam/Bloodlines", installer: nil, home: home), [
            FinalRow(label: "App", paths: ["~/Games/Vampire Bloodlines.app"], note: "About 3 GB, game data inside"),
            FinalRow(label: "Saves, settings, logs", paths: ["~/Library/Application Support/Vampire Bloodlines"], note: ""),
            FinalRow(label: "Windows game folder", paths: ["~/Steam/Bloodlines"], note: "Left as it is"),
            FinalRow(label: "Build cache", paths: ["~/Library/Caches/Vampire Bloodlines"], note: "Deleted after install"),
        ])
        let patched = finalRows(app: Names.app(home: home), game: "/Volumes/A/Game", installer: "/Users/x/Downloads/VTMBup120.8.exe", home: home)
        XCTAssertEqual(patched[2], FinalRow(label: "Windows game folder and installer", paths: ["/Volumes/A/Game", "~/Downloads/VTMBup120.8.exe"], note: "Left as they are"))
    }

    func testSuccessfulBuildShowsInstalledWithPlay() async {
        let f = Fake(replies: [Reply(match: ["build"], stdout: ["{\"result\": \"pass\", \"exit\": 0, \"message\": \"installed\"}"])])
        await f.model.open()
        f.model.settings.game = "/games/vtmb"
        try? FileManager.default.createDirectory(atPath: f.bundle, withIntermediateDirectories: true)
        await f.model.build()
        XCTAssertTrue(f.model.justInstalled)
        XCTAssertEqual(f.model.state, .ready)
        XCTAssertEqual(f.model.look.title, "Installed")
        XCTAssertEqual(f.model.look.rows.map(\.label), ["App", "Saves, settings, logs", "Windows game folder", "Build cache"])
        XCTAssertTrue(KitModel.controls(for: .ready).contains(.play))
    }
}

final class NamesTests: XCTestCase {
    func testSwiftNamesMatchThePythonConstants() throws {
        let py = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../../../kitconst.py").standardized
        let text = try String(contentsOf: py, encoding: .utf8)
        for line in ["GAME_TITLE = \"\(Names.gameTitle)\"", "BUNDLE_ID = \"\(Names.bundleID)\"", "KIT_TITLE = \"\(Names.kitTitle)\"",
                     "SUPPORT_NAME = \"\(Names.supportName)\"", "CACHE_NAME = \"\(Names.cacheName)\"", "INSTALL_DIR = \"\(Names.installDir)\""] {
            XCTAssertTrue(text.contains(line), line)
        }
        XCTAssertEqual(Names.appName, "Vampire Bloodlines.app")
        XCTAssertEqual(Names.kitAppName, "Bloodlines Porter.app")
    }
}

@MainActor
final class UninstallTests: XCTestCase {
    let defaults = ["~/Games/Vampire Bloodlines.app", "~/Library/Caches/Vampire Bloodlines", "~/Library/Application Support/Vampire Bloodlines/cache/dxvk"]
    let data = ["~/Library/Application Support/Vampire Bloodlines/files", "~/Library/Application Support/Vampire Bloodlines/launcher.json"]

    func fake(yesExit: Int = 0) -> Fake {
        Fake(replies: [
            Reply(match: ["uninstall", "--dry-run", "--remove-data"], stdout: defaults + data),
            Reply(match: ["uninstall", "--dry-run"], stdout: defaults),
            Reply(match: ["uninstall", "--yes"], stderr: yesExit == 0 ? nil : "game is running: nothing removed", exit: yesExit),
        ])
    }

    func testBoxOffRunsYes() async {
        let f = fake()
        await f.model.open()
        f.clearLog()
        await f.model.startUninstall()
        XCTAssertEqual(f.argvs, [["uninstall", "--dry-run", "--bundle", f.bundle]])
        XCTAssertEqual(f.model.uninstall?.paths, defaults)
        XCTAssertEqual(f.model.uninstall?.removeData, false)
        f.clearLog()
        await f.model.confirmUninstall()
        XCTAssertEqual(f.commands("uninstall").last, ["uninstall", "--yes", "--bundle", f.bundle])
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.bundle))
        XCTAssertEqual(f.model.status.state, "ready")
        XCTAssertEqual(f.model.state, .notBuilt)
        XCTAssertEqual(f.model.settings.game, "/games/vtmb")
        XCTAssertTrue(f.commands("set").isEmpty, "no settings read right after uninstall")
    }

    func testRemoveDataGoesToFirstRun() async {
        let f = fake()
        await f.model.open()
        await f.model.startUninstall()
        await f.model.setRemoveData(true)
        await f.model.confirmUninstall()
        f.clearLog()
        await f.model.confirmUninstall(secondConfirmed: true)
        XCTAssertEqual(f.argvs.first, ["uninstall", "--yes", "--remove-data", "--bundle", f.bundle])
        XCTAssertEqual(f.model.state, .noGame)
        XCTAssertEqual(f.model.settings.game, "")
        XCTAssertTrue(f.commands("set").isEmpty)
    }

    func testWorkFolderPassedAfterABuild() async {
        let f = fake()
        await f.model.open()
        await f.model.build()
        f.clearLog()
        await f.model.startUninstall()
        await f.model.setRemoveData(true)
        await f.model.confirmUninstall()
        await f.model.confirmUninstall(secondConfirmed: true)
        let u = f.commands("uninstall")
        XCTAssertEqual(u.count, 3)
        for a in u { XCTAssertEqual(Array(a.suffix(4)), ["--work", f.work, "--bundle", f.bundle], "\(a)") }
    }

    func testDeclineRunsNothing() async {
        let f = fake()
        await f.model.open()
        await f.model.startUninstall()
        f.clearLog()
        f.model.declineUninstall()
        XCTAssertTrue(f.argvs.isEmpty)
        XCTAssertNil(f.model.uninstall)
    }

    func testBoxOnNeedsSecondConfirmation() async {
        let f = fake()
        await f.model.open()
        await f.model.startUninstall()
        await f.model.setRemoveData(true)
        XCTAssertEqual(f.commands("uninstall").last, ["uninstall", "--dry-run", "--remove-data", "--bundle", f.bundle])
        XCTAssertEqual(f.model.uninstall?.paths, defaults + data)
        f.clearLog()
        await f.model.confirmUninstall()
        XCTAssertEqual(f.model.uninstall?.confirmData, true)
        XCTAssertTrue(f.argvs.isEmpty, "no --yes before the second confirmation")
        f.model.declineUninstall()
        XCTAssertTrue(f.argvs.isEmpty)

        await f.model.startUninstall()
        await f.model.setRemoveData(true)
        await f.model.confirmUninstall()
        f.clearLog()
        await f.model.confirmUninstall(secondConfirmed: true)
        XCTAssertEqual(f.argvs.first, ["uninstall", "--yes", "--remove-data", "--bundle", f.bundle])
    }

    func testRefusedShowsStderr() async {
        let f = fake(yesExit: 1)
        await f.model.open()
        await f.model.startUninstall()
        await f.model.confirmUninstall()
        XCTAssertEqual(f.model.alert?.message, "game is running: nothing removed")
    }
}

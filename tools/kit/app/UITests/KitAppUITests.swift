import XCTest

final class KitAppUITests: XCTestCase {
    func fakeDir() throws -> String {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("kitapp-ui-fake").appendingPathComponent(UUID().uuidString).path
        try FileManager.default.createDirectory(atPath: dir + "/Vampire Bloodlines.app", withIntermediateDirectories: true)
        return dir
    }

    func app(fakeDir: String) -> XCUIApplication {
        let a = XCUIApplication()
        let here = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let fake = here.appendingPathComponent("Tests/fake_cli.py").path
        a.launchArguments = ["--cli", fake, "--kit", fake, "--bundle", fakeDir + "/Vampire Bloodlines.app", "--work", fakeDir + "/work"]
        a.launchEnvironment = ["FAKE_DIR": fakeDir]
        return a
    }

    func replies(_ dir: String, status: String) throws {
        let json = "[{\"match\": [\"status\"], \"stdout\": [\"state: \(status)\", \"unofficial patch: off\"]}, {\"match\": [\"set\"], \"stdout\": [\"{\\\"game\\\": \\\"/games/vtmb\\\", \\\"mode\\\": \\\"player\\\"}\"]}]"
        try json.write(toFile: dir + "/replies.json", atomically: true, encoding: .utf8)
    }

    func testAccessibilityAuditInEveryState() throws {
        let states = [("no-game - pick the game folder", "no-game"), ("check-failed - missing: a", "folder-invalid"), ("running - x", "running"), ("last-run-failed - exit 3", "last-run-failed"), ("ready", "ready")]
        for (status, id) in states {
            let dir = try fakeDir()
            try replies(dir, status: status)
            let a = app(fakeDir: dir)
            a.launch()
            XCTAssertTrue(a.otherElements[id].waitForExistence(timeout: 10), id)
            try a.performAccessibilityAudit()
            a.terminate()
        }
    }

    func testKeyboardOnlyPathToPlay() throws {
        let dir = try fakeDir()
        try replies(dir, status: "ready")
        let a = app(fakeDir: dir)
        a.launch()
        XCTAssertTrue(a.otherElements["ready"].waitForExistence(timeout: 10))
        for name in ["Choose…", "Verify", "Build", "Play", "Collect Logs"] {
            var reached = false
            for _ in 0..<40 {
                a.typeKey(.tab, modifierFlags: [])
                if (a.buttons[name].value(forKey: "hasKeyboardFocus") as? Bool) == true { reached = true; break }
            }
            XCTAssertTrue(reached, name)
        }
        a.typeKey("l", modifierFlags: [.command, .shift])
    }
}

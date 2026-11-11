import XCTest
@testable import KitApp

@MainActor
final class ResolutionTests: XCTestCase {
    var root: URL!

    override func setUp() {
        root = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("kitapp-resolve").appendingPathComponent(UUID().uuidString)
        try! FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
    }

    func touch(_ path: String) {
        let url = root.appendingPathComponent(path)
        try! FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: url.path, contents: Data())
    }

    func resolve(_ args: [String] = []) -> KitConfig {
        KitConfig.fromArguments(args, bundleURL: root.appendingPathComponent("porting-kit/Bloodlines Porter.app"), resourceURL: root.appendingPathComponent("porting-kit/Bloodlines Porter.app/Contents/Resources"))
    }

    func testLaunchArgumentWins() {
        touch("porting-kit/src/tools/kit/launcher.py")
        touch("porting-kit/src/tools/kit/kit.py")
        let c = resolve(["--cli", "/x/launcher.py", "--kit", "/x/kit.py"])
        XCTAssertEqual(c.launcher, "/x/launcher.py")
        XCTAssertEqual(c.kit, "/x/kit.py")
        XCTAssertTrue(c.canBuild)
    }

    func testKitPythonRunsIsolatedWithoutWritingBytecode() {
        let c = resolve(["--cli", "/x/launcher.py", "--kit", "/x/kit.py"])
        XCTAssertEqual(Array(c.launcherArgv(["status"]).prefix(4)), [KitConfig.python, "-I", "-B", "/x/launcher.py"])
        XCTAssertEqual(c.kitArgv(["build"]), [KitConfig.python, "-I", "-B", "/x/kit.py", "build"])
    }

    func testPortingKitFolderBesideTheApp() {
        touch("porting-kit/src/tools/kit/launcher.py")
        touch("porting-kit/src/tools/kit/kit.py")
        touch("porting-kit/Bloodlines Porter.app/Contents/Resources/kit/launcher.py")
        let c = resolve()
        let folder = root.appendingPathComponent("porting-kit/src/tools/kit").path
        XCTAssertEqual(c.launcher, folder + "/launcher.py")
        XCTAssertEqual(c.kit, folder + "/kit.py")
        XCTAssertTrue(c.canBuild)
    }

    func testKitFolderInsideTheAppWinsAndKeepsBuildEnabled() {
        touch("Games/Bloodlines Porter.app/Contents/Resources/porting-kit/src/tools/kit/launcher.py")
        touch("Games/Bloodlines Porter.app/Contents/Resources/porting-kit/src/tools/kit/kit.py")
        touch("Games/Bloodlines Porter.app/Contents/Resources/kit/launcher.py")
        let c = KitConfig.fromArguments([], bundleURL: root.appendingPathComponent("Games/Bloodlines Porter.app"), resourceURL: root.appendingPathComponent("Games/Bloodlines Porter.app/Contents/Resources"))
        let folder = root.appendingPathComponent("Games/Bloodlines Porter.app/Contents/Resources/porting-kit/src/tools/kit").path
        XCTAssertEqual(c.launcher, folder + "/launcher.py")
        XCTAssertEqual(c.kit, folder + "/kit.py")
        XCTAssertTrue(c.canBuild)
    }

    func testDefaultsComeFromTheNames() {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let c = resolve()
        XCTAssertEqual(c.bundle, home + "/Games/Vampire Bloodlines.app")
        XCTAssertEqual(c.work, home + "/Library/Caches/Vampire Bloodlines")
        XCTAssertEqual(Names.support(home: home), home + "/Library/Application Support/Vampire Bloodlines")
    }

    func testBundledCopyIsLauncherOnlyWithBuildDisabled() async {
        touch("porting-kit/Bloodlines Porter.app/Contents/Resources/kit/launcher.py")
        let c = resolve()
        XCTAssertEqual(c.launcher, root.appendingPathComponent("porting-kit/Bloodlines Porter.app/Contents/Resources/kit/launcher.py").path)
        XCTAssertFalse(c.canBuild)
        let m = KitModel(config: c)
        XCTAssertFalse(m.enabled(.build))
        XCTAssertEqual(m.buildNote, "Build needs the porting kit folder")
        await m.build()
        XCTAssertNil(m.activity)
        XCTAssertFalse(m.builtThisSession)
    }

    func testIncompleteKitFolderFallsBackToBundledCopy() {
        touch("porting-kit/src/tools/kit/launcher.py")
        let c = resolve()
        XCTAssertFalse(c.canBuild)
        XCTAssertTrue(c.launcher.contains("Contents/Resources/kit/launcher.py"))
    }

    func testOtherCommandsStayEnabledWithoutBuild() {
        let m = KitModel(config: resolve())
        for c in [Control.collect, .play, .verify, .choose] { XCTAssertTrue(m.enabled(c) == KitModel.controls(for: m.state).contains(c)) }
    }

    func testFullKitKeepsBuildEnabled() {
        let f = Fake(installed: false)
        XCTAssertTrue(f.model.config.canBuild)
        XCTAssertNil(f.model.buildNote)
        XCTAssertTrue(f.model.enabled(.build))
    }
}

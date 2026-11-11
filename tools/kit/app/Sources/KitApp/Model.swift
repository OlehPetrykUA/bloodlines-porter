import Foundation

public enum KitState: String, CaseIterable {
    case needsTools = "needs-command-line-tools"
    case building
    case verifying
    case noGame = "no-game"
    case folderInvalid = "folder-invalid"
    case notBuilt = "not-built"
    case buildFailed = "build-failed"
    case running
    case lastRunFailed = "last-run-failed"
    case ready
}

public enum Control: String, CaseIterable {
    case choose, verify, build, options, play, collect, cancel, showLog, showGame, install, checkAgain
}

public enum Activity: Equatable {
    case building, verifying, playing
}

public struct Settings: Equatable {
    public var game = ""
    public var mode = "player"
    public var sound = "on"
    public var intros = "play"
    public var subtitles = "on"
    public var aspect: String?
    public var display: Int?
    public var extra = ""

    public init() {}

    init?(json: String) {
        guard let d = json.data(using: .utf8), let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { return nil }
        game = o["game"] as? String ?? ""
        mode = o["mode"] as? String ?? mode
        sound = o["sound"] as? String ?? sound
        intros = o["intros"] as? String ?? intros
        subtitles = o["subtitles"] as? String ?? subtitles
        aspect = o["aspect"] as? String
        display = (o["display"] as? NSNumber)?.intValue
        extra = o["extra"] as? String ?? ""
    }
}

public struct StatusInfo: Equatable {
    public var state = ""
    public var why = ""
    public var upLines: [String] = []
    public var personal = false

    public init() {}

    public init(lines: [String]) {
        for l in lines {
            if l.hasPrefix("state: ") {
                let rest = String(l.dropFirst(7))
                if let r = rest.range(of: " - ") {
                    state = String(rest[..<r.lowerBound])
                    why = String(rest[r.upperBound...])
                } else {
                    state = rest
                }
            } else if l.hasPrefix("unofficial patch") {
                upLines.append(l)
            } else if l == "personal build (GPLv3 FFmpeg)" {
                personal = true
            }
        }
    }
}

public struct ProgressInfo: Equatable {
    public var step = ""
    public var n: Int?
    public var of: Int?
    public var done: Int?
    public var total: Int?
    public var fraction: Double?
    public var elapsed: Double?
    public var lastLog = ""
    public var logs: [String] = []
    public var lastLineAt = Date()

    public var percent: Int? { fraction.map { Int(($0 * 100).rounded()) } }

    public var accessibilityValue: String {
        var parts = [step.isEmpty ? "Working" : step.prefix(1).uppercased() + step.dropFirst()]
        if let n, let of { parts.append("step \(n) of \(of)") }
        if let percent { parts.append("\(percent) percent") }
        return parts.joined(separator: ", ")
    }
}

public struct AlertInfo: Identifiable, Equatable {
    public var id = UUID()
    public var title: String
    public var message: String
    public var retryFolder = false
}

public struct UninstallSheet: Equatable {
    public var paths: [String]
    public var removeData = false
    public var confirmData = false
}

public let noToolsMessage = Names.gameTitle + " needs the Xcode Command Line Tools. Run xcode-select --install in Terminal, then open the app again."
public let pickInstallerPrompt = "Choose the Unofficial Patch installer (VTMBup120.8.exe)"
public let noGameHint = "No game folder chosen yet. Choose your game folder first."

public enum Pick: CaseIterable {
    case gameFolder, installer, logFolder

    public var message: String {
        switch self {
        case .gameFolder: return "Choose the folder that holds your Bloodlines 1.2 files"
        case .installer: return pickInstallerPrompt
        case .logFolder: return "Choose a folder for the log bundle"
        }
    }

    public var folders: Bool { self != .installer }
}

public func installerVerified(_ logs: [String]) -> String? {
    let prefix = "unofficial patch installer: ", suffix = " ok"
    guard let l = logs.last(where: { $0.hasPrefix(prefix) && $0.hasSuffix(suffix) }) else { return nil }
    let body = l.dropFirst(prefix.count).dropLast(suffix.count)
    guard let r = body.range(of: " MD5 ") else { return nil }
    return "Installer verified: \(body[..<r.lowerBound]), MD5 \(body[r.upperBound...])"
}

public struct FinalRow: Equatable {
    public var label: String
    public var paths: [String]
    public var note: String
}

public func finalRows(app: String, game: String, installer: String?, home: String) -> [FinalRow] {
    let t = { Names.tilde($0, home: home) }
    return [
        FinalRow(label: "App", paths: [t(app)], note: "About 3 GB, game data inside"),
        FinalRow(label: "Saves, settings, logs", paths: [t(Names.support(home: home))], note: ""),
        installer.map { FinalRow(label: "Windows game folder and installer", paths: [t(game), t($0)], note: "Left as they are") }
            ?? FinalRow(label: "Windows game folder", paths: [t(game)], note: "Left as it is"),
        FinalRow(label: "Build cache", paths: [t(Names.work(home: home))], note: "Deleted after install"),
    ]
}

public let buildExitPatch: Int32 = 7

public let launcherExitMigrate: Int32 = 8

public let buildExitMessages: [Int32: String] = [
    1: "Build failed: a check failed",
    buildExitPatch: "Build failed: Unofficial Patch",
    3: "Build failed: disallowed instruction",
    4: "Build failed: link",
    5: "Build failed: doctor",
]

@MainActor
public final class KitModel: ObservableObject {
    public let config: KitConfig
    public let shell: KitShell
    public let runner: CLIRunner

    @Published public var status = StatusInfo()
    @Published public var settings = Settings()
    @Published public var toolsMissing = false
    @Published public var activity: Activity?
    @Published public var progress = ProgressInfo()
    @Published public var buildFailure: (code: Int32, lines: [String])?
    @Published public var folderFailure: (path: String, sentence: String, lines: [String])?
    @Published public var checkLine: String?
    @Published public var stopped = false
    @Published public var noProgress = false
    @Published public var alert: AlertInfo?
    @Published public var notes: [String: String] = [:]
    @Published public var upNote: String?
    @Published public var footerNote: String?
    @Published public var collecting = false
    @Published public var builtThisSession = false
    @Published public var uninstall: UninstallSheet?
    @Published public var extraDraft = ""
    @Published public var patchOn = false
    @Published public var patchInstaller: String?
    @Published public var justInstalled = false
    private var collectRetried = false
    private var buildStarted: Date?
    private var patchSeeded = false

    public init(config: KitConfig, shell: KitShell = .live) {
        self.config = config
        self.shell = shell
        self.runner = CLIRunner(environment: config.environment)
    }

    public var appInstalled: Bool { FileManager.default.fileExists(atPath: config.bundle) }

    public var state: KitState {
        if toolsMissing { return .needsTools }
        switch activity {
        case .building: return .building
        case .verifying: return .verifying
        default: break
        }
        if folderFailure != nil || status.state == "check-failed" { return .folderInvalid }
        if status.state == "no-game" { return .noGame }
        if buildFailure != nil { return .buildFailed }
        if activity == .playing || status.state == "running" { return .running }
        if status.state == "last-run-failed" { return .lastRunFailed }
        if !appInstalled { return .notBuilt }
        return .ready
    }

    public static func controls(for state: KitState) -> Set<Control> {
        switch state {
        case .needsTools: return [.install, .checkAgain]
        case .building: return [.cancel, .showLog, .collect]
        case .verifying: return [.cancel, .collect]
        case .noGame: return [.choose, .collect]
        case .folderInvalid: return [.choose, .verify, .collect]
        case .notBuilt: return [.choose, .verify, .build, .options, .collect]
        case .buildFailed: return [.showLog, .collect, .build, .options]
        case .running: return [.showGame, .collect]
        case .lastRunFailed: return [.play, .build, .choose, .verify, .options, .collect]
        case .ready: return [.choose, .verify, .build, .options, .play, .collect]
        }
    }

    public func enabled(_ c: Control) -> Bool {
        if c == .collect && collecting { return false }
        if c == .build && !config.canBuild { return false }
        return Self.controls(for: state).contains(c)
    }

    public var displayChoices: [(label: String, value: String)] {
        [("Automatic", "auto")] + shell.screens().enumerated().map { ($0.element, String($0.offset)) }
    }

    private func launcher(_ cmd: [String], onLine: @escaping @MainActor (String) -> Void = { _ in }) async -> CLIResult {
        await runner.run(config.launcherArgv(cmd), onLine: onLine)
    }

    private func streamed(_ argv: [String], as act: Activity) async -> CLIResult {
        activity = act
        progress = ProgressInfo()
        noProgress = false
        let r = await runner.run(argv, cancellable: true) { [weak self] line in self?.consume(line) }
        activity = nil
        return r
    }

    public func consume(_ line: String) {
        progress.lastLineAt = Date()
        noProgress = false
        switch ProgressLine.parse(line) {
        case let .step(name, n, of, done, total, fraction, elapsed):
            progress.step = name
            progress.n = n
            progress.of = of
            progress.done = done
            progress.total = total
            if let fraction { progress.fraction = max(fraction, progress.fraction ?? 0) } else { progress.fraction = nil }
            progress.elapsed = elapsed
        case let .log(text):
            progress.lastLog = text
            progress.logs.append(text)
            if progress.logs.count > 200 { progress.logs.removeFirst(progress.logs.count - 200) }
        case let .result(_, _, message):
            if !message.isEmpty { progress.logs.append(message); progress.lastLog = message }
        }
    }

    public func tick(now: Date = Date()) {
        if activity != nil && activity != .playing {
            noProgress = now.timeIntervalSince(progress.lastLineAt) >= 60
        }
    }

    public func open() async {
        await probeTools()
        if toolsMissing { return }
        _ = await launcher(["session-start"])
        await refresh()
    }

    public func probeTools() async {
        let r = await runner.run(config.xcodeSelect + ["-p"])
        toolsMissing = r.exit != 0
    }

    public func installTools() async {
        _ = await runner.run(config.xcodeSelect + ["--install"])
    }

    public func checkAgain() async {
        await probeTools()
        if !toolsMissing { await open() }
    }

    public func refresh(readSettings: Bool = true) async {
        if toolsMissing || (activity != nil && activity != .playing) { return }
        let s = await launcher(["status"])
        if s.exit == 0 {
            status = StatusInfo(lines: s.stdout)
            if !patchSeeded { patchOn = status.upLines.first == "unofficial patch: on"; patchSeeded = true }
        } else if s.exit == launcherExitMigrate && alert == nil {
            showError("Could not move your saves", s)
        }
        if readSettings && activity == nil {
            let e = await launcher(["set"])
            if e.exit == 0, let last = e.stdout.last, let parsed = Settings(json: last) { apply(parsed) }
        }
    }

    private func apply(_ s: Settings) {
        settings = s
        extraDraft = s.extra
    }

    private func clearResult() {
        footerNote = nil
        stopped = false
        justInstalled = false
    }

    private func failureLines(_ lines: [String]) -> [String] {
        lines.map { $0.trimmingCharacters(in: .whitespaces) }.filter { $0.hasPrefix("missing:") || $0.hasPrefix("changed:") || $0.hasPrefix("unreadable:") }
    }

    private func sentence(_ r: CLIResult) -> String {
        progress.logs.first { $0.hasPrefix("game check failed") || $0.contains("does not look like") || $0.contains("is not a complete") } ?? r.stderr
    }

    public func choose(folder: String) async {
        clearResult()
        let r = await streamed(config.launcherArgv(["check", "--game", folder, "--progress"]), as: .verifying)
        if r.interrupted { await refresh(); return }
        switch r.exit {
        case 0:
            folderFailure = nil
            checkLine = progress.logs.last { $0.hasPrefix("game ok") }
            let s = await launcher(["set", "--game", folder])
            if s.exit == 0, let last = s.stdout.last, let parsed = Settings(json: last) { apply(parsed) } else if s.exit != 0 { showError("Choose failed", s) }
        case 6:
            folderFailure = (folder, sentence(r), failureLines(progress.logs))
        default:
            showError("Verify failed", r)
        }
        await refresh()
    }

    public func verify() async {
        clearResult()
        let r = await streamed(config.launcherArgv(["check", "--progress"]), as: .verifying)
        if r.interrupted { await refresh(); return }
        switch r.exit {
        case 0:
            folderFailure = nil
            checkLine = progress.logs.last { $0.hasPrefix("game ok") }
        case 6:
            folderFailure = (settings.game, sentence(r), failureLines(progress.logs))
        case 2 where r.stderr.hasPrefix("no game folder"):
            break
        default:
            showError("Verify failed", r)
        }
        await refresh()
    }

    public var buildNote: String? {
        !config.canBuild && Self.controls(for: state).contains(.build) ? KitConfig.buildNeedsKitNote : nil
    }

    public func build() async {
        guard config.canBuild else { return }
        clearResult()
        buildFailure = nil
        builtThisSession = true
        buildStarted = Date()
        justInstalled = false
        let r = await streamed(config.kitArgv(buildCommand), as: .building)
        if r.interrupted {
            stopped = true
        } else {
            switch r.exit {
            case 0: justInstalled = true
            case 5:
                await probeTools()
                if !toolsMissing { buildFailure = (5, failureTail()) }
            case 6:
                folderFailure = (settings.game, sentence(r), failureLines(progress.logs))
            default:
                buildFailure = (r.exit, failureTail())
            }
        }
        await refresh()
    }

    public var buildCommand: [String] {
        ["build", "--game", settings.game, "--work", config.work, "--app", config.bundle, "--unofficial-patch", patchOn ? "on" : "off"]
            + (patchOn ? patchInstaller.map { ["--up-installer", $0] } ?? [] : []) + ["--progress"]
    }

    public func setPatch(_ on: Bool) -> Bool {
        upNote = nil
        patchSeeded = true
        guard on else { patchOn = false; patchInstaller = nil; return false }
        let found = Names.installers(in: settings.game)
        guard found.count == 1 else {
            if found.count > 1 { upNote = "\(found.count) installers in the game folder: choose one" }
            return true
        }
        usePatchInstaller(found[0])
        return false
    }

    public func usePatchInstaller(_ path: String) {
        patchSeeded = true
        patchInstaller = path
        patchOn = true
    }

    public var logFile: URL {
        let work = URL(fileURLWithPath: config.work)
        let build = work.appendingPathComponent("build.log")
        if let m = (try? FileManager.default.attributesOfItem(atPath: build.path))?[.modificationDate] as? Date, m >= (buildStarted ?? .distantPast) {
            return build
        }
        return work.appendingPathComponent("kit.log")
    }

    private func failureTail() -> [String] {
        guard let text = try? String(contentsOf: logFile, encoding: .utf8) else { return Array(progress.logs.suffix(20)) }
        return Array(text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init).reversed().drop { $0.isEmpty }.reversed().suffix(20))
    }

    public func cancel() {
        runner.interrupt()
    }

    public func showLog() {
        shell.open(logFile)
    }

    public func setAspect(_ tag: String) async {
        await set("aspect", tag.isEmpty ? "default" : tag)
    }

    public func play() async {
        clearResult()
        if status.state == "running" { shell.showGame(); return }
        activity = .playing
        let poll = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                if Task.isCancelled { break }
                await self?.refresh()
            }
        }
        let r = await launcher(["run"])
        poll.cancel()
        activity = nil
        if r.exit == 0 && r.stdout.contains("already running: nothing started") {
            shell.showGame()
        } else if r.exit == 2 {
            showError("Play failed", r)
        } else if r.exit == launcherExitMigrate {
            showError("Could not move your saves", r)
        } else if r.exit == 127 {
            showError("Play failed", r, fallback: "cannot start the game")
        }
        await refresh()
    }

    private func showError(_ title: String, _ r: CLIResult, fallback: String = "") {
        alert = AlertInfo(title: title, message: r.stderr.isEmpty ? fallback : r.stderr)
    }

    public func set(_ key: String, _ value: String) async {
        clearResult()
        let r = await launcher(["set", "--" + key, value])
        if r.exit == 0, let last = r.stdout.last, let parsed = Settings(json: last) {
            notes[key] = nil
            apply(parsed)
        } else {
            notes[key] = r.stderr
            if key != "extra" { let keep = extraDraft; apply(settings); extraDraft = keep }
        }
    }

    public func collectLogs(outDir: String? = nil) async {
        if outDir == nil { collectRetried = false }
        collecting = true
        footerNote = "Collecting logs…"
        var cmd = ["collect-logs"]
        if builtThisSession { cmd += ["--work", config.work] }
        if let outDir { cmd += ["--out-dir", outDir] }
        let r = await launcher(cmd)
        collecting = false
        if r.exit == 0, let zip = r.stdout.last(where: { !$0.isEmpty }) {
            let url = URL(fileURLWithPath: zip)
            shell.reveal(url)
            footerNote = "Saved \(url.lastPathComponent) to the " + (outDir.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "Desktop")
        } else {
            footerNote = nil
            alert = AlertInfo(title: "Couldn’t save the log bundle", message: r.stderr, retryFolder: outDir == nil && !collectRetried)
            if outDir != nil { collectRetried = true }
        }
    }

    private func uninstallCmd(_ flags: [String]) -> [String] {
        ["uninstall"] + flags + (builtThisSession ? ["--work", config.work] : [])
    }

    public func startUninstall() async {
        let r = await launcher(uninstallCmd(["--dry-run"]))
        if r.exit != 0 { showError("Uninstall failed", r); return }
        uninstall = UninstallSheet(paths: r.stdout.filter { !$0.isEmpty })
    }

    public func setRemoveData(_ on: Bool) async {
        let r = await launcher(uninstallCmd(on ? ["--dry-run", "--remove-data"] : ["--dry-run"]))
        if r.exit != 0 { uninstall = nil; showError("Uninstall failed", r); return }
        uninstall = UninstallSheet(paths: r.stdout.filter { !$0.isEmpty }, removeData: on)
    }

    public func confirmUninstall(secondConfirmed: Bool = false) async {
        guard let sheet = uninstall else { return }
        if sheet.removeData && !secondConfirmed {
            uninstall?.confirmData = true
            return
        }
        uninstall = nil
        let r = await launcher(uninstallCmd(sheet.removeData ? ["--yes", "--remove-data"] : ["--yes"]))
        if r.exit != 0 {
            showError("Uninstall failed", r)
        } else {
            folderFailure = nil
            buildFailure = nil
            checkLine = nil
            if sheet.removeData { apply(Settings()) }
        }
        await refresh(readSettings: false)
    }

    public func declineUninstall() {
        uninstall = nil
    }
}

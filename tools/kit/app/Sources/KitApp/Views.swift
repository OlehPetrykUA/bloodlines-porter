import SwiftUI
import UniformTypeIdentifiers

struct Look {
    var symbol: String
    var tint: Color
    var title: String
    var detail: String
    var rows: [FinalRow] = []
}

extension KitModel {
    var look: Look {
        let expected = patchOn ? "Expected: about 45 min and 6 GB of free disk." : "Expected: about 25 min and 3 GB of free disk."
        switch state {
        case .needsTools: return Look(symbol: "apple.terminal", tint: .orange, title: "Command Line Tools needed", detail: noToolsMessage)
        case .noGame: return Look(symbol: "folder", tint: .accentColor, title: "Choose your game folder", detail: "Pick the folder that holds your Bloodlines 1.2 files.\nPorter only reads it.")
        case .folderInvalid:
            let path = folderFailure?.path ?? settings.game
            let why = folderFailure?.sentence ?? "is not a complete, unmodified Bloodlines 1.2 copy"
            return Look(symbol: "exclamationmark.triangle", tint: .orange, title: "This folder failed the game check", detail: path + "\n" + why)
        case .verifying: return Look(symbol: "folder", tint: .accentColor, title: "Verifying the game folder", detail: "Checking every file against the Bloodlines 1.2 manifest.")
        case .notBuilt:
            if stopped { return Look(symbol: "shippingbox", tint: .accentColor, title: "Build stopped", detail: "Nothing was installed or changed.\n" + expected) }
            return Look(symbol: "shippingbox", tint: .accentColor, title: "Ready to build", detail: expected + "\nThe game folder is only read.")
        case .building:
            var d = "Step \(progress.n ?? 0) of \(progress.of ?? 0): \(progress.step)"
            if let e = progress.elapsed { d += " · " + Self.duration(e) }
            return Look(symbol: "shippingbox", tint: .accentColor, title: "Building", detail: d)
        case .buildFailed:
            let code = buildFailure?.code ?? 1
            return Look(symbol: "xmark.octagon", tint: .red, title: buildExitMessages[code] ?? "Build failed", detail: "Exit \(code). The installed app, if any, is unchanged.\nThe last 20 lines of \(logFile.lastPathComponent):")
        case .ready:
            if stopped { return Look(symbol: "shippingbox", tint: .accentColor, title: "Build stopped", detail: "Nothing was installed or changed.") }
            if justInstalled {
                return Look(symbol: "checkmark.circle", tint: .green, title: "Installed", detail: "", rows: finalRows(app: config.bundle, game: settings.game, installer: patchOn ? patchInstaller : nil, home: FileManager.default.homeDirectoryForCurrentUser.path))
            }
            return Look(symbol: "checkmark.circle", tint: .green, title: "Ready to play", detail: "Set the launch options, then Play.")
        case .running: return Look(symbol: "play.circle", tint: .accentColor, title: "Game running", detail: "Show Game brings it to the front.\nLaunch options unlock when the game quits.")
        case .lastRunFailed: return Look(symbol: "exclamationmark.triangle", tint: .orange, title: "Last run failed", detail: status.why)
        }
    }

    static func duration(_ s: Double) -> String {
        let t = Int(s)
        return t >= 60 ? "\(t / 60) min \(t % 60) s" : "\(t) s"
    }
}

struct ProgressBlock: View {
    let left: String
    let right: String
    let progress: ProgressInfo
    var showLog = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(left)
                Spacer()
                Text(right).foregroundStyle(.secondary).monospacedDigit()
            }
            Group {
                if let f = progress.fraction {
                    ProgressView(value: f, total: 1)
                } else {
                    ProgressView()
                }
            }
            .progressViewStyle(.linear)
            .accessibilityLabel(left)
            .accessibilityValue(progress.accessibilityValue)
            .accessibilityIdentifier("progress")
            if showLog && !progress.lastLog.isEmpty {
                Text(progress.lastLog).font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail)
            }
        }
    }
}

struct LinesBox: View {
    let lines: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(lines.enumerated()), id: \.offset) { Text($0.element).lineLimit(1).truncationMode(.tail) }
        }
        .font(.system(size: 11, design: .monospaced))
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8).padding(.horizontal, 12)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct FinalList: View {
    let rows: [FinalRow]
    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.offset) { i, r in
                if i > 0 { Divider() }
                HStack(alignment: .top, spacing: 12) {
                    Text(r.label).frame(width: 136, alignment: .leading).fixedSize(horizontal: false, vertical: true)
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(r.paths, id: \.self) { Text($0).font(.system(size: 12, design: .monospaced)).lineLimit(1).truncationMode(.middle) }
                        if !r.note.isEmpty { Text(r.note).font(.system(size: 11)).foregroundStyle(.secondary) }
                    }
                    .textSelection(.enabled)
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 8).padding(.horizontal, 12)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .textBackgroundColor).opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct Note: View {
    let text: String
    var symbol: String?
    var tint: Color = .secondary
    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            if let symbol { Image(systemName: symbol).foregroundStyle(tint).accessibilityHidden(true) }
            Text(text).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 11)).foregroundStyle(.secondary)
    }
}

public struct ContentView: View {
    @ObservedObject var model: KitModel
    @State private var pick = Pick.gameFolder
    @State private var picking = false

    public init(model: KitModel) { self.model = model }

    private func run(_ f: @escaping () async -> Void) { Task { await f() } }
    private func present(_ p: Pick) { pick = p; picking = true }

    private func picked(_ url: URL) {
        switch pick {
        case .gameFolder: run { await model.choose(folder: url.path) }
        case .installer: model.usePatchInstaller(url.path)
        case .logFolder: run { await model.collectLogs(outDir: url.path) }
        }
    }
    private func on(_ c: Control) -> Bool { model.enabled(c) }

    public var body: some View {
        let state = model.state
        VStack(spacing: 0) {
            Form {
                statusSection(state)
                gameSection(state)
                appSection(state)
                optionsSection(state)
            }
            .formStyle(.grouped)
            .scrollDisabled(true)
            Divider()
            footer(state)
        }
        .frame(width: 600)
        .fixedSize(horizontal: false, vertical: true)
        .background(Color(nsColor: .windowBackgroundColor))
        .fileImporter(isPresented: $picking, allowedContentTypes: pick.folders ? [.folder] : [.item]) { r in
            if case let .success(url) = r { picked(url) }
        }
        .fileDialogMessage(Text(pick.message))
        .task(id: model.activity) {
            guard let a = model.activity, a != .playing else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                model.tick()
            }
        }
        .alert(model.alert?.title ?? "", isPresented: Binding(get: { model.alert != nil }, set: { if !$0 { model.alert = nil } }), presenting: model.alert) { a in
            if a.retryFolder {
                Button("Cancel", role: .cancel) {}
                Button("Choose Folder…") { present(.logFolder) }.keyboardShortcut(.defaultAction)
            } else {
                Button("OK") {}.keyboardShortcut(.defaultAction)
            }
        } message: { a in
            Text(a.message)
        }
        .sheet(isPresented: Binding(get: { model.uninstall != nil }, set: { if !$0 { model.declineUninstall() } })) {
            UninstallView(model: model)
        }
    }

    @ViewBuilder private func statusSection(_ state: KitState) -> some View {
        let look = model.look
        Section {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: look.symbol).font(.system(size: 28)).foregroundStyle(look.tint).frame(width: 32).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(look.title).font(.system(size: 15, weight: .semibold))
                        if !look.detail.isEmpty {
                            Text(look.detail).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 0)
                    if state == .lastRunFailed {
                        Button("Collect Logs") { run { await model.collectLogs() } }.disabled(!on(.collect))
                    }
                }
                if !look.rows.isEmpty { FinalList(rows: look.rows) }
                switch state {
                case .verifying:
                    ProgressBlock(left: "Files checked", right: counts, progress: model.progress)
                case .building:
                    ProgressBlock(left: stepText, right: model.progress.percent.map { "\($0) %" } ?? "", progress: model.progress, showLog: true)
                case .folderInvalid:
                    let lines = model.folderFailure?.lines ?? model.status.why.components(separatedBy: "; ")
                    if !lines.isEmpty { LinesBox(lines: lines) }
                case .buildFailed:
                    LinesBox(lines: model.buildFailure?.lines ?? [])
                default:
                    EmptyView()
                }
            }
            .padding(.vertical, 4)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier(state.rawValue)
        }
    }

    private var counts: String {
        guard let d = model.progress.done, let t = model.progress.total else { return model.progress.percent.map { "\($0) %" } ?? "" }
        return "\(d.formatted()) of \(t.formatted())"
    }

    private var stepText: String {
        let p = model.progress
        let name = p.step.prefix(1).uppercased() + p.step.dropFirst()
        if let d = p.done, let t = p.total { return "\(name): \(d.formatted()) of \(t.formatted())" }
        return name
    }

    @ViewBuilder private func gameSection(_ state: KitState) -> some View {
        Section("Game") {
            LabeledContent("Game folder") {
                HStack {
                    if model.settings.game.isEmpty {
                        Text("None chosen").foregroundStyle(.secondary)
                    } else {
                        Text(model.settings.game).font(.system(size: 12, design: .monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    }
                    Spacer()
                    if state != .noGame && state != .folderInvalid {
                        Button("Choose…") { present(.gameFolder) }.disabled(!on(.choose))
                    }
                }
            }
            LabeledContent("Check") {
                HStack {
                    Text(checkText(state)).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Button(state == .folderInvalid ? "Verify Again" : "Verify") { run { await model.verify() } }.disabled(!on(.verify))
                }
            }
        }
    }

    private func checkText(_ state: KitState) -> String {
        if state == .verifying { return "Verifying…" }
        if state == .folderInvalid {
            let n = model.folderFailure?.lines.count ?? model.status.why.components(separatedBy: "; ").count
            return "Failed: \(n) files"
        }
        return model.checkLine ?? "Not verified"
    }

    static func appRowHasBuild(_ state: KitState) -> Bool {
        [.ready, .running, .lastRunFailed].contains(state)
    }

    @ViewBuilder private func appSection(_ state: KitState) -> some View {
        Section("App") {
            LabeledContent("App") {
                HStack {
                    if model.appInstalled {
                        Text((model.config.bundle as NSString).abbreviatingWithTildeInPath).font(.system(size: 12, design: .monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    } else {
                        Text("Not built yet").foregroundStyle(.secondary)
                    }
                    Spacer()
                    if Self.appRowHasBuild(state) {
                        Button("Build") { run { await model.build() } }.disabled(!on(.build))
                    }
                }
            }
            LabeledContent("Version") {
                Text("Porter " + (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")).foregroundStyle(.secondary)
            }
            if model.status.personal {
                LabeledContent("Personal build") {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Not for distribution").foregroundStyle(.primary)
                        Note(text: "status: personal build (GPLv3 FFmpeg)", symbol: "exclamationmark.triangle", tint: .orange)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func toggle(_ label: String, _ key: String, isOn: Bool, on onValue: String, off offValue: String) -> some View {
        Toggle(label, isOn: Binding(get: { isOn }, set: { v in run { await model.set(key, v ? onValue : offValue) } }))
            .toggleStyle(.switch)
    }

    @ViewBuilder private func optionsSection(_ state: KitState) -> some View {
        let off = !on(.options)
        let s = model.settings
        Section("Launch Options") {
            Group {
                Picker("Mode", selection: Binding(get: { s.mode }, set: { v in run { await model.set("mode", v) } })) {
                    Text("Player").tag("player")
                    Text("Developer").tag("developer")
                }
                .pickerStyle(.segmented)
                VStack(alignment: .leading, spacing: 4) {
                    toggle("Sound", "sound", isOn: s.sound == "on", on: "on", off: "off")
                    if let n = model.notes["sound"] { Note(text: n, symbol: "xmark.circle", tint: .red) }
                }
                VStack(alignment: .leading, spacing: 4) {
                    toggle("Subtitles", "subtitles", isOn: s.subtitles == "on", on: "on", off: "off")
                    Note(text: model.notes["subtitles"] ?? "Shows dialogue text (the game's own default is off).", symbol: model.notes["subtitles"] == nil ? nil : "xmark.circle", tint: .red)
                }
                toggle("Intro movies", "intros", isOn: s.intros == "play", on: "play", off: "skip")
                Picker("Display", selection: Binding(get: { s.display.map(String.init) ?? "auto" }, set: { v in run { await model.set("display", v) } })) {
                    ForEach(model.displayChoices, id: \.value) { c in
                        Text(c.label).tag(c.value)
                        if c.value == "auto" { Divider() }
                    }
                }
                .pickerStyle(.menu)
                VStack(alignment: .leading, spacing: 4) {
                    Picker("Aspect", selection: Binding(get: { s.aspect ?? "" }, set: { v in run { await model.setAspect(v) } })) {
                        Text("Default (4:3)").tag("")
                        Divider()
                        Text("16:9").tag("16:9")
                        Text("21:9").tag("21:9")
                        Text("Fit window").tag("fit")
                        Text("Engine 1280×1024").tag("engine")
                        if let a = s.aspect, !["16:9", "21:9", "fit", "engine"].contains(a) {
                            Text(a).tag(a)
                        }
                    }
                    .pickerStyle(.menu)
                    Note(text: model.notes["aspect"] ?? "Takes effect at the next launch.")
                }
                if s.mode == "developer" {
                    LabeledContent("Extra arguments") {
                        HStack(alignment: .center, spacing: 12) {
                            if let n = model.notes["extra"] { Note(text: n, symbol: "xmark.circle", tint: .red) }
                            Spacer(minLength: 0)
                            TextField("Extra arguments", text: $model.extraDraft, prompt: Text("passed after --"))
                                .labelsHidden()
                                .textFieldStyle(.roundedBorder)
                                .multilineTextAlignment(.leading)
                                .font(.system(size: 12, design: .monospaced))
                                .overlay {
                                    if model.notes["extra"] != nil { RoundedRectangle(cornerRadius: 6).stroke(.red, lineWidth: 1.5) }
                                }
                                .frame(width: 248)
                                .onSubmit { run { await model.set("extra", model.extraDraft) } }
                        }
                    }
                }
            }
            .foregroundStyle(off ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
            .disabled(off)
            patchRow(state, off: off)
        }
    }

    @ViewBuilder private func patchRow(_ state: KitState, off: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            let patchOff = off || state == .folderInvalid || state == .building
            Toggle("Unofficial Patch (applies on the next build)", isOn: Binding(get: { model.patchOn }, set: { v in
                if model.setPatch(v) { present(.installer) }
            }))
            .toggleStyle(.switch)
            .foregroundStyle(patchOff ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
            .disabled(patchOff)
            patchNotes(state)
        }
    }

    @ViewBuilder private func patchNotes(_ state: KitState) -> some View {
        if state == .noGame {
            Note(text: noGameHint)
        } else if state == .building, let v = installerVerified(model.progress.logs) {
            Note(text: v, symbol: "checkmark.circle", tint: .green)
        } else if let n = model.upNote {
            Note(text: n, symbol: "xmark.circle", tint: .red)
        } else if model.patchOn, let i = model.patchInstaller {
            Note(text: "Installer: " + (i as NSString).lastPathComponent)
        } else if state != .folderInvalid && !model.status.upLines.isEmpty && model.status.upLines != ["unofficial patch: off"] {
            Note(text: model.status.upLines.joined(separator: "\n"), symbol: "checkmark.circle", tint: .green)
        }
    }

    @ViewBuilder private func footer(_ state: KitState) -> some View {
        HStack(spacing: 8) {
            if state != .lastRunFailed {
                Button("Collect Logs") { run { await model.collectLogs() } }.disabled(!on(.collect))
            }
            if state == .building || state == .buildFailed {
                Button("Show Log") { model.showLog() }.disabled(!on(.showLog))
            }
            Spacer()
            if model.collecting { ProgressView().controlSize(.small).accessibilityHidden(true) }
            if model.noProgress {
                Text("No progress for 1 min").foregroundStyle(.secondary)
            } else if let n = model.footerNote ?? model.buildNote {
                Text(n).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
            footerRight(state)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
    }

    @ViewBuilder private func footerRight(_ state: KitState) -> some View {
        switch state {
        case .needsTools:
            Button("Check Again") { run { await model.checkAgain() } }
            Button("Install Command Line Tools") { run { await model.installTools() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
        case .noGame:
            Button("Choose Game Folder…") { present(.gameFolder) }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
        case .folderInvalid:
            Button("Choose Again…") { present(.gameFolder) }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
        case .verifying, .building:
            Button("Cancel") { model.cancel() }.keyboardShortcut(.cancelAction)
        case .notBuilt:
            Button("Build") { run { await model.build() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!on(.build))
        case .buildFailed:
            Button("Build Again") { run { await model.build() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!on(.build))
        case .running:
            Button("Show Game") { model.shell.showGame() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
        case .ready, .lastRunFailed:
            Button("Play") { run { await model.play() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!on(.play))
        }
    }
}

public struct UninstallView: View {
    @ObservedObject var model: KitModel
    public init(model: KitModel) { self.model = model }

    public var body: some View {
        let sheet = model.uninstall ?? UninstallSheet(paths: [])
        VStack(alignment: .leading, spacing: 12) {
            Text("Uninstall \(Names.gameTitle)?").font(.system(size: 13, weight: .semibold))
            Text("This removes:").font(.system(size: 11))
            VStack(alignment: .leading, spacing: 4) {
                ForEach(sheet.paths, id: \.self) { Text($0).lineLimit(1).truncationMode(.middle) }
            }
            .font(.system(size: 12, design: .monospaced))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8).padding(.horizontal, 12)
            .background(Color(nsColor: .quaternarySystemFill), in: RoundedRectangle(cornerRadius: 8))
            .accessibilityIdentifier("uninstall-list")
            Toggle("Also remove saves and settings", isOn: Binding(get: { sheet.removeData }, set: { v in Task { await model.setRemoveData(v) } }))
                .toggleStyle(.checkbox)
            Text((sheet.removeData ? "" : "Kept: saves, settings and logs.\n") + "Never touched: the game folder, \(Names.kitAppName), crash reports and log bundles.")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { model.declineUninstall() }.keyboardShortcut(.cancelAction)
                Button(role: .destructive) { Task { await model.confirmUninstall() } } label: { Text("Uninstall").foregroundStyle(.red) }
            }
        }
        .padding(20)
        .frame(width: 552)
        .background(Color(nsColor: .windowBackgroundColor))
        .alert("Delete saves and settings?", isPresented: Binding(get: { model.uninstall?.confirmData == true }, set: { if !$0 { model.uninstall?.confirmData = false } })) {
            Button("Cancel", role: .cancel) { model.declineUninstall() }
            Button("Delete", role: .destructive) { Task { await model.confirmUninstall(secondConfirmed: true) } }
        } message: {
            Text("Your saved games and save backups, settings and launch choices, logs and the game check record in ~/Library/Application Support/\(Names.supportName) are deleted. This can’t be undone.")
        }
    }
}

public struct KitCommands: Commands {
    @ObservedObject var model: KitModel
    public static let collectShortcut = KeyboardShortcut("l", modifiers: [.command, .shift])
    public init(model: KitModel) { self.model = model }

    public var body: some Commands {
        CommandGroup(after: .appInfo) {
            Button("Collect Logs") { Task { await model.collectLogs() } }
                .keyboardShortcut(Self.collectShortcut)
                .disabled(!model.enabled(.collect))
            Button("Uninstall…") { Task { await model.startUninstall() } }
        }
    }
}

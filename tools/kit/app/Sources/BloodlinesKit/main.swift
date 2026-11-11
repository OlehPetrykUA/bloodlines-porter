import AppKit
import KitApp
import SwiftUI

struct BloodlinesKitApp: App {
    @StateObject private var model = KitModel(config: .fromArguments(CommandLine.arguments))

    var body: some Scene {
        Window(Names.kitTitle, id: "main") {
            ContentView(model: model)
                .task { await model.open() }
                .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
                    Task { await model.refresh() }
                }
        }
        .windowResizability(.contentSize)
        .commands { KitCommands(model: model) }
    }
}

BloodlinesKitApp.main()

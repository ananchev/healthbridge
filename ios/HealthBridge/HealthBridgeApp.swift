// HealthBridgeApp.swift
// @main entry point. Minimal SwiftUI app, no third-party dependencies.
//
// NOTE for Claude Code: this is a compiling skeleton. Flesh out per ios/CLAUDE.md.
// Manual sync path first; background wiring later.

import SwiftUI

@main
struct HealthBridgeApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .task {
                    await model.requestAuthorizationIfNeeded()
                }
        }
    }
}

// Shared observable app state. Wire to HealthKitManager + SyncEngine.
@MainActor
final class AppModel: ObservableObject {
    @Published var lastSync: Date?
    @Published var lastError: String?
    @Published var sleepCount: Int = 0
    @Published var hrvCount: Int = 0
    @Published var rhrCount: Int = 0
    @Published var autoSyncEnabled: Bool = false

    // TODO(claude-code): inject HealthKitManager + SyncEngine + Settings.
    func requestAuthorizationIfNeeded() async {
        // TODO: call HealthKitManager.requestAuthorization()
    }

    func syncNow() async {
        // TODO: call SyncEngine.syncNow(); update published fields on result/error.
    }
}

// ContentView.swift
// Single-screen UI: status, endpoint/token config, sync button, auto-sync toggle.
// NOTE for Claude Code: skeleton — bind to AppModel, add config fields per ios/CLAUDE.md.

import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                Section("Status") {
                    LabeledContent("Last sync",
                        value: model.lastSync?.formatted() ?? "never")
                    LabeledContent("Sleep samples", value: "\(model.sleepCount)")
                    LabeledContent("HRV samples", value: "\(model.hrvCount)")
                    LabeledContent("RHR samples", value: "\(model.rhrCount)")
                    if let err = model.lastError {
                        Text(err).foregroundStyle(.red).font(.caption)
                    }
                }

                Section("Sync") {
                    Button("Sync now") {
                        Task { await model.syncNow() }
                    }
                    Toggle("Auto-sync on new samples", isOn: $model.autoSyncEnabled)
                }

                // TODO(claude-code): Section("Server") with endpoint URL field +
                // bearer token field (write to Keychain), and a
                // source picker that lists HealthKit sources writing sleep data.
            }
            .navigationTitle("HealthBridge")
        }
    }
}

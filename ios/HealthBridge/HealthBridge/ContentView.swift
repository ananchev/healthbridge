// ContentView.swift — status, server config, source picker, sync controls.

import SwiftUI

struct ContentView: View {
    @Environment(AppModel.self) private var model
    @State private var showSourcePicker = false
    @State private var isSyncing = false

    var body: some View {
        NavigationStack {
            Form {
                statusSection
                serverSection
                sourceSection
                syncSection
            }
            .navigationTitle("HealthBridge")
            .sheet(isPresented: $showSourcePicker) {
                SourcePickerView()
                    .environment(model)
            }
        }
    }

    // MARK: - Sections

    private var statusSection: some View {
        Section("Status") {
            LabeledContent("Last sync",
                value: model.lastSync.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? "never")
            LabeledContent("Sleep written", value: "\(model.sleepCount)")
            LabeledContent("HRV written",   value: "\(model.hrvCount)")
            LabeledContent("RHR written",   value: "\(model.rhrCount)")
            if let err = model.lastError {
                Text(err).foregroundStyle(.red).font(.caption)
            }
        }
    }

    private var serverSection: some View {
        Section("Server") {
            LabeledContent("Endpoint") {
                TextField("https://healthbridge.example.com",
                          text: Binding(
                            get: { model.settings.endpoint.absoluteString },
                            set: { if let u = URL(string: $0) { model.settings.endpoint = u } }
                          ))
                    .keyboardType(.URL)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
            }
            SecureField("Bearer token", text: Binding(
                get:  { model.settings.bearerToken ?? "" },
                set:  { model.settings.bearerToken = $0.isEmpty ? nil : $0 }
            ))
        }
    }

    private var sourceSection: some View {
        Section("Apple Watch source") {
            if let src = model.settings.sleepSourceName {
                LabeledContent("Selected", value: src)
            } else {
                Text("Not set — tap to choose").foregroundStyle(.secondary)
            }
            Button("Pick source…") { showSourcePicker = true }
        }
    }

    private var syncSection: some View {
        @Bindable var model = model
        return Section("Sync") {
            Button {
                isSyncing = true
                Task {
                    await model.syncNow()
                    isSyncing = false
                }
            } label: {
                HStack {
                    Text("Sync now")
                    Spacer()
                    if isSyncing { ProgressView() }
                }
            }
            .disabled(isSyncing)

            Toggle("Background sync", isOn: $model.autoSyncEnabled)
                .onChange(of: model.autoSyncEnabled) { _, enabled in
                    model.handleAutoSyncToggle(enabled)
                }
        }
    }
}

// MARK: - Source picker sheet

struct SourcePickerView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var sources: [String] = []
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if loading {
                    ProgressView("Loading sources…")
                } else if let err = error {
                    Text(err).foregroundStyle(.red).padding()
                } else {
                    List(sources, id: \.self) { src in
                        Button {
                            model.settings.sleepSourceName = src
                            dismiss()
                        } label: {
                            HStack {
                                Text(src)
                                Spacer()
                                if model.settings.sleepSourceName == src {
                                    Image(systemName: "checkmark").foregroundStyle(.blue)
                                }
                            }
                        }
                        .foregroundStyle(.primary)
                    }
                }
            }
            .navigationTitle("Sleep sources")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task {
                do {
                    sources = try await model.health.sourcesWritingSleep()
                } catch {
                    self.error = error.localizedDescription
                }
                loading = false
            }
        }
    }
}

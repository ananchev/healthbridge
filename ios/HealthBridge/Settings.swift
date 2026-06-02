// Settings.swift
// UserDefaults-backed config: endpoint, chosen sleep source, per-type sync cursors.
// The bearer token lives in Keychain, NOT here.
// NOTE for Claude Code: implement typed accessors.

import Foundation

final class Settings {
    private let defaults = UserDefaults.standard

    // TODO(claude-code):
    //  var endpoint: URL?
    //  var sleepSourceName: String?     // confirmed by user (handles NBSP)
    //  var sleepCursor / hrvCursor / rhrCursor: Date?
    //  bearer token via Keychain.
}

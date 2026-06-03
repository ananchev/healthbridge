// Settings.swift — UserDefaults-backed config. Bearer token lives in Keychain only.

import Foundation
import UIKit

final class Settings {
    static let shared = Settings()
    private let defaults = UserDefaults.standard

    private enum Key {
        static let endpoint       = "endpoint"
        static let sleepSource    = "sleepSource"
        static let sleepCursor    = "sleepCursor"
        static let hrvCursor      = "hrvCursor"
        static let rhrCursor      = "rhrCursor"
        static let bearerToken    = "bearerToken"   // Keychain key (not stored in defaults)
        static let deviceID       = "deviceID"
    }

    var endpoint: URL {
        get {
            if let s = defaults.string(forKey: Key.endpoint), let u = URL(string: s) { return u }
            return URL(string: "https://healthbridge.example.com")!
        }
        set { defaults.set(newValue.absoluteString, forKey: Key.endpoint) }
    }

    // Source name chosen by the user from the in-app picker.
    // Stored verbatim — may contain U+00A0 (non-breaking space) from Apple Watch source.
    var sleepSourceName: String? {
        get { defaults.string(forKey: Key.sleepSource) }
        set { defaults.set(newValue, forKey: Key.sleepSource) }
    }

    // Per-type cursors: advance only after confirmed 2xx.
    var sleepCursor: Date? {
        get { defaults.object(forKey: Key.sleepCursor) as? Date }
        set { defaults.set(newValue, forKey: Key.sleepCursor) }
    }
    var hrvCursor: Date? {
        get { defaults.object(forKey: Key.hrvCursor) as? Date }
        set { defaults.set(newValue, forKey: Key.hrvCursor) }
    }
    var rhrCursor: Date? {
        get { defaults.object(forKey: Key.rhrCursor) as? Date }
        set { defaults.set(newValue, forKey: Key.rhrCursor) }
    }

    // Bearer token stored in Keychain.
    var bearerToken: String? {
        get { Keychain.get(Key.bearerToken) }
        set {
            if let v = newValue { Keychain.set(v, for: Key.bearerToken) }
            else { Keychain.delete(Key.bearerToken) }
        }
    }

    // Stable device identifier for the ingest payload.
    var deviceID: String {
        if let existing = defaults.string(forKey: Key.deviceID) { return existing }
        let id = UIDevice.current.name
            .lowercased()
            .replacingOccurrences(of: " ", with: "-")
            .filter { $0.isLetter || $0.isNumber || $0 == "-" }
        defaults.set(id, forKey: Key.deviceID)
        return id
    }
}

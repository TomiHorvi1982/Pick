// Bluetooth MIDI for Pick: macOS keeps a pedal it once connected (FS-1-WL, Ampero Control…) but
// after a sleep or a restart often does not connect it again by itself — this asks it to. The
// server (/api/midi/system, /api/midi/bluetooth) builds it once with swiftc and runs it.
//
//   midi-bt list       JSON: the MIDI inputs macOS has now, and the Bluetooth MIDI devices it knows
//   midi-bt connect    the same, after asking for every known Bluetooth MIDI device again (≤ 6 s)
import CoreMIDI
import Foundation

func str(_ o: MIDIObjectRef, _ p: CFString) -> String {
    var n: Unmanaged<CFString>?
    if MIDIObjectGetStringProperty(o, p, &n) == noErr, let s = n?.takeRetainedValue() { return (s as String).trimmingCharacters(in: .whitespacesAndNewlines) }
    return ""
}
func offline(_ o: MIDIObjectRef) -> Bool { var v: Int32 = 0; MIDIObjectGetIntegerProperty(o, kMIDIPropertyOffline, &v); return v != 0 }
func sources() -> [String] { (0..<MIDIGetNumberOfSources()).map { str(MIDIGetSource($0), kMIDIPropertyDisplayName) } }
func bluetooth() -> [[String: Any]] {
    (0..<MIDIGetNumberOfDevices()).map { MIDIGetDevice($0) }
        .filter { str($0, kMIDIPropertyDriverOwner) == "com.apple.AppleMIDIBluetoothDriver" && !str($0, kMIDIPropertyName).isEmpty && str($0, kMIDIPropertyName) != "Bluetooth" }
        .map { ["name": str($0, kMIDIPropertyName), "online": !offline($0)] }
}

var client = MIDIClientRef()
MIDIClientCreateWithBlock("pick-midi-bt" as CFString, &client) { _ in }
var asked: Int32? = nil
if CommandLine.arguments.dropFirst().first == "connect" {
    let before = Set(sources())
    asked = MIDIBluetoothDriverActivateAllConnections()
    for _ in 0..<12 {                                    // what comes, comes within a few seconds
        RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        if Set(sources()).subtracting(before).count > 0 { RunLoop.current.run(until: Date().addingTimeInterval(0.5)); break }
    }
}
var out: [String: Any] = ["sources": sources(), "bluetooth": bluetooth()]
if let a = asked { out["asked"] = a }
let data = try! JSONSerialization.data(withJSONObject: out, options: [])
print(String(data: data, encoding: .utf8)!)

// OpenFlow flow widget: throwaway SwiftUI / NSPanel prototype for the Phase 5
// evaluation (docs/superpowers/specs/2026-10-02-swiftui-widget-eval.md).
//
// Scope: a non-activating NSPanel with the idle bar, the hover (Dictate) pill
// and the recording / silent / processing pill with the RMS waveform, at the
// same sizes as ui/flow_widget.py (WIDGET_SCALE 0.86). It speaks the subset of
// the widget_channel.py protocol the measurement needs: newline-delimited JSON
// over a Unix socket; daemon -> widget `state`, `level`, `config`, `exit`;
// widget -> daemon `start`, `confirm`, `cancel`.
//
// Not in scope: toasts, card, tooltip, menu, drag-to-dock, screen following.
//
// Bench hooks (stdout): `FIRST_FRAME <epoch>` on the first draw, `CONNECTED
// <epoch>` on connect, and frame-interval stats on exit.
//
// Build: ./build.sh   Run: .build/flow-widget-proto [--socket PATH] [--fps N] [--offscreen]

import AppKit
import SwiftUI

// MARK: - constants (mirrors ui/widget_geometry.py and ui/flow_widget.py)

let S: CGFloat = 0.86
let MARGIN: CGFloat = 16  // transparent margin around the shape, room for the shadow
let EDGE_INSET: CGFloat = 4
let BOTTOM_INSET: CGFloat = 10
let MORPH_S = 0.22
let DOT_SHAPE: [CGFloat] = [0.45, 0.7, 0.9, 1.0, 0.85, 0.65, 0.4]
let LEVEL_FLOOR_DB = -55.0
let LEVEL_CEIL_DB = -18.0

// (width, height) for vertical placement, rounded like SIZES in widget_geometry.py
func baseSize(_ view: String) -> CGSize {
    let base: (CGFloat, CGFloat)
    switch view {
    case "hover": base = (36, 56)
    case "recording", "silent", "processing": base = (26, 102)
    default: base = (8, 46)
    }
    return CGSize(width: (base.0 * S).rounded(), height: (base.1 * S).rounded())
}

func levelFromRMS(_ rms: Double) -> Double {
    if rms <= 0 { return 0 }
    let db = 20 * log10(rms)
    return max(0, min(1, (db - LEVEL_FLOOR_DB) / (LEVEL_CEIL_DB - LEVEL_FLOOR_DB)))
}

func epoch() -> Double { Date().timeIntervalSince1970 }

func say(_ s: String) {
    print(s)
    fflush(stdout)
}

// MARK: - theme

struct Theme {
    let surface: Color, text: Color, hairline: Color, secondaryBg: Color, secondaryText: Color
    let accent = Color(red: 0xE5 / 255, green: 0x40 / 255, blue: 0x2F / 255)
    static let paper = Theme(
        surface: Color(red: 0xFA / 255, green: 0xF7 / 255, blue: 0xF2 / 255),
        text: Color(red: 0x1A / 255, green: 0x18 / 255, blue: 0x14 / 255),
        hairline: Color(red: 0xE8 / 255, green: 0xE2 / 255, blue: 0xD9 / 255),
        secondaryBg: Color(red: 0xEF / 255, green: 0xEA / 255, blue: 0xE1 / 255),
        secondaryText: Color(red: 0x1A / 255, green: 0x18 / 255, blue: 0x14 / 255))
    static let ink = Theme(
        surface: Color(red: 0x1A / 255, green: 0x18 / 255, blue: 0x14 / 255),
        text: Color(red: 0xFA / 255, green: 0xF7 / 255, blue: 0xF2 / 255),
        hairline: Color(red: 0xFA / 255, green: 0xF7 / 255, blue: 0xF2 / 255).opacity(26.0 / 255),
        secondaryBg: Color(red: 0x3D / 255, green: 0x38 / 255, blue: 0x32 / 255),
        secondaryText: Color(red: 0xFA / 255, green: 0xF7 / 255, blue: 0xF2 / 255))
}

// MARK: - model

final class Model: ObservableObject {
    @Published var view = "idle"
    @Published var position = "right"
    @Published var dark = false
    @Published var visible = false
    // Read by the waveform on every frame; not @Published so a 20 Hz level
    // feed doesn't invalidate the view tree on its own.
    var level: Double = 0
    let t0 = CACurrentMediaTime()

    var vertical: Bool { position != "bottom" }
    var theme: Theme { dark ? .ink : .paper }
    var animating: Bool { visible && (view == "recording" || view == "processing") }

    func setLevel(_ rms: Double) {
        // Rise fast so a syllable shows at once; fall slowly so it reads.
        let target = levelFromRMS(rms)
        let k = target > level ? 0.7 : 0.25
        level += k * (target - level)
    }

    func shapeSize() -> CGSize {
        let s = baseSize(view)
        return vertical ? s : CGSize(width: s.height, height: s.width)
    }
}

// MARK: - frame stats (smoothness)

final class FrameStats {
    var firstFrameLogged = false
    var stamps: [CFTimeInterval] = []
    var draws = 0

    func frame(animating: Bool) {
        draws += 1
        if !firstFrameLogged {
            firstFrameLogged = true
            say("FIRST_FRAME \(epoch())")
        }
        if animating { stamps.append(CACurrentMediaTime()) }
    }

    func report() {
        guard stamps.count > 2 else {
            say("FRAMES draws=\(draws) animated=0")
            return
        }
        var iv: [Double] = []
        for i in 1..<stamps.count {
            let d = (stamps[i] - stamps[i - 1]) * 1000
            if d < 500 { iv.append(d) }  // gaps between recording phases aren't frames
        }
        iv.sort()
        func pct(_ p: Double) -> Double { iv[min(iv.count - 1, Int(Double(iv.count - 1) * p))] }
        let mean = iv.reduce(0, +) / Double(iv.count)
        say(String(format: "FRAMES draws=%d animated=%d mean_ms=%.2f p50_ms=%.2f p95_ms=%.2f p99_ms=%.2f max_ms=%.2f",
                   draws, iv.count, mean, pct(0.5), pct(0.95), pct(0.99), iv.last ?? 0))
    }
}

let stats = FrameStats()

// MARK: - views

struct WidgetView: View {
    @ObservedObject var model: Model
    let fps: Double
    let onClick: (String) -> Void

    var body: some View {
        let size = model.shapeSize()
        let th = model.theme
        ZStack {
            // The pill: SwiftUI animates the morph (220 ms, cubic-bezier(.2,.8,.2,1)).
            RoundedRectangle(cornerRadius: model.view == "idle" ? 4 * S : min(size.width, size.height) / 2,
                             style: .continuous)
                .fill(pillFill(th))
                .overlay(
                    RoundedRectangle(cornerRadius: model.view == "idle" ? 4 * S : min(size.width, size.height) / 2,
                                     style: .continuous)
                        .strokeBorder(model.view == "idle" || model.view == "hover" ? Color.clear : th.hairline,
                                      lineWidth: 1))
                .frame(width: size.width, height: size.height)
                .shadow(color: Color(red: 60 / 255, green: 40 / 255, blue: 25 / 255)
                            .opacity(model.view == "idle" ? 0 : 0.18), radius: 14, y: 5)
            // Contents: redrawn by the timeline only while something animates.
            TimelineView(.animation(minimumInterval: fps > 0 ? 1 / fps : nil, paused: !model.animating)) { tl in
                Canvas { ctx, csize in
                    stats.frame(animating: model.animating)
                    let t = tl.date.timeIntervalSinceReferenceDate
                    draw(ctx, csize, t)
                }
                .frame(width: size.width, height: size.height)
            }
            .allowsHitTesting(false)
        }
        .padding(MARGIN)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: anchor)
        .animation(.timingCurve(0.2, 0.8, 0.2, 1, duration: MORPH_S), value: model.view)
        .contentShape(Rectangle())
        .opacity(model.visible ? 1 : 0)
    }

    // The shape hugs the docked edge and grows inward, as in the PyQt widget.
    var anchor: Alignment {
        switch model.position {
        case "left": return .leading
        case "bottom": return .bottom
        default: return .trailing
        }
    }

    func pillFill(_ th: Theme) -> Color {
        switch model.view {
        case "idle", "hover": return th.accent
        default: return th.surface
        }
    }

    func draw(_ ctx: GraphicsContext, _ size: CGSize, _ t: Double) {
        let th = model.theme
        let r = CGRect(origin: .zero, size: size)
        switch model.view {
        case "hover": drawMic(ctx, r)
        case "recording", "silent", "processing": drawRecording(ctx, r, th, t)
        default: break  // idle: the pill is the whole handle
        }
    }

    func drawMic(_ ctx: GraphicsContext, _ r: CGRect) {
        var c = ctx
        let k = 20 * S / 24
        c.translateBy(x: r.midX, y: r.midY)
        c.scaleBy(x: k, y: k)
        c.translateBy(x: -12, y: -12)
        let white = Color(red: 250 / 255, green: 247 / 255, blue: 242 / 255)
        c.fill(Path(roundedRect: CGRect(x: 8, y: 2.5, width: 8, height: 13), cornerRadius: 4), with: .color(white))
        var arc = Path()
        arc.addArc(center: CGPoint(x: 12, y: 11), radius: 7.5, startAngle: .degrees(180), endAngle: .degrees(0),
                   clockwise: true)
        arc.move(to: CGPoint(x: 12, y: 18.5))
        arc.addLine(to: CGPoint(x: 12, y: 21.5))
        c.stroke(arc, with: .color(white), style: StrokeStyle(lineWidth: 2.2, lineCap: .round))
    }

    func buttonCenters(_ r: CGRect) -> (CGPoint, CGPoint) {
        let inset = min(r.width, r.height) / 2
        if model.vertical {
            return (CGPoint(x: r.midX, y: r.minY + inset), CGPoint(x: r.midX, y: r.maxY - inset))
        }
        return (CGPoint(x: r.minX + inset, y: r.midY), CGPoint(x: r.maxX - inset, y: r.midY))
    }

    func drawRecording(_ ctx: GraphicsContext, _ r: CGRect, _ th: Theme, _ t: Double) {
        let (x, ok) = buttonCenters(r)
        let g = S
        // ✕ cancel
        ctx.fill(Path(ellipseIn: CGRect(x: x.x - 10 * g, y: x.y - 10 * g, width: 20 * g, height: 20 * g)),
                 with: .color(th.secondaryBg))
        var xs = Path()
        xs.move(to: CGPoint(x: x.x - 3.5 * g, y: x.y - 3.5 * g)); xs.addLine(to: CGPoint(x: x.x + 3.5 * g, y: x.y + 3.5 * g))
        xs.move(to: CGPoint(x: x.x - 3.5 * g, y: x.y + 3.5 * g)); xs.addLine(to: CGPoint(x: x.x + 3.5 * g, y: x.y - 3.5 * g))
        ctx.stroke(xs, with: .color(th.secondaryText), style: StrokeStyle(lineWidth: 1.6 * g, lineCap: .round))
        // ✓ confirm
        ctx.fill(Path(ellipseIn: CGRect(x: ok.x - 10 * g, y: ok.y - 10 * g, width: 20 * g, height: 20 * g)),
                 with: .color(th.accent))
        var tick = Path()
        tick.move(to: CGPoint(x: ok.x - 4 * g, y: ok.y))
        tick.addLine(to: CGPoint(x: ok.x - 1 * g, y: ok.y + 3 * g))
        tick.addLine(to: CGPoint(x: ok.x + 4 * g, y: ok.y - 3 * g))
        ctx.stroke(tick, with: .color(Color(red: 250 / 255, green: 247 / 255, blue: 242 / 255)),
                   style: StrokeStyle(lineWidth: 1.8 * g, lineCap: .round, lineJoin: .round))
        // waveform: 7 dots, 2.5 thick, 5.5 pitch (× scale)
        let tt = CACurrentMediaTime() - model.t0
        let n = CGFloat(DOT_SHAPE.count)
        let thick = 2.5 * S, pitch = 5.5 * S
        let span = n * thick + (n - 1) * (pitch - thick)
        for (i, shape) in DOT_SHAPE.enumerated() {
            var length: CGFloat
            var alpha: Double
            switch model.view {
            case "recording":
                length = S * (3 + CGFloat(model.level) * 13 * shape * (0.8 + 0.2 * CGFloat(sin(tt * 16 + Double(i)))))
                alpha = 1
            case "silent":
                length = 3 * S; alpha = 0.3
            default:
                length = 3 * S
                alpha = 0.25 + 0.75 * max(0, sin(tt / 0.14 - Double(i) * 0.7))
            }
            let offset = -span / 2 + CGFloat(i) * pitch
            let dot = model.vertical
                ? CGRect(x: r.midX - length / 2, y: r.midY + offset, width: length, height: thick)
                : CGRect(x: r.midX + offset, y: r.midY - length / 2, width: thick, height: length)
            ctx.fill(Path(roundedRect: dot, cornerRadius: thick / 2), with: .color(th.text.opacity(alpha)))
        }
        _ = t
    }
}

// MARK: - panel

final class Panel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class HostView: NSHostingView<WidgetView> {
    var onHover: ((Bool) -> Void)?
    var onClickAt: ((NSPoint) -> Void)?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        for a in trackingAreas where a.owner === self { removeTrackingArea(a) }
        // .activeAlways: hover works while another app is active, which is
        // always the case for OpenFlow (no HoverRelay needed).
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.activeAlways, .mouseEnteredAndExited, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }

    override func mouseEntered(with event: NSEvent) { onHover?(true) }
    override func mouseExited(with event: NSEvent) { onHover?(false) }
    override func mouseUp(with event: NSEvent) { onClickAt?(convert(event.locationInWindow, from: nil)) }
}

// MARK: - channel (widget_channel.py protocol subset)

final class Channel {
    let path: String
    private var fd: Int32 = -1
    private let writeLock = NSLock()
    var onMessage: (([String: Any]) -> Void)?
    var onClose: (() -> Void)?

    init(path: String) { self.path = path }

    var connected: Bool { fd >= 0 }

    func connect() -> Bool {
        if fd >= 0 { return true }
        let s = socket(AF_UNIX, SOCK_STREAM, 0)
        if s < 0 { return false }
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8)
        guard bytes.count < MemoryLayout.size(ofValue: addr.sun_path) else { close(s); return false }
        withUnsafeMutableBytes(of: &addr.sun_path) { buf in
            for (i, b) in bytes.enumerated() { buf[i] = b }
            buf[bytes.count] = 0
        }
        let ok = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(s, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if ok != 0 { close(s); return false }
        var one: Int32 = 1
        setsockopt(s, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
        fd = s
        let t = Thread { [weak self] in self?.readLoop(s) }
        t.name = "widget-channel-read"
        t.start()
        return true
    }

    private func readLoop(_ s: Int32) {
        var buf = Data()
        var chunk = [UInt8](repeating: 0, count: 4096)
        while true {
            let n = read(s, &chunk, chunk.count)
            if n <= 0 { break }
            buf.append(chunk, count: n)
            while let nl = buf.firstIndex(of: 0x0A) {
                let line = buf.subdata(in: buf.startIndex..<nl)
                buf.removeSubrange(buf.startIndex...nl)
                guard !line.isEmpty,
                      let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
                DispatchQueue.main.async { self.onMessage?(obj) }
            }
        }
        DispatchQueue.main.async {
            if self.fd == s { close(s); self.fd = -1 }
            self.onClose?()
        }
    }

    func send(_ msg: [String: Any]) {
        guard fd >= 0, var data = try? JSONSerialization.data(withJSONObject: msg) else { return }
        data.append(0x0A)
        writeLock.lock(); defer { writeLock.unlock() }
        _ = data.withUnsafeBytes { write(fd, $0.baseAddress, data.count) }
    }
}

// MARK: - app

final class App: NSObject, NSApplicationDelegate {
    let model = Model()
    var panel: Panel!
    var host: HostView!
    let channel: Channel
    let fps: Double
    var offscreen = false
    var lostSince: CFTimeInterval? = CACurrentMediaTime()
    var state = "idle"

    init(socketPath: String, fps: Double) {
        channel = Channel(path: socketPath)
        self.fps = fps
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        let frame = NSRect(x: 0, y: 0, width: 120, height: 140)
        panel = Panel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false  // the pill draws its own soft shadow
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        host = HostView(rootView: WidgetView(model: model, fps: fps, onClick: { [weak self] in self?.channel.send(["action": $0]) }))
        host.onHover = { [weak self] on in self?.setHover(on) }
        host.onClickAt = { [weak self] p in self?.click(at: p) }
        panel.contentView = host

        channel.onMessage = { [weak self] m in self?.dispatch(m) }
        channel.onClose = { [weak self] in
            self?.lostSince = CACurrentMediaTime()
            self?.model.visible = false
            self?.panel.orderOut(nil)
        }
        Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tryConnect() }
        tryConnect()
    }

    func tryConnect() {
        if channel.connected { return }
        if channel.connect() {
            say("CONNECTED \(epoch())")
            lostSince = nil
            return
        }
        if let since = lostSince, CACurrentMediaTime() - since > 30 { NSApp.terminate(nil) }
    }

    func dispatch(_ m: [String: Any]) {
        switch m["type"] as? String {
        case "level":
            model.setLevel((m["rms"] as? Double) ?? Double((m["rms"] as? Int) ?? 0))
        case "config":
            if let p = m["position"] as? String, ["left", "right", "bottom"].contains(p) { model.position = p }
            if let a = m["appearance"] as? String {
                model.dark = a == "ink" || (a == "system" && NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua)
            }
            show()
        case "state":
            state = (m["state"] as? String) ?? "idle"
            let view = ["recording", "silent", "processing"].contains(state) ? state : "idle"
            if !(model.view == "hover" && view == "idle") { model.view = view }
            if view != "recording" { model.level = 0 }
            show()
        case "exit":
            stats.report()
            NSApp.terminate(nil)
        default:
            break
        }
    }

    func show() {
        layoutPanel()
        model.visible = true
        panel.orderFrontRegardless()
    }

    // The window is sized for the biggest view and only moves when the
    // position changes, so a morph is pure SwiftUI animation inside a fixed
    // window (no window resizes mid-animation).
    func layoutPanel() {
        guard var screen = NSScreen.main?.visibleFrame else { return }
        if offscreen {
            // Bench mode: lay out against a fake display far off every real
            // one, so nothing ever shows on the user's screen.
            screen = NSRect(x: -30000, y: -30000, width: 1440, height: 900)
        }
        let big = baseSize("hover")
        let rec = baseSize("recording")
        let w = max(big.width, rec.width) + 2 * MARGIN
        let h = max(big.height, rec.height) + 2 * MARGIN
        let size = model.vertical ? CGSize(width: w, height: h) : CGSize(width: h, height: w)
        let origin: CGPoint
        switch model.position {
        case "left":
            origin = CGPoint(x: screen.minX + EDGE_INSET - MARGIN, y: screen.midY - size.height / 2)
        case "bottom":
            origin = CGPoint(x: screen.midX - size.width / 2, y: screen.minY + BOTTOM_INSET - MARGIN)
        default:
            origin = CGPoint(x: screen.maxX - EDGE_INSET - size.width + MARGIN, y: screen.midY - size.height / 2)
        }
        panel.setFrame(NSRect(origin: origin, size: size), display: false)
    }

    func setHover(_ on: Bool) {
        if on && model.view == "idle" && state == "idle" {
            model.view = "hover"
        } else if !on && model.view == "hover" {
            model.view = "idle"
        }
    }

    func click(at p: NSPoint) {
        switch model.view {
        case "hover": channel.send(["action": "start"])
        case "recording", "silent", "processing":
            // top half (vertical) = ✕, bottom half = ✓; good enough for a prototype
            let topHalf = model.vertical ? p.y > host.bounds.midY : p.x < host.bounds.midX
            channel.send(["action": topHalf ? "cancel" : (model.view == "processing" ? "cancel" : "confirm")])
        default: break
        }
    }
}

// MARK: - main

var socketPath = (NSHomeDirectory() as NSString).appendingPathComponent(".openflow/widget.sock")
if let home = ProcessInfo.processInfo.environment["HOME"] {
    socketPath = (home as NSString).appendingPathComponent(".openflow/widget.sock")
}
var fps = 30.0
var offscreen = false
var args = CommandLine.arguments.dropFirst().makeIterator()
while let a = args.next() {
    switch a {
    case "--socket": if let v = args.next() { socketPath = v }
    case "--fps": if let v = args.next(), let d = Double(v) { fps = d }  // 0 = display rate
    case "--offscreen": offscreen = true
    default: break
    }
}

let nsapp = NSApplication.shared
nsapp.setActivationPolicy(.accessory)  // no Dock icon, never activates
let delegate = App(socketPath: socketPath, fps: fps)
delegate.offscreen = offscreen
nsapp.delegate = delegate
nsapp.run()

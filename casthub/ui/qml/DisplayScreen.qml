// One Display-mode screen (design pages 12-13): idle, connecting, streaming, busy toast.
// casthub/ui/app.py creates one per output and sets these properties; it never blocks
// on the receiver (SPEC: ui renders state per screen).
import QtQuick
import "uikit"

Rectangle {
    id: root

    // ---- state from the hub ---------------------------------------------------
    property string screenState: "idle"          // idle | connecting | streaming
    property string receiverName: ""            // "" until the hub says Windows can find it: "Loading"
    property string deviceName: ""          // sender, while connecting/streaming
    property string busyText: ""            // non-empty: show the busy toast
    property string pinCode: ""             // Wi-Fi Direct pairing: the PIN to type on the PC
    property string location: ""
    property string coordinates: ""
    property bool airplay: true
    property string qrPayload: ""           // hotspot join code; empty = hotspot off (D13)
    property string hotspotName: ""         // from the join code: for laptops, which can't scan it
    property string hotspotPassword: ""
    property string route: ""               // network | hotspot | hotspot-alone; "" while the hub decides
    readonly property bool hotspotOn: qrPayload !== "" && route.indexOf("hotspot") === 0
    property string theme: "auto"           // auto | light | dark
    // Passthrough (M10): casts go to this TV, not our screens ("" = off). Then this screen
    // stays on the page and says where the cast is.
    property string tvName: ""
    property var passthrough: ({ state: "off", tv: "", why: "", tvs: [], locked: false, allowed: true })
    property bool searching: false
    readonly property bool onTv: tvName !== "" && screenState === "streaming"
    // Passthrough on its way (the leave page closes once a TV is picked): say so here, next to
    // the ways to connect, so a phone can be added straight away. Not on the TV's own screen.
    readonly property string tvStatus: {
        var p = passthrough || {}
        if (tvCard || !p.tv) return ""
        if (p.state === "searching" || p.state === "connecting") return dotted("Connecting to " + p.tv)
        if (p.state === "failed") return "Couldn't reach " + p.tv
        return ""
    }

    // ---- leave page (Ctrl+Alt+Esc), drawn on top of this screen ----------------
    property bool leaveOpen: false
    // Passthrough (M10): this screen is drawn for the TV (media/tvcard.py) between casts. The
    // TV gets the clock, the name and the status with its dots; the ways to connect, the QR
    // and the Log out / Close button stay on Cast Hub's own screens (owner, Nov 9).
    property bool tvCard: false
    property bool panel: false              // inside Workstation/Console (casthub-display): "Menu" opens a short leave page
    signal leaveChosen(string action)
    signal leaveCancelled()

    // ---- clock ----------------------------------------------------------------
    property var fixedNow: null             // preview harness only
    property date now: new Date()
    Timer { interval: 1000; running: root.fixedNow === null; repeat: true; onTriggered: root.now = new Date() }
    readonly property date clockNow: fixedNow !== null ? fixedNow : now

    // ---- loading dots: four periods, a purple one sweeps along them, then a beat in the
    // text colour, so a wait never looks like a freeze ---------------------------------
    readonly property bool loading: receiverName === "" || screenState === "connecting"
    property int dotStep: 0                 // 0-3: that period is purple; 4: none
    Timer { interval: 220; running: root.loading && !root.tvCard; repeat: true; onTriggered: root.dotStep = (root.dotStep + 1) % 5 }
    function dotted(base, spaced) {
        var out = base
        for (var i = 0; i < 4; i++)         // spaced: thin spaces before each dot, the display style's tight tracking joins them
            out += (spaced ? "\u2009" : "") + (i === root.dotStep ? "<font color=\"" + t.accent + "\">.</font>" : ".")
        return out
    }

    readonly property bool dark: theme === "dark" || theme === "auto" && (clockNow.getHours() < 7 || clockNow.getHours() >= 19)

    // ---- frame (tokens [display]: 1080 x 633) ---------------------------------
    readonly property real ux: width / t.displayWidth
    readonly property real uy: height / t.displayHeight
    readonly property real u: Math.min(ux, uy)
    // Grid and top bar use the main frame (1080 x 675, as the login and lock screens do), so
    // nothing jumps when one screen hands over to the next. Heights below the bar use uy.
    readonly property real fu: Math.min(width / t.frameWidth, height / t.frameHeight)
    readonly property real margin: t.gridMargin * fu
    readonly property real gutter: t.gridGutter * fu
    readonly property real col: (width - 2 * margin - (t.gridColumns - 1) * gutter) / t.gridColumns
    function colX(i) { return margin + i * (col + gutter) }
    readonly property real hair: Math.max(1, Math.round(u))

    Tokens { id: t; dark: root.dark }
    color: root.screenState === "streaming" && !root.onTv ? "black" : t.bg
    Behavior on color { ColorAnimation { duration: t.motionNormal } }

    function pad(n) { return n < 10 ? "0" + n : "" + n }
    readonly property var days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    readonly property var months: ["January", "February", "March", "April", "May", "June", "July",
                                   "August", "September", "October", "November", "December"]

    // ---- idle + connecting ----------------------------------------------------
    Item {
        id: page
        anchors.fill: parent
        visible: root.screenState !== "streaming" || root.onTv

        GridLines { u: root.fu; tok: t; margin: root.margin; gutter: root.gutter }

        DisplayBar {
            u: root.fu; tok: t
            margin: root.margin; modeX: root.colX(4)
            dateText: root.days[root.clockNow.getDay()] + " " + root.clockNow.getDate() + " " + root.months[root.clockNow.getMonth()]
            showLogout: !root.tvCard
            logoutText: root.panel ? "Menu" : "Log out"
            onLogoutRequested: root.leaveOpen = true
        }

        // Big line: the time, or the sender's name while connecting.
        Label {
            id: big
            x: root.margin - 7 * root.ux
            y: 350 * root.uy - baselineOffset
            width: root.colX(8) - x
            u: root.u; tok: t; textStyle: t.dclock
            text: root.pinCode ? root.pinCode.replace(/^(\d{4})(\d{4})$/, "$1 $2")
                  : (root.screenState === "connecting" || root.onTv) && root.deviceName
                  ? root.deviceName : root.pad(root.clockNow.getHours()) + ":" + root.pad(root.clockNow.getMinutes())
            fontSizeMode: Text.HorizontalFit
            minimumPixelSize: 40 * root.u
            elide: Text.ElideRight
        }
        Label {
            id: dateLine
            x: root.margin
            y: 435 * root.uy - baselineOffset
            u: root.u; tok: t
            textStyle: root.pinCode || root.loading ? t.ddate : t.dname
            color: root.pinCode || root.loading ? t.muted : t.fg
            textFormat: Text.StyledText
            text: root.pinCode ? "Type this PIN on your PC"
                  : root.onTv ? "On " + root.tvName
                  : root.screenState === "connecting" ? root.dotted("Connecting", true)
                  : root.receiverName || root.dotted("Loading", true)   // the name to pick in Win + K / Screen Mirroring
        }
        Label {
            anchors.right: parent.right; anchors.rightMargin: root.margin
            y: 433 * root.uy - baselineOffset
            u: root.u; tok: t; textStyle: t.dplace
            text: root.location
        }

        Rectangle { y: 473 * root.uy; x: root.margin; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }

        // Status, bottom left.
        Item {
            readonly property bool active: root.screenState === "connecting" || root.pinCode !== "" || root.onTv
            id: status
            x: root.margin; y: 499 * root.uy
            Rectangle {
                id: dot
                width: 9 * root.u; height: width; radius: width / 2
                anchors.verticalCenter: parent.top
                color: status.active ? t.accent : "transparent"
                border.width: status.active ? 0 : Math.max(1, 1.3 * root.u)
                border.color: t.accent
                SequentialAnimation on opacity {
                    running: status.active && !root.tvCard; loops: Animation.Infinite
                    onRunningChanged: if (!running) dot.opacity = 1
                    NumberAnimation { to: 0.3; duration: 600; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1; duration: 600; easing.type: Easing.InOutSine }
                }
            }
            Label {
                x: 17 * root.ux; anchors.verticalCenter: parent.top
                u: root.u; tok: t; textStyle: t.dlabel
                textFormat: Text.StyledText
                text: root.pinCode ? "Pairing" : root.onTv ? "Showing on the TV"
                      : root.screenState === "connecting" ? root.dotted("Connecting")
                      : !root.receiverName ? root.dotted("Getting ready")
                      : root.tvName ? "Casts go to " + root.tvName
                      : root.tvStatus ? root.tvStatus : "Waiting for a device"
            }
            Label {
                y: 24 * root.uy - height / 2
                u: root.u; tok: t; textStyle: t.dlabel; color: t.muted
                text: root.coordinates
            }
        }

        // How to connect, one row per way in. Snapped to the right: against the QR code when
        // it is shown, else the right margin. Each key stays on one line.
        // Loader in the instructions' place while the hub decides how senders reach it (SPEC D28):
        // the rows never show steps that won't work on this network.
        Label {
            visible: root.receiverName === "" && root.screenState === "idle" && !root.pinCode && !root.tvCard
            anchors.right: parent.right; anchors.rightMargin: root.margin
            y: 494 * root.uy
            u: root.u; tok: t; textStyle: t.dlabel; color: t.muted
            textFormat: Text.StyledText
            text: root.dotted("Finding the best way to connect")
        }

        Grid {
            id: ways
            visible: root.receiverName !== "" && !root.tvCard
            anchors.right: qr.visible ? qr.left : parent.right
            anchors.rightMargin: qr.visible ? 2 * root.gutter : root.margin
            y: 494 * root.uy
            columns: 3
            columnSpacing: 18 * root.ux
            rowSpacing: 14 * root.uy
            verticalItemAlignment: Grid.AlignVCenter
            readonly property var rows: {
                var r = []
                var name = " › " + root.receiverName
                // Hotspot routes: Apple devices join the hub's own Wi-Fi first (the network keeps
                // devices apart, or the hub left it). Windows always finds the hub over Wi-Fi Direct.
                if (root.airplay)
                    r.push({ k: "Apple · AirPlay",
                             v: (root.hotspotOn ? "Scan the code, then Screen Mirroring" : "Control Centre › Screen Mirroring") + name })
                r.push({ k: "Windows · Miracast", v: "Press Win + K" + name })
                if (root.hotspotOn && root.hotspotName)
                    r.push({ k: root.route === "hotspot-alone" ? "Wi-Fi · offline" : "Cast Hub Wi-Fi",
                             v: root.hotspotName + (root.hotspotPassword ? " · password " + root.hotspotPassword : "") })
                return r
            }
            Repeater {
                model: ways.rows.length * 3
                Label {
                    required property int index
                    readonly property var row: ways.rows[Math.floor(index / 3)]
                    readonly property int part: index % 3
                    u: root.u; tok: t
                    textStyle: part === 0 ? t.dnum : part === 1 ? t.dlabel : t.dbody
                    color: part === 0 ? t.muted : t.fg
                    text: part === 0 ? root.pad(Math.floor(index / 3) + 1) : part === 1 ? row.k : row.v
                }
            }
        }

        // Hotspot QR. Always dark modules on a light ground so phones read it in either theme.
        Rectangle {
            id: qr
            visible: root.hotspotOn && root.receiverName !== "" && !root.tvCard
            readonly property real side: 110 * root.u
            readonly property real pad: root.dark ? 6 * root.u : 0
            width: side + 2 * pad; height: width
            anchors.right: parent.right; anchors.rightMargin: root.margin + 4 * root.ux - pad
            y: 494 * root.uy - pad
            radius: root.dark ? 4 * root.u : 0
            color: root.dark ? "#FFFFFF" : "transparent"
            Image {
                anchors.centerIn: parent
                width: parent.side; height: width
                source: root.qrPayload ? "image://qr/" + encodeURIComponent(root.qrPayload) : ""
                sourceSize.width: width; sourceSize.height: height
                smooth: false
                cache: false
            }
        }
    }

    // ---- busy toast: a second sender tried this screen -------------------------
    Rectangle {
        id: toast
        visible: opacity > 0
        opacity: root.busyText ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: t.motionNormal } }
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.height - 36 * root.uy - height / 2
        width: toastRow.width + 28 * root.u; height: 34 * root.u
        radius: height / 2
        color: t.surface
        border.color: t.line; border.width: root.hair
        Row {
            id: toastRow
            anchors.centerIn: parent
            spacing: 10 * root.u
            Chip { anchors.verticalCenter: parent.verticalCenter; u: root.u; tok: t; kind: "warning"; text: "Busy" }
            Label { anchors.verticalCenter: parent.verticalCenter; u: root.u; tok: t; textStyle: t.dbody; text: root.busyText }
        }
    }

    Loader {
        id: leaveLoader
        anchors.fill: parent
        active: root.leaveOpen
        focus: true
        source: Qt.resolvedUrl("../../switcher/qml/LeavePage.qml")
        onLoaded: {
            item.receiverName = Qt.binding(function () { return root.receiverName })
            item.theme = Qt.binding(function () { return root.theme })
            item.fixedNow = root.fixedNow
            item.panel = root.panel
            item.passthrough = Qt.binding(function () { return root.passthrough })
            item.searching = Qt.binding(function () { return root.searching })
            item.forceActiveFocus()
        }
        Connections {
            target: leaveLoader.item
            function onChosen(action) { root.leaveChosen(action) }
            function onCancelled() { root.leaveCancelled() }
        }
    }
}

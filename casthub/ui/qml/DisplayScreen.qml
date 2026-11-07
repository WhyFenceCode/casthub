// One Display-mode screen (design pages 12-13): idle, connecting, streaming, busy toast.
// casthub/ui/app.py creates one per output and sets these properties; it never blocks
// on the receiver (SPEC: ui renders state per screen).
import QtQuick
import "uikit"

Rectangle {
    id: root

    // ---- state from the hub ---------------------------------------------------
    property string screenState: "idle"          // idle | connecting | streaming
    property string receiverName: "Cast Hub-1"
    property string deviceName: ""          // sender, while connecting/streaming
    property string busyText: ""            // non-empty: show the busy toast
    property string location: ""
    property string coordinates: ""
    property bool airplay: true
    property string qrPayload: ""           // hotspot join code; empty = hotspot off (D13)
    property string theme: "auto"           // auto | light | dark

    // ---- PIN page (Ctrl+Alt+Esc), drawn on top of this screen ------------------
    property bool pinOpen: false
    property bool pinRequired: true
    signal pinSubmitted(string pin)
    signal pinChosen(string action)
    signal pinCancelled()
    function pinResult(ok, message) { if (pinLoader.item) pinLoader.item.result(ok, message) }

    // ---- clock ----------------------------------------------------------------
    property var fixedNow: null             // preview harness only
    property date now: new Date()
    Timer { interval: 1000; running: root.fixedNow === null; repeat: true; onTriggered: root.now = new Date() }
    readonly property date clockNow: fixedNow !== null ? fixedNow : now

    readonly property bool dark: theme === "dark" || theme === "auto" && (clockNow.getHours() < 7 || clockNow.getHours() >= 19)

    // ---- frame (tokens [display]: 1080 x 633) ---------------------------------
    readonly property real ux: width / t.displayWidth
    readonly property real uy: height / t.displayHeight
    readonly property real u: Math.min(ux, uy)
    readonly property real margin: t.displayMargin * ux
    readonly property real gutter: t.displayGutter * ux
    readonly property real col: (width - 2 * margin - (t.gridColumns - 1) * gutter) / t.gridColumns
    function colX(i) { return margin + i * (col + gutter) }
    readonly property real hair: Math.max(1, Math.round(u))

    Tokens { id: t; dark: root.dark }
    color: root.screenState === "streaming" ? "black" : t.bg
    Behavior on color { ColorAnimation { duration: t.motionNormal } }

    function pad(n) { return n < 10 ? "0" + n : "" + n }
    readonly property var days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    readonly property var months: ["January", "February", "March", "April", "May", "June", "July",
                                   "August", "September", "October", "November", "December"]

    // ---- idle + connecting ----------------------------------------------------
    Item {
        id: page
        anchors.fill: parent
        visible: root.screenState !== "streaming"

        GridLines { u: root.ux; tok: t; margin: root.margin; gutter: root.gutter }

        DisplayBar {
            u: root.u; ux: root.ux; uy: root.uy; tok: t
            margin: root.margin; modeX: root.colX(4)
            receiverName: root.receiverName
        }

        // Big line: the time, or the sender's name while connecting.
        Label {
            id: big
            x: root.margin - 7 * root.ux
            y: 350 * root.uy - baselineOffset
            width: root.colX(8) - x
            u: root.u; tok: t; textStyle: t.dclock
            text: root.screenState === "connecting" && root.deviceName
                  ? root.deviceName : root.pad(root.clockNow.getHours()) + ":" + root.pad(root.clockNow.getMinutes())
            fontSizeMode: Text.HorizontalFit
            minimumPixelSize: 40 * root.u
            elide: Text.ElideRight
        }
        Label {
            id: dateLine
            x: root.margin
            y: 435 * root.uy - baselineOffset
            u: root.u; tok: t; textStyle: t.ddate
            color: root.screenState === "connecting" ? t.muted : t.fg
            text: root.screenState === "connecting" ? "Connecting…"
                  : root.days[root.clockNow.getDay()] + " " + root.clockNow.getDate() + " " + root.months[root.clockNow.getMonth()]
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
            x: root.margin; y: 499 * root.uy
            Rectangle {
                id: dot
                width: 9 * root.u; height: width; radius: width / 2
                anchors.verticalCenter: parent.top
                color: root.screenState === "connecting" ? t.accent : "transparent"
                border.width: root.screenState === "connecting" ? 0 : Math.max(1, 1.3 * root.u)
                border.color: t.accent
                SequentialAnimation on opacity {
                    running: root.screenState === "connecting"; loops: Animation.Infinite
                    onRunningChanged: if (!running) dot.opacity = 1
                    NumberAnimation { to: 0.3; duration: 600; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1; duration: 600; easing.type: Easing.InOutSine }
                }
            }
            Label {
                x: 17 * root.ux; anchors.verticalCenter: parent.top
                u: root.u; tok: t; textStyle: t.dlabel
                text: root.screenState === "connecting" ? "Connecting" : "Waiting for a device"
            }
            Label {
                y: 24 * root.uy - height / 2
                u: root.u; tok: t; textStyle: t.dlabel; color: t.muted
                text: root.coordinates
            }
        }

        // How to connect, one row per way in.
        Column {
            x: 290 * root.ux; y: 494 * root.uy
            spacing: 14 * root.uy
            Repeater {
                model: {
                    var rows = []
                    if (root.airplay) rows.push({ k: "Apple · AirPlay", v: "Control Centre › Screen Mirroring › " + root.receiverName })
                    rows.push({ k: "Windows · Miracast", v: "Press Win + K › " + root.receiverName })
                    if (root.qrPayload) rows.push({ k: "Anything else", v: "Scan the code to pair" })
                    return rows
                }
                Item {
                    required property var modelData
                    required property int index
                    width: 600 * root.ux
                    height: key.height
                    Label { y: (key.lineH - height) / 2 - 1 * root.uy; u: root.u; tok: t; textStyle: t.dnum; color: t.muted; text: root.pad(index + 1) }
                    Label {
                        id: key
                        readonly property real lineH: 15 * root.uy
                        x: 35 * root.ux; width: 135 * root.ux
                        u: root.u; tok: t; textStyle: t.dlabel
                        wrapMode: Text.WordWrap
                        lineHeightMode: Text.FixedHeight; lineHeight: lineH
                        text: modelData.k
                    }
                    Label {
                        id: val
                        x: 185 * root.ux; y: (key.lineH - height) / 2 + 1 * root.uy
                        u: root.u; tok: t; textStyle: t.dbody
                        text: modelData.v
                    }
                }
            }
        }

        // Hotspot QR. Always dark modules on a light ground so phones read it in either theme.
        Rectangle {
            visible: root.qrPayload !== ""
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
        id: pinLoader
        anchors.fill: parent
        active: root.pinOpen
        focus: true
        source: Qt.resolvedUrl("../../switcher/qml/PinPage.qml")
        onLoaded: {
            item.receiverName = Qt.binding(function () { return root.receiverName })
            item.theme = Qt.binding(function () { return root.theme })
            item.fixedNow = root.fixedNow
            item.pinRequired = root.pinRequired
            item.unlocked = !root.pinRequired
            item.forceActiveFocus()
        }
        Connections {
            target: pinLoader.item
            function onSubmitted(pin) { root.pinSubmitted(pin) }
            function onChosen(action) { root.pinChosen(action) }
            function onCancelled() { root.pinCancelled() }
        }
    }
}

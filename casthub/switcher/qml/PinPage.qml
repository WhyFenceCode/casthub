// Leave Display mode (SPEC D7): Ctrl+Alt+Esc opens this page on top of everything.
// Same frame as the idle screen (design pages 12-13): PIN dots where the clock is, and
// once unlocked the instruction rows become the choices. Python checks the PIN
// (casthub/switcher/pin.py) and runs the action; QML only collects keys.
import QtQuick
import "../../ui/qml"
import "../../ui/qml/uikit"

Rectangle {
    id: root

    // ---- state ----------------------------------------------------------------
    property string receiverName: ""
    property string theme: "auto"
    property bool pinRequired: true         // false: no PIN configured, go straight to the choices
    property int entered: 0                 // digits typed (preview sets this directly)
    property string error: ""
    property bool unlocked: !pinRequired
    property int selected: 0
    property bool busy: false               // waiting for Python to check the PIN

    signal submitted(string pin)
    signal chosen(string action)            // "leave" | "reboot" | "poweroff"
    signal cancelled()

    property string pin: ""
    onPinChanged: entered = pin.length

    property var fixedNow: null
    readonly property date clockNow: fixedNow !== null ? fixedNow : new Date()
    readonly property bool dark: theme === "dark" || theme === "auto" && (clockNow.getHours() < 7 || clockNow.getHours() >= 19)

    readonly property var actions: [
        { id: "leave", k: "Return to login", v: "Back to the login screen" },
        { id: "reboot", k: "Restart", v: "Restart this machine" },
        { id: "poweroff", k: "Shut down", v: "Turn this machine off" },
    ]

    // ---- frame ------------------------------------------------------------------
    readonly property real ux: width / t.displayWidth
    readonly property real uy: height / t.displayHeight
    readonly property real u: Math.min(ux, uy)
    readonly property real margin: t.displayMargin * ux
    readonly property real gutter: t.displayGutter * ux
    readonly property real col: (width - 2 * margin - (t.gridColumns - 1) * gutter) / t.gridColumns
    function colX(i) { return margin + i * (col + gutter) }
    readonly property real hair: Math.max(1, Math.round(u))

    Tokens { id: t; dark: root.dark }
    color: t.bg

    // ---- keys -------------------------------------------------------------------
    focus: true
    Keys.onPressed: function (e) {
        if (e.key === Qt.Key_Escape) { root.cancelled(); e.accepted = true; return }
        if (root.busy) { e.accepted = true; return }
        if (!root.unlocked) {
            if (e.text.length === 1 && e.text >= "0" && e.text <= "9" && root.pin.length < 8) {
                root.pin += e.text; root.error = ""
            } else if (e.key === Qt.Key_Backspace) {
                root.pin = root.pin.slice(0, -1)
            } else if ((e.key === Qt.Key_Return || e.key === Qt.Key_Enter) && root.pin.length >= 4) {
                root.busy = true
                root.submitted(root.pin)
                root.pin = ""
            }
        } else {
            if (e.key === Qt.Key_Down || e.key === Qt.Key_Tab) root.selected = (root.selected + 1) % root.actions.length
            else if (e.key === Qt.Key_Up || e.key === Qt.Key_Backtab) root.selected = (root.selected + root.actions.length - 1) % root.actions.length
            else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) root.chosen(root.actions[root.selected].id)
        }
        e.accepted = true
    }
    // Python calls this after checking a PIN.
    function result(ok, message) {
        busy = false
        if (ok) { unlocked = true; error = "" }
        else { error = message; shake.restart() }
    }

    GridLines { u: root.ux; tok: t; margin: root.margin; gutter: root.gutter }

    DisplayBar {
        u: root.u; ux: root.ux; uy: root.uy; tok: t
        margin: root.margin; modeX: root.colX(4)
        modeText: "Leave display mode"
        receiverName: root.receiverName
    }

    // PIN dots where the clock sits: filled per digit typed, at least four places.
    Row {
        id: dots
        x: root.margin
        readonly property real d: 96 * root.u
        y: 350 * root.uy - d
        spacing: 26 * root.u
        visible: !root.unlocked
        Repeater {
            model: Math.max(4, root.entered + (root.entered < 8 && root.entered >= 4 ? 1 : 0))
            Rectangle {
                required property int index
                width: dots.d; height: width; radius: width / 2
                color: index < root.entered ? t.fg : "transparent"
                border.width: Math.max(2, 3 * root.u); border.color: index < root.entered ? t.fg : t.line
                Behavior on color { ColorAnimation { duration: t.motionFast } }
            }
        }
        SequentialAnimation {
            id: shake
            NumberAnimation { target: dots; property: "x"; to: root.margin + 14 * root.u; duration: 50 }
            NumberAnimation { target: dots; property: "x"; to: root.margin - 14 * root.u; duration: 80 }
            NumberAnimation { target: dots; property: "x"; to: root.margin + 7 * root.u; duration: 70 }
            NumberAnimation { target: dots; property: "x"; to: root.margin; duration: 60 }
        }
    }
    Label {
        visible: root.unlocked
        x: root.margin - 7 * root.ux
        y: 350 * root.uy - baselineOffset
        u: root.u; tok: t; textStyle: t.dclock
        text: "Leave?"
    }

    Label {
        id: heading
        x: root.margin
        y: 435 * root.uy - baselineOffset
        u: root.u; tok: t; textStyle: t.ddate
        text: root.unlocked ? "Choose what to do" : root.busy ? "Checking…" : "Enter PIN"
    }
    Chip {
        visible: root.error !== ""
        anchors.left: heading.right; anchors.leftMargin: 18 * root.ux
        anchors.verticalCenter: heading.verticalCenter
        u: root.u * 1.25; tok: t; kind: "error"; text: root.error
    }

    Rectangle { y: 473 * root.uy; x: root.margin; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }

    Item {
        x: root.margin; y: 499 * root.uy
        Rectangle {
            width: 9 * root.u; height: width; radius: width / 2
            anchors.verticalCenter: parent.top
            color: root.unlocked ? t.accent : "transparent"
            border.width: root.unlocked ? 0 : Math.max(1, 1.3 * root.u); border.color: t.accent
        }
        Label {
            x: 17 * root.ux; anchors.verticalCenter: parent.top
            u: root.u; tok: t; textStyle: t.dlabel
            text: root.unlocked ? "Unlocked" : "Locked"
        }
        Label {
            y: 24 * root.uy - height / 2
            u: root.u; tok: t; textStyle: t.dlabel; color: t.muted
            text: root.unlocked ? "↑↓ · Enter · Esc" : "Digits · Enter · Esc"
        }
    }

    Column {
        x: 290 * root.ux; y: 494 * root.uy
        spacing: 14 * root.uy
        Repeater {
            model: root.actions
            Item {
                id: row
                required property var modelData
                required property int index
                readonly property bool active: root.unlocked && index === root.selected
                width: 600 * root.ux; height: 15 * root.uy
                opacity: root.unlocked ? 1 : 0.45
                Rectangle {
                    visible: row.active
                    x: -12 * root.ux; y: -4 * root.uy
                    width: 2 * root.hair; height: parent.height + 8 * root.uy
                    color: t.accent
                }
                Label { y: (parent.height - height) / 2 - 1 * root.uy; u: root.u; tok: t; textStyle: t.dnum; color: t.muted
                        text: "0" + (index + 1) }
                Label { x: 35 * root.ux; y: (parent.height - height) / 2; u: root.u; tok: t; textStyle: t.dlabel
                        text: modelData.k; color: row.active ? t.fg : t.muted }
                Label { x: 185 * root.ux; y: (parent.height - height) / 2 + 1 * root.uy; u: root.u; tok: t; textStyle: t.dbody
                        text: modelData.v; color: row.active ? t.fg : t.muted }
            }
        }
    }
}

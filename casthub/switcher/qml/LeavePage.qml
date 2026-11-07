// Leave Display mode (SPEC D7): Ctrl+Alt+Esc opens this page on top of the screen.
// No PIN: leaving only returns to the login screen. Same frame as the idle screen (design
// pages 12-13); the instruction rows become the choices. Python runs the chosen action.
import QtQuick
import "../../ui/qml"
import "../../ui/qml/uikit"

Rectangle {
    id: root

    // ---- state ----------------------------------------------------------------
    property string receiverName: ""
    property string theme: "auto"
    property int selected: 0
    property string error: ""

    signal chosen(string action)            // "leave" | "reboot" | "poweroff"
    signal cancelled()

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
        if (e.key === Qt.Key_Escape) root.cancelled()
        else if (e.key === Qt.Key_Down || e.key === Qt.Key_Tab) root.selected = (root.selected + 1) % root.actions.length
        else if (e.key === Qt.Key_Up || e.key === Qt.Key_Backtab) root.selected = (root.selected + root.actions.length - 1) % root.actions.length
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) root.chosen(root.actions[root.selected].id)
        e.accepted = true
    }

    GridLines { u: root.ux; tok: t; margin: root.margin; gutter: root.gutter }

    DisplayBar {
        u: root.u; ux: root.ux; uy: root.uy; tok: t
        margin: root.margin; modeX: root.colX(4)
        modeText: "Leave display mode"
        receiverName: root.receiverName
    }

    Label {
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
        text: "Choose what to do"
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
            color: t.accent
        }
        Label {
            x: 17 * root.ux; anchors.verticalCenter: parent.top
            u: root.u; tok: t; textStyle: t.dlabel
            text: "Display mode paused"
        }
        Label {
            y: 24 * root.uy - height / 2
            u: root.u; tok: t; textStyle: t.dlabel; color: t.muted
            text: "↑↓ · Enter · Esc"
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
                readonly property bool active: index === root.selected
                width: 600 * root.ux; height: 15 * root.uy
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

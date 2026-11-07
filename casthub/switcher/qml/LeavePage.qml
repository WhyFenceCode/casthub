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

    signal chosen(string action)            // "leave" | "reboot" | "poweroff"; never a "later" one
    signal cancelled()

    property var fixedNow: null
    readonly property date clockNow: fixedNow !== null ? fixedNow : new Date()
    readonly property bool dark: theme === "dark" || theme === "auto" && (clockNow.getHours() < 7 || clockNow.getHours() >= 19)

    // "later": shown so the option is known, but not built yet (SPEC D21, passthrough).
    readonly property var actions: [
        { id: "leave", k: "Log out", v: "Back to the login screen" },
        { id: "passthrough", k: "Passthrough setup", v: "Pass AirPlay on to a Miracast display", later: true },
        { id: "reboot", k: "Restart", v: "Restart this machine" },
        { id: "poweroff", k: "Shut down", v: "Turn this machine off" },
    ]
    function choose(i) {
        var a = actions[i]
        if (a.later) { error = "Coming in a later update"; return }
        chosen(a.id)
    }

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
        if (e.key !== Qt.Key_Return && e.key !== Qt.Key_Enter) root.error = ""
        if (e.key === Qt.Key_Escape) root.cancelled()
        else if (e.key === Qt.Key_Down || e.key === Qt.Key_Tab) root.selected = (root.selected + 1) % root.actions.length
        else if (e.key === Qt.Key_Up || e.key === Qt.Key_Backtab) root.selected = (root.selected + root.actions.length - 1) % root.actions.length
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) root.choose(root.selected)
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
        u: root.u * 1.25; tok: t; kind: "accent"; text: root.error
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

    // The choices, in the same right-snapped grid as the idle screen's ways to connect.
    Grid {
        id: choices
        anchors.right: parent.right; anchors.rightMargin: root.margin
        y: 494 * root.uy
        columns: 3
        columnSpacing: 18 * root.ux
        rowSpacing: 14 * root.uy
        verticalItemAlignment: Grid.AlignVCenter
        Repeater {
            id: cells
            model: root.actions.length * 3
            Item {
                id: cell
                required property int index
                readonly property int row: Math.floor(index / 3)
                readonly property int part: index % 3
                readonly property var a: root.actions[row]
                readonly property bool active: row === root.selected
                implicitWidth: content.width; implicitHeight: content.height
                Row {
                    id: content
                    spacing: 12 * root.ux
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        u: root.u; tok: t
                        textStyle: cell.part === 0 ? t.dnum : cell.part === 1 ? t.dlabel : t.dbody
                        color: cell.part === 0 || !cell.active ? t.muted : t.fg
                        text: cell.part === 0 ? "0" + (cell.row + 1) : cell.part === 1 ? cell.a.k : cell.a.v
                    }
                    Chip { visible: cell.part === 2 && cell.a.later === true; anchors.verticalCenter: parent.verticalCenter
                           u: root.u; tok: t; kind: "accent"; text: "Later" }
                }
                MouseArea {
                    anchors.fill: parent; anchors.margins: -6 * root.u
                    onClicked: { root.selected = cell.row; root.choose(cell.row) }
                }
            }
        }
    }
    Rectangle {   // selection marker, left of the selected row
        readonly property Item first: cells.count > 0 ? cells.itemAt(root.selected * 3) : null
        visible: first !== null
        x: choices.x - 12 * root.ux
        y: first ? choices.y + first.y - 4 * root.uy : 0
        width: 2 * root.hair; height: first ? first.height + 8 * root.uy : 0
        color: t.accent
    }
}

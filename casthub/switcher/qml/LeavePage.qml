// Leave Display mode (SPEC D7): Ctrl+Alt+Esc opens this page on top of the screen.
// No PIN: leaving only returns to the login screen. Same frame as the idle screen (design
// pages 12-13); the instruction rows become the choices. Python runs the chosen action.
// "Passthrough setup" (SPEC D29) opens a second view on the same page: the Miracast displays
// found nearby, search again, turn off. In `fixed` mode (casthub.toml) it only shows the target.
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
    // From the hub: {state: off|searching|connecting|on|failed, tv, why, tvs: [names], locked, allowed}
    property var passthrough: ({ state: "off", tv: "", why: "", tvs: [], locked: false, allowed: true })
    property bool panel: false              // the Display panel (casthub-display): Close + passthrough only
    property bool searching: false          // a search for displays is running (~10 s)
    property string view: "main"            // main | passthrough

    signal chosen(string action)            // leave | reboot | poweroff | tv_find | tv_off | tv_on:<name>
    signal cancelled()

    property var fixedNow: null
    readonly property date clockNow: fixedNow !== null ? fixedNow : new Date()
    readonly property bool dark: theme === "dark" || theme === "auto" && (clockNow.getHours() < 7 || clockNow.getHours() >= 19)

    readonly property var pt: passthrough || ({})
    readonly property bool ptActive: pt.state !== undefined && pt.state !== "off"
    readonly property var mainActions: {
        var r = [panel ? { id: "leave", k: "Quit panel", v: "End any cast and close the panel" }
                       : { id: "leave", k: "Log out", v: "Back to the login screen" }]
        if (pt.allowed !== false)
            r.push({ id: "passthrough", k: "Passthrough setup",
                     v: pt.state === "on" ? "Sending to " + pt.tv
                        : pt.locked ? "Set by the administrator" : "Send casts on to a Miracast display" })
        if (panel) {                        // the desktop is still there: no restart or shut down here
            r.push({ id: "back", k: "Resume", v: "Back to the Display screen" })
            return r
        }
        r.push({ id: "reboot", k: "Restart", v: "Restart this machine" })
        r.push({ id: "poweroff", k: "Shut down", v: "Turn this machine off" })
        return r
    }
    readonly property var ptActions: {
        if (pt.locked)
            return [{ id: "back", k: "Back", v: "To the other choices" }]
        // Four rows at most, like the main choices (Esc goes back, as the hint says).
        var r = []
        var tvs = (pt.tvs || []).slice()
        if (ptActive && pt.tv && tvs.indexOf(pt.tv) < 0) tvs.unshift(pt.tv)
        tvs = tvs.slice(0, ptActive ? 2 : 3)
        for (var i = 0; i < tvs.length; i++) {
            var name = tvs[i], current = ptActive && pt.tv === name
            r.push({ id: "tv_on:" + name, k: name,
                     v: current && pt.state === "on" ? "Sending here"
                        : current && pt.state !== "failed" ? "Connecting" : "Send casts here" })
        }
        r.push({ id: "tv_find", k: "Search again", v: searching ? "Searching" : "Look for displays nearby" })
        if (ptActive) r.push({ id: "tv_off", k: "Stop passthrough", v: "Back to Cast Hub's own screens" })
        if (r.length < 4) r.push({ id: "back", k: "Back", v: "To the other choices" })
        return r
    }
    readonly property var actions: view === "passthrough" ? ptActions : mainActions
    function choose(i) {
        var a = actions[i]
        if (a.id === "passthrough") {
            view = "passthrough"; selected = 0
            if (!pt.locked && !searching && (pt.tvs || []).length === 0) chosen("tv_find")
            return
        }
        if (a.id === "back" && view === "main") { cancelled(); return }
        if (a.id === "back") { view = "main"; selected = 1; return }
        if (a.id === "tv_find" && searching) return
        chosen(a.id)
    }
    readonly property string ptHeading: {
        if (pt.locked) return "Set by the administrator: " + pt.tv
        if (pt.state === "on") return "Sending to " + pt.tv
        if (pt.state === "searching" || pt.state === "connecting") return "Connecting to " + pt.tv
        if (pt.state === "failed") return "Couldn't reach " + pt.tv + (pt.why ? " · " + pt.why : "")
        if (searching) return "Looking for displays nearby"
        return (pt.tvs || []).length ? "Pick a display to send casts to" : "No displays found yet"
    }

    // ---- frame ------------------------------------------------------------------
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
    color: t.bg

    // ---- keys -------------------------------------------------------------------
    focus: true
    Keys.onPressed: function (e) {
        if (e.key !== Qt.Key_Return && e.key !== Qt.Key_Enter) root.error = ""
        if (e.key === Qt.Key_Escape) { if (root.view !== "main") { root.view = "main"; root.selected = 1 } else root.cancelled() }
        else if (e.key === Qt.Key_Down || e.key === Qt.Key_Tab) root.selected = (root.selected + 1) % root.actions.length
        else if (e.key === Qt.Key_Up || e.key === Qt.Key_Backtab) root.selected = (root.selected + root.actions.length - 1) % root.actions.length
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) root.choose(root.selected)
        e.accepted = true
    }

    GridLines { u: root.fu; tok: t; margin: root.margin; gutter: root.gutter }

    DisplayBar {
        u: root.fu; tok: t
        margin: root.margin; modeX: root.colX(4)
        // Panel names kept apart (owner, Nov 9: Close / Cancel / Exit side by side confused):
        // "Menu" opens this page, "Quit panel" closes the panel, "Resume" goes back.
        modeText: root.panel ? "Display panel menu" : "Leave display mode"
        logoutText: root.panel ? "Quit panel" : "Log out"
        dateText: Qt.formatDate(root.clockNow, "dddd d MMMM")
        showLogout: true                                   // same bar as the idle screen
        onLogoutRequested: root.chosen("leave")
    }

    Label {
        x: root.margin - 7 * root.ux
        y: 350 * root.uy - baselineOffset
        u: root.u; tok: t; textStyle: t.dclock
        text: root.view === "passthrough" ? "To a TV" : root.panel ? "Menu" : "Leave?"
        fontSizeMode: Text.HorizontalFit
        width: root.colX(8) - x
    }

    Label {
        id: heading
        x: root.margin
        y: 435 * root.uy - baselineOffset
        u: root.u; tok: t; textStyle: t.ddate
        text: root.view === "passthrough" ? root.ptHeading : "Choose what to do"
        width: root.colX(8) - x
        elide: Text.ElideRight
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
            text: root.view === "passthrough" ? "TV only while on · no hotspot" : root.panel ? "Display panel" : "Display mode paused"
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

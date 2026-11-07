// Plasma's leave screen (ksmserver-logout-greeter) in the Cast Hub style (SPEC D22): the
// same frame as the Display leave page. One instance per monitor.
// The greeter provides: sdtype, ShutdownType, maysd, canLogout, spdMethods,
// softwareUpdatePending; we answer with the signals below.
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Window
import org.kde.kirigami as Kirigami
import org.kde.coreaddons as KCoreAddons
import "uikit"
import "timer.js" as Countdown

Rectangle {
    id: root

    signal logoutRequested()
    signal haltRequested()
    signal haltUpdateRequested()
    signal suspendRequested(int spdMethod)
    signal rebootRequested()
    signal rebootRequested2(int opt)
    signal rebootUpdateRequested()
    signal cancelRequested()
    signal lockScreenRequested()
    signal cancelSoftwareUpdateRequested()

    // ---- what was asked for ---------------------------------------------------
    readonly property bool all: sdtype === ShutdownType.ShutdownTypeDefault
    readonly property string asked: sdtype === ShutdownType.ShutdownTypeReboot ? "reboot"
                                   : sdtype === ShutdownType.ShutdownTypeHalt ? "halt" : "logout"
    readonly property bool updates: typeof softwareUpdatePending !== "undefined" && softwareUpdatePending
    readonly property var actions: {
        var a = []
        function add(id, k, v, ok) { if (ok) a.push({ id: id, k: k, v: v }) }
        add("logout", "Log out", "Back to the login screen", canLogout && (all || asked === "logout"))
        add("reboot", "Restart", updates ? "Install updates and restart" : "Restart this machine", maysd && (all || asked === "reboot"))
        add("halt", "Shut down", updates ? "Install updates and shut down" : "Turn this machine off", maysd && (all || asked === "halt"))
        add("sleep", "Sleep", "Suspend; locked when it wakes", all && spdMethods.SuspendState)
        add("lock", "Lock", "Keep everything open, ask for the password", all || asked === "logout")
        add("cancel", "Cancel", "Back to the desktop · Esc", true)
        return a
    }
    function run(id) {
        Countdown.cancelAll()
        switch (id) {
        case "logout": logoutRequested(); break
        case "reboot": updates ? rebootUpdateRequested() : rebootRequested(); break
        case "halt": updates ? haltUpdateRequested() : haltRequested(); break
        case "sleep": suspendRequested(2); break
        case "lock": lockScreenRequested(); break
        default: cancelRequested()
        }
    }
    property int selected: 0

    // ---- countdown when one action was asked for (Plasma's behaviour) -----------
    property int remaining: 30
    Timer {
        id: countdown
        running: !root.all
        interval: 1000; repeat: true
        onTriggered: { root.remaining--; if (root.remaining <= 0) { running = false; root.run(root.asked) } }
        Component.onCompleted: Countdown.onCancel(function () { countdown.running = false })
    }

    // ---- frame (1080 x 675 units, as the login screen) ---------------------------
    readonly property real u: Math.min(width / 1080, height / t.frameHeight)
    readonly property real margin: t.gridMargin * u
    readonly property real gutter: t.gridGutter * u
    readonly property real hair: Math.max(1, Math.round(u))
    Kirigami.Theme.colorSet: Kirigami.Theme.Window
    Kirigami.Theme.inherit: false
    Tokens { id: t; dark: Kirigami.Theme.backgroundColor.hslLightness < 0.5 }
    color: t.bg

    focus: true
    Component.onCompleted: forceActiveFocus()
    Keys.onPressed: function (e) {
        Countdown.cancelAll()
        if (e.key === Qt.Key_Escape) root.cancelRequested()
        else if (e.key === Qt.Key_Down || e.key === Qt.Key_Tab) root.selected = (root.selected + 1) % root.actions.length
        else if (e.key === Qt.Key_Up || e.key === Qt.Key_Backtab) root.selected = (root.selected + root.actions.length - 1) % root.actions.length
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) root.run(root.actions[root.selected].id)
        e.accepted = true
    }
    HoverHandler { onHoveredChanged: if (hovered && root.Window.window) root.Window.window.requestActivate() }

    GridLines { u: root.u; tok: t; margin: root.margin; gutter: root.gutter }

    Wordmark { x: root.margin; y: 23.5 * root.u - height / 2; u: root.u; tok: t }
    Row {
        x: root.margin + 4 * ((root.width - 2 * root.margin - 11 * root.gutter) / 12 + root.gutter)
        y: 23.5 * root.u - height / 2
        spacing: 8 * root.u
        Rectangle { width: 6 * root.u; height: width; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
        Label { u: root.u; tok: t; textStyle: t.label; text: "Leave workstation" }
    }
    Rectangle { x: root.margin; y: 47 * root.u; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }

    Label {
        x: root.margin - 5 * root.u
        y: 345 * root.u - baselineOffset
        u: root.u; tok: t; textStyle: t.today
        font.pixelSize: 200 * root.u
        text: root.asked === "reboot" && !root.all ? "Restart?" : root.asked === "halt" && !root.all ? "Shut down?" : "Leave?"
    }
    Label {
        x: root.margin
        y: 405 * root.u - baselineOffset
        u: root.u; tok: t; textStyle: t.label; color: countdown.running ? t.fg : t.muted
        text: countdown.running
              ? (root.asked === "reboot" ? "Restarting" : root.asked === "halt" ? "Shutting down" : "Logging out") + " in " + root.remaining + " s · any key stops this"
              : "↑↓ · Enter · Esc"
    }
    Rectangle { x: root.margin; y: 430 * root.u; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }

    KCoreAddons.KUser { id: kuser }
    Column {
        x: root.margin; y: 452 * root.u
        spacing: 6 * root.u
        Label { u: root.u; tok: t; textStyle: t.name; text: kuser.fullName || kuser.loginName }
        Label { u: root.u; tok: t; textStyle: t.label; color: t.muted; text: "Signed in · Workstation" }
    }

    // Choices: the same right-snapped grid as the Display screens.
    Grid {
        id: choices
        anchors.right: parent.right; anchors.rightMargin: root.margin
        y: 452 * root.u
        columns: 3
        columnSpacing: 18 * root.u
        rowSpacing: 12 * root.u
        verticalItemAlignment: Grid.AlignVCenter
        Repeater {
            id: cells
            model: root.actions.length * 3
            Label {
                id: cell
                required property int index
                readonly property int row: Math.floor(index / 3)
                readonly property int part: index % 3
                readonly property bool active: row === root.selected
                u: root.u; tok: t
                textStyle: part === 0 ? t.label : part === 1 ? t.label : t.body
                color: part === 0 || !active ? t.muted : t.fg
                text: part === 0 ? "0" + (row + 1) : part === 1 ? root.actions[row].k : root.actions[row].v
                MouseArea {
                    anchors.fill: parent; anchors.margins: -5 * root.u
                    hoverEnabled: true
                    onEntered: root.selected = cell.row
                    onClicked: root.run(root.actions[cell.row].id)
                }
            }
        }
    }
    Rectangle {
        readonly property Item first: cells.count > root.selected * 3 ? cells.itemAt(root.selected * 3) : null
        visible: first !== null
        x: choices.x - 12 * root.u
        y: first ? choices.y + first.y - 4 * root.u : 0
        width: 2 * root.hair; height: first ? first.height + 8 * root.u : 0
        color: t.accent
    }

    QQC2.Action { shortcut: "Escape"; onTriggered: root.cancelRequested() }
}

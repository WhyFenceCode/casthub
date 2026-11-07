// Cast Hub desktop layer (SPEC D17). Drawn under all windows, on top of the grid wallpaper.
//   Console / Files tabs (design pages 7-9): Today panel on the left, and the frame of the
//     app panel on the right; the KWin views script docks Konsole / Dolphin inside it.
//   Desktop tab (pages 10-11): ~/Desktop as tiles, the day in faint display type, and the
//     month · location · coordinates line.
// Geometry: 1080 x 675 frame units (u = height / 675) with the dashboard moved up 20 units
// for the thin top bar (D20); numbers shared with the KWin script via brand/tokens.toml.
import QtCore
import QtQuick
import Qt.labs.folderlistmodel
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.taskmanager as TaskManager
import org.kde.ksysguard.sensors as Sensors
import org.kde.plasma.workspace.calendar as PlasmaCalendar
import org.kde.plasma.plasma5support as P5Support
import "uikit"
import "uikit/Names.js" as Names

ContainmentItem {
    id: root

    // ---- frame -----------------------------------------------------------------
    // The desktop layer covers the whole screen; the top panel floats above it.
    readonly property real u: Math.max(1, height) / t.frameHeight
    readonly property real margin: t.gridMargin * u
    readonly property real gutter: t.gridGutter * u
    readonly property real col: (width - 2 * margin - (t.gridColumns - 1) * gutter) / t.gridColumns
    function colX(i) { return margin + i * (col + gutter) }
    function yAt(v) { return v * u }
    readonly property real hair: Math.max(1, Math.round(u))

    readonly property bool dark: Kirigami.Theme.backgroundColor.hslLightness < 0.5
    Tokens { id: t; dark: root.dark }
    Tokens { id: term; dark: true }                              // the console panel is always dark

    // ---- which tab -------------------------------------------------------------
    TaskManager.VirtualDesktopInfo { id: vdi }
    readonly property int tab: Math.max(0, vdi.desktopIds.indexOf(vdi.currentDesktop))
    readonly property bool dashboard: tab < 2

    property date now: new Date()
    Timer { interval: 30000; running: true; repeat: true; triggeredOnStart: true; onTriggered: { root.now = new Date(); agenda.refresh() } }
    function pad(n) { return n < 10 ? "0" + n : "" + n }
    readonly property var monthsShort: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    readonly property var months: ["January", "February", "March", "April", "May", "June", "July", "August",
                                   "September", "October", "November", "December"]
    readonly property var days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

    // ---- launching apps (empty dock, opening files) -----------------------------
    P5Support.DataSource {
        id: exec
        engine: "executable"
        onNewData: function (source) { disconnectSource(source) }
        function run(cmd) { connectSource(cmd) }
    }
    TaskManager.TasksModel {
        id: deskTasks
        groupMode: TaskManager.TasksModel.GroupDisabled
        filterByVirtualDesktop: true
        virtualDesktop: vdi.currentDesktop
    }
    readonly property var dockApps: ["org.kde.konsole", "org.kde.dolphin"]
    readonly property var dockCmds: ["kstart -- konsole --hide-menubar --hide-toolbars", "kstart dolphin"]
    property string dockTitle: ""
    property bool dockPresent: false
    Repeater {
        model: deskTasks
        delegate: Item {
            required property var model
            readonly property bool isDock: root.dashboard && model.AppId === root.dockApps[root.tab]
            Component.onCompleted: if (isDock) root.dockPresent = true
            Binding { when: isDock; target: root; property: "dockTitle"; value: Names.cleanTitle(model.display, model.AppName) }
        }
    }
    // Open the docked app the first time its tab is empty.
    property var launched: ({})
    Timer {
        id: dockCheck; interval: 900
        onTriggered: {
            if (!root.dashboard || root.dockPresent || root.launched[root.tab]) return
            root.launched[root.tab] = true
            exec.run(root.dockCmds[root.tab])
        }
    }
    onTabChanged: { dockPresent = false; dockTitle = ""; dockRescan.restart() }
    Timer { id: dockRescan; interval: 50; onTriggered: { root.dockPresent = false; for (var i = 0; i < deskTasks.count; i++) { var idx = deskTasks.index(i, 0); if (deskTasks.data(idx, TaskManager.AbstractTasksModel.AppId) === root.dockApps[root.tab]) root.dockPresent = true } dockCheck.restart() } }
    Connections { target: deskTasks; function onCountChanged() { dockRescan.restart() } }

    // =============================================================================
    // Console / Files: Today panel + app panel frame
    // =============================================================================
    Item {
        id: dash
        anchors.fill: parent
        visible: root.dashboard
        opacity: visible ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: t.motionNormal } }

        // ■ TODAY
        Row {
            x: root.margin; y: root.yAt(83.5) - height / 2
            spacing: 7 * root.u
            Rectangle { width: 5 * root.u; height: width; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
            Label { u: root.u; tok: t; textStyle: t.label; text: "Today" }
        }
        Label {
            id: bigDay
            x: root.margin - 2 * root.u
            y: root.yAt(204) - baselineOffset
            u: root.u; tok: t; textStyle: t.today
            text: root.pad(root.now.getDate())
        }
        Column {
            x: root.colX(1) + 23 * root.u
            y: root.yAt(204) - height + 3 * root.u
            spacing: 6 * root.u
            Label { u: root.u; tok: t; textStyle: t.label; text: root.monthsShort[root.now.getMonth()] }
            Label { u: root.u; tok: t; textStyle: t.label; text: root.days[root.now.getDay()] }
        }
        Rectangle { x: root.margin; y: root.yAt(233); width: root.colX(4) - root.gutter - root.margin; height: root.hair; color: t.fg }

        // Agenda (D19): KDE calendars + holidays; hidden when there is nothing today.
        Column {
            id: agendaList
            x: root.margin; y: root.yAt(233) + root.hair
            width: root.colX(4) - root.gutter - root.margin
            visible: agenda.events.length > 0
            Repeater {
                model: agenda.events
                Item {
                    required property var modelData
                    width: agendaList.width; height: 37.5 * root.u
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        u: root.u; tok: t; textStyle: t.dnum; color: t.muted
                        text: modelData.time
                    }
                    Label {
                        x: 52 * root.u; width: parent.width - x
                        anchors.verticalCenter: parent.verticalCenter
                        u: root.u; tok: t; textStyle: t.body
                        text: modelData.title; elide: Text.ElideRight
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: root.hair; color: t.line }
                }
            }
        }
        QtObject {
            id: agenda
            property var events: []
            function refresh() {
                var day = new Date(root.now.getFullYear(), root.now.getMonth(), root.now.getDate())
                var raw = cal.daysModel ? cal.daysModel.eventsForDate(day) : []
                var out = []
                for (var i = 0; i < raw.length; i++) {
                    var e = raw[i]
                    var s = e.startDateTime
                    out.push({
                        sort: e.isAllDay ? -1 : s.getHours() * 60 + s.getMinutes(),
                        time: e.isAllDay ? "All day" : root.pad(s.getHours()) + ":" + root.pad(s.getMinutes()),
                        title: e.title
                    })
                }
                out.sort(function (a, b) { return a.sort - b.sort })
                events = out.slice(0, 8)
            }
        }
        PlasmaCalendar.EventPluginsManager {
            id: plugins
            enabledPlugins: Plasmoid.configuration.calendarPlugins
        }
        PlasmaCalendar.Calendar {
            id: cal
            days: 7; weeks: 6
            today: root.now
            Component.onCompleted: { daysModel.setPluginsManager(plugins); agenda.refresh() }
        }
        Connections {
            target: cal.daysModel
            function onModelReset() { agenda.refresh() }
            function onDataChanged() { agenda.refresh() }
            function onAgendaUpdated() { agenda.refresh() }
            ignoreUnknownSignals: true
        }

        // System meters, bottom left (page 7).
        Sensors.Sensor { id: cpu; sensorId: "cpu/all/usage"; updateRateLimit: 2000 }
        Sensors.Sensor { id: gpu; sensorId: "gpu/all/usage"; updateRateLimit: 2000 }
        Sensors.Sensor { id: memUsed; sensorId: "memory/physical/used"; updateRateLimit: 4000 }
        Sensors.Sensor { id: memTotal; sensorId: "memory/physical/total"; updateRateLimit: 60000 }
        function gb(b) { var g = b / 1073741824; return g >= 10 ? Math.round(g) : g.toFixed(1) }
        Repeater {
            model: [
                { k: "CPU", v: Math.round(cpu.value || 0) + "%", f: (cpu.value || 0) / 100, top: 563 },
                { k: "GPU", v: Math.round(gpu.value || 0) + "%", f: (gpu.value || 0) / 100, top: 592 },
                { k: "RAM", v: memTotal.value ? dash.gb(memUsed.value) + " / " + dash.gb(memTotal.value) + " GB" : "",
                  f: memTotal.value ? memUsed.value / memTotal.value : 0, top: 621 }
            ]
            Item {
                required property var modelData
                x: root.margin; y: root.yAt(modelData.top)
                width: root.colX(4) - root.gutter - root.margin; height: 18 * root.u
                Label { u: root.u; tok: t; textStyle: t.barlabel; color: t.muted; text: modelData.k }
                Label { anchors.right: parent.right; u: root.u; tok: t; textStyle: t.barlabel; text: modelData.v }
                Rectangle { y: 16 * root.u; width: parent.width; height: 2 * root.hair; color: t.line }
                Rectangle {
                    y: 16 * root.u; height: 2 * root.hair; color: t.fg
                    width: parent.width * Math.max(0, Math.min(1, modelData.f))
                    Behavior on width { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
                }
            }
        }

        // App panel frame: the docked window sits under the header (KWin views script).
        Rectangle {
            id: panel
            readonly property bool isConsole: root.tab === 0
            readonly property Tokens pt: isConsole ? term : t
            x: root.colX(t.shellPanelFirstCol); y: root.yAt(t.shellPanelTop)
            width: root.width - root.margin - x; height: root.yAt(t.shellPanelBottom) - y
            radius: t.radiusPanel * root.u
            color: pt.surface
            border.width: root.hair; border.color: isConsole ? term.line : t.line

            Label {
                x: 15 * root.u; y: t.shellPanelHeader * root.u / 2 - height / 2
                width: parent.width - 30 * root.u
                u: root.u; tok: panel.pt; textStyle: t.barlabel
                textFormat: Text.StyledText
                text: (panel.isConsole ? "Console" : "Files") + (root.dockTitle
                      ? "<font color=\"" + panel.pt.muted + "\">  ·  " + root.dockTitle.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</font>" : "")
                elide: Text.ElideRight
            }
            Rectangle { y: t.shellPanelHeader * root.u - root.hair; width: parent.width; height: root.hair; color: panel.pt.line }

            // Shown only until the docked app appears (or if it was closed).
            Column {
                anchors.centerIn: parent
                spacing: 12 * root.u
                visible: !root.dockPresent
                Label { anchors.horizontalCenter: parent.horizontalCenter; u: root.u; tok: panel.pt; textStyle: t.label; color: panel.pt.muted
                        text: panel.isConsole ? "No console open" : "No file window open" }
                PillButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    u: root.u; tok: panel.pt
                    text: panel.isConsole ? "Open console" : "Open files"
                    onClicked: exec.run(root.dockCmds[root.tab])
                }
            }
        }
    }

    // =============================================================================
    // Desktop: ~/Desktop tiles, faint day, footer
    // =============================================================================
    Item {
        id: desk
        anchors.fill: parent
        visible: !root.dashboard
        opacity: visible ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: t.motionNormal } }

        Label {
            anchors.right: parent.right; anchors.rightMargin: 73 * root.u
            y: root.yAt(634) - baselineOffset
            u: root.u; tok: t; textStyle: t.faint; color: t.well
            text: root.pad(root.now.getDate())
        }
        Label {
            anchors.right: parent.right; anchors.rightMargin: 37 * root.u
            y: root.yAt(648.5) - height / 2
            u: root.u; tok: t; textStyle: t.label; color: t.muted
            text: [root.months[root.now.getMonth()], Plasmoid.configuration.location, Plasmoid.configuration.coordinates]
                  .filter(function (s) { return s && s.indexOf("@") < 0 }).join("  ·  ")
        }

        FolderListModel {
            id: files
            folder: StandardPaths.writableLocation(StandardPaths.DesktopLocation)
            showDirsFirst: true
            showHidden: false
            sortField: FolderListModel.Name
        }
        property string selected: ""
        MouseArea { anchors.fill: parent; onClicked: desk.selected = "" }

        Repeater {
            model: files.count + 1                     // + Trash
            Item {
                id: tile
                required property int index
                readonly property bool trash: index === files.count
                readonly property string name: trash ? "Trash" : files.get(index, "fileName")
                readonly property string url: trash ? "trash:/" : files.get(index, "fileUrl").toString()
                readonly property bool isDir: !trash && files.get(index, "fileIsDir")
                readonly property string kind: trash ? "Bin" : isDir ? "Dir"
                                               : (name.lastIndexOf(".") > 0 ? name.slice(name.lastIndexOf(".") + 1) : "File")
                readonly property bool sel: desk.selected === url
                readonly property int perCol: Math.max(1, Math.floor((root.height - root.yAt(53) - 40 * root.u) / (84 * root.u)))
                x: (41 + Math.floor(index / perCol) * 90) * root.u - 19 * root.u
                y: root.yAt(53) + (index % perCol) * 84 * root.u
                width: 82 * root.u; height: 76 * root.u

                Rectangle {
                    id: box
                    x: 19 * root.u; width: 44 * root.u; height: 45 * root.u
                    color: tile.sel ? t.fg : "transparent"
                    border.width: root.hair; border.color: t.fg
                    Rectangle { visible: tile.sel; anchors.right: parent.right; width: 2 * root.hair; height: parent.height; color: t.accent }
                    Label {
                        x: 5 * root.u; anchors.bottom: parent.bottom; anchors.bottomMargin: 5 * root.u
                        u: root.u; tok: t; textStyle: t.tile; color: tile.sel ? t.bg : t.fg
                        text: tile.kind.slice(0, 4)
                    }
                }
                Rectangle {
                    anchors.horizontalCenter: box.horizontalCenter
                    y: box.height + 7 * root.u
                    width: nameLbl.width + 6 * root.u; height: nameLbl.height + 2 * root.u
                    color: tile.sel ? t.fg : "transparent"
                    Label {
                        id: nameLbl
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, 80 * root.u)
                        u: root.u; tok: t; textStyle: t.small; color: tile.sel ? t.bg : t.fg
                        text: tile.name; elide: Text.ElideMiddle
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: desk.selected = tile.url
                    onDoubleClicked: Qt.openUrlExternally(tile.url)
                }
            }
        }
    }
}

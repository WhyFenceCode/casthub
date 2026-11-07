// Cast Hub top bar (design page 10, one thin bar on every tab: D20).
// Wordmark (opens KRunner) · CONSOLE FILES DESKTOP (virtual desktops, D17) · active window.
// The tray and the clock are separate panel widgets to the right.
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.taskmanager as TaskManager
import org.kde.plasma.workspace.dbus as DBus
import "uikit"
import "uikit/Names.js" as Names

PlasmoidItem {
    id: root
    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Plasmoid.constraintHints: Plasmoid.CanFillArea
    Layout.fillWidth: true
    Layout.fillHeight: true
    Layout.minimumWidth: 400 * u

    readonly property real u: Math.max(1, height) / t.shellBar
    readonly property bool dark: Kirigami.Theme.backgroundColor.hslLightness < 0.5
    Tokens { id: t; dark: root.dark }

    // ---- tabs = the first three virtual desktops (named by the views KWin script) ----
    TaskManager.VirtualDesktopInfo { id: vdi }
    readonly property int tab: Math.max(0, vdi.desktopIds.indexOf(vdi.currentDesktop))
    readonly property var tabs: ["Console", "Files", "Desktop"]
    function show(i) {
        DBus.SessionBus.asyncCall({
            service: "org.kde.kglobalaccel", path: "/component/kwin",
            iface: "org.kde.kglobalaccel.Component", member: "invokeShortcut",
            arguments: ["CastHub " + tabs[i]]
        })
    }
    function runner() {
        DBus.SessionBus.asyncCall({
            service: "org.kde.krunner", path: "/App", iface: "org.kde.krunner.App",
            member: "toggleDisplay", arguments: []
        })
    }

    // ---- active window title ----------------------------------------------------
    property string activeTitle: ""
    TaskManager.TasksModel {
        id: tasks
        groupMode: TaskManager.TasksModel.GroupDisabled
        filterByVirtualDesktop: true
        virtualDesktop: vdi.currentDesktop
    }
    Repeater {
        model: tasks
        delegate: Item {
            required property var model
            Binding {
                when: model.IsActive === true
                target: root; property: "activeTitle"
                value: Names.caption(model.AppId, model.AppName, model.display, " — ")
            }
        }
    }
    Connections {
        target: tasks
        function onActiveTaskChanged() { if (!tasks.activeTask.valid) root.activeTitle = "" }
    }

    fullRepresentation: Item {
        id: bar
        Layout.fillWidth: true
        Layout.fillHeight: true

        // Everything in the bar sits on one baseline (that of the mono labels).
        Label {
            id: baselineRef
            visible: false
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: -0.5 * root.u
            u: root.u; tok: t; textStyle: t.barlabel; text: "X"
        }
        Wordmark {
            id: mark
            x: t.shellBarMargin * root.u
            anchors.baseline: baselineRef.baseline
            u: root.u * t.barmark.size / t.wordmark.size; tok: t
        }
        MouseArea {
            x: mark.x - 6 * root.u; y: 0; width: mark.width + 12 * root.u; height: bar.height
            cursorShape: Qt.PointingHandCursor
            onClicked: root.runner()
        }

        // Tabs at x 119 / 175 / 219 (page 10); active one underlined.
        Repeater {
            model: root.tabs
            Item {
                id: tabItem
                required property string modelData
                required property int index
                readonly property bool active: root.tab === index
                x: [119, 175, 219][index] * root.u
                width: lbl.width; height: bar.height
                Label {
                    id: lbl
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: -0.5 * root.u
                    u: root.u; tok: t; textStyle: t.barlabel
                    text: tabItem.modelData
                    color: tabItem.active ? t.fg : t.muted
                }
                Rectangle {
                    visible: tabItem.active
                    anchors.horizontalCenter: lbl.horizontalCenter
                    y: 19 * root.u
                    width: lbl.width; height: Math.max(1, Math.round(root.u))
                    color: t.accent
                }
                MouseArea {
                    anchors.fill: parent; anchors.leftMargin: -6 * root.u; anchors.rightMargin: -6 * root.u
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.show(tabItem.index)
                }
            }
        }

        Label {
            x: 280 * root.u
            width: bar.width - x - 8 * root.u
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: -0.5 * root.u
            u: root.u; tok: t; textStyle: t.barlabel; color: t.muted
            text: root.activeTitle
            elide: Text.ElideRight
        }
    }
}

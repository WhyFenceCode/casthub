// Alt+Tab in the Cast Hub style (SPEC D22): a list like the leave screens' choices —
// number, app, window title, and the view (Console / Files / Desktop) it lives on.
import QtQuick
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.kwin as KWin
import "uikit"
import "uikit/Names.js" as Names

KWin.TabBoxSwitcher {
    id: tabBox
    currentIndex: list.currentIndex

    PlasmaCore.Dialog {
        id: dialog
        location: PlasmaCore.Types.Floating
        visible: tabBox.visible
        flags: Qt.ToolTip | Qt.X11BypassWindowManagerHint
        x: tabBox.screenGeometry.x + (tabBox.screenGeometry.width - mainItem.width) / 2
        y: tabBox.screenGeometry.y + (tabBox.screenGeometry.height - mainItem.height) / 2

        mainItem: Item {
            id: box
            readonly property real u: tabBox.screenGeometry.height / t.frameHeight
            readonly property real rowH: 30 * u
            width: Math.max(420 * u, Math.min(tabBox.screenGeometry.width * 0.6, 520 * u))
            height: header.height + Math.min(list.count || 1, 9) * rowH + 4 * u

            Tokens { id: t; dark: Kirigami.Theme.backgroundColor.hslLightness < 0.5 }

            Item {
                id: header
                width: parent.width; height: 28 * box.u
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 7 * box.u
                    Rectangle { width: 5 * box.u; height: width; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
                    Label { u: box.u; tok: t; textStyle: t.label; text: "Switch window" }
                }
                Label {
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    u: box.u; tok: t; textStyle: t.label; color: t.muted
                    text: (list.count || 0) + (list.count === 1 ? " window" : " windows")
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: Math.max(1, box.u); color: t.fg }
            }

            ListView {
                id: list
                anchors.top: header.bottom; anchors.topMargin: 4 * box.u
                width: parent.width; height: parent.height - header.height - 4 * box.u
                clip: true
                focus: true
                model: tabBox.model
                boundsBehavior: Flickable.StopAtBounds
                highlightMoveDuration: 0
                delegate: Item {
                    id: row
                    required property int index
                    required property string caption
                    required property bool minimized
                    required property var desktopName
                    readonly property bool active: ListView.isCurrentItem
                    readonly property string full: Names.captionFromTitle(caption, "\u0001")
                    readonly property int cut: full.indexOf("\u0001")
                    width: list.width; height: box.rowH
                    opacity: minimized ? 0.6 : 1

                    Rectangle {
                        visible: row.active
                        x: 0; y: 5 * box.u; width: 2 * Math.max(1, Math.round(box.u)); height: parent.height - 10 * box.u
                        color: t.accent
                    }
                    Label {
                        x: 12 * box.u; anchors.verticalCenter: parent.verticalCenter
                        u: box.u; tok: t; textStyle: t.label; color: t.muted
                        text: (row.index < 9 ? "0" : "") + (row.index + 1)
                    }
                    Label {
                        id: app
                        x: 38 * box.u; width: 70 * box.u; anchors.verticalCenter: parent.verticalCenter
                        u: box.u; tok: t; textStyle: t.label; color: row.active ? t.fg : t.muted
                        elide: Text.ElideRight
                        text: row.cut > 0 ? row.full.slice(0, row.cut) : ""
                    }
                    Label {
                        anchors.left: app.right; anchors.leftMargin: 10 * box.u
                        anchors.right: view.left; anchors.rightMargin: 10 * box.u
                        anchors.verticalCenter: parent.verticalCenter
                        u: box.u; tok: t; textStyle: t.body; color: row.active ? t.fg : t.muted
                        elide: Text.ElideMiddle
                        text: (row.minimized ? "(" : "") + (row.cut > 0 ? row.full.slice(row.cut + 1) : row.full) + (row.minimized ? ")" : "")
                    }
                    Label {
                        id: view
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        u: box.u; tok: t; textStyle: t.label; color: t.muted
                        text: row.desktopName || ""
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: Math.max(1, box.u); color: t.line; visible: row.index < list.count - 1 }
                    TapHandler { onTapped: { list.currentIndex = row.index; tabBox.model.activate(row.index) } }
                }
                Connections {
                    target: tabBox
                    function onCurrentIndexChanged() { list.currentIndex = tabBox.currentIndex }
                }
                Label {
                    visible: list.count === 0
                    anchors.centerIn: parent
                    u: box.u; tok: t; textStyle: t.label; color: t.muted
                    text: "No windows"
                }
            }
        }
    }
}

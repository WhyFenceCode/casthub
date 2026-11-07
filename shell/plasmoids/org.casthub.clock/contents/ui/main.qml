// "● 01:06" at the right of the top bar (design page 10), then "Log out" (owner request:
// Workstation needs a way out). Log out opens Plasma's own confirmation, which can also
// restart or shut down.
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.plasma.workspace.dbus as DBus
import "uikit"

PlasmoidItem {
    id: root
    preferredRepresentation: fullRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    readonly property real u: Math.max(1, height) / t.shellBar
    Tokens { id: t; dark: Kirigami.Theme.backgroundColor.hslLightness < 0.5 }

    property date now: new Date()
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = new Date() }
    function pad(n) { return n < 10 ? "0" + n : "" + n }
    function logout() {
        DBus.SessionBus.asyncCall({
            service: "org.kde.LogoutPrompt", path: "/LogoutPrompt",
            iface: "org.kde.LogoutPrompt", member: "promptLogout", arguments: []
        })
    }

    fullRepresentation: Item {
        Layout.minimumWidth: row.width + t.shellBarMargin * root.u
        Layout.preferredWidth: Layout.minimumWidth
        Layout.fillHeight: true
        Row {
            id: row
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: -0.5 * root.u
            x: 0
            spacing: 6 * root.u
            Rectangle { width: 5 * root.u; height: width; radius: width / 2; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
            Label { u: root.u; tok: t; textStyle: t.barlabel; text: root.pad(root.now.getHours()) + ":" + root.pad(root.now.getMinutes()) }
            Item { width: 12 * root.u; height: 1 }
            Label {
                id: out
                u: root.u; tok: t; textStyle: t.barlabel
                color: outArea.containsMouse ? t.fg : t.muted
                text: "Log out"
                Rectangle {   // accent underline on hover, like the tabs
                    visible: outArea.containsMouse
                    y: parent.height + 3 * root.u; width: parent.width; height: Math.max(1, root.u); color: t.accent
                }
            }
        }
        MouseArea {
            id: outArea
            x: row.x + out.x - 4 * root.u; width: out.width + 8 * root.u; height: parent.height
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.logout()
            Accessible.role: Accessible.Button
            Accessible.name: "Log out"
        }
    }
}

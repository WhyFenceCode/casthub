// Display-mode top bar: wordmark, "■ DISPLAY MODE", date, battery, Log out, rule.
// Same measurements as the login and lock screens' bars (main frame, 1080 x 675 units: centre
// line 23.5, rule 47, label type), so leaving Display mode doesn't make the bar jump.
import QtQuick
import "uikit"

Item {
    id: bar
    property real u: 1                       // main-frame unit: min(width / 1080, height / 675)
    property Tokens tok
    property real margin: 42
    property real modeX: 376
    property string modeText: "Display mode"
    property string dateText: ""
    property string logoutText: "Log out"
    property bool showLogout: false        // idle screen: a visible way out (owner request)
    signal logoutRequested()
    anchors.left: parent.left; anchors.right: parent.right
    height: 47 * u

    readonly property real mid: 23.5 * u

    Wordmark { x: bar.margin; y: bar.mid - height / 2; u: bar.u; tok: bar.tok }
    Row {
        x: bar.modeX; y: bar.mid - height / 2
        spacing: 8 * bar.u
        Rectangle { width: 6 * bar.u; height: width; anchors.verticalCenter: parent.verticalCenter; color: bar.tok.accent }
        Label { u: bar.u; tok: bar.tok; textStyle: bar.tok.label; text: bar.modeText }
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: bar.margin
        y: bar.mid - height / 2
        spacing: 14 * bar.u
        Label {
            u: bar.u; tok: bar.tok; textStyle: bar.tok.label; color: bar.tok.muted
            text: bar.dateText
        }
        Battery { u: bar.u; tok: bar.tok; anchors.verticalCenter: parent.verticalCenter }
        Label {
            id: logout
            visible: bar.showLogout
            u: bar.u; tok: bar.tok; textStyle: bar.tok.label
            color: logoutArea.containsMouse ? bar.tok.fg : bar.tok.muted
            text: bar.logoutText
            Rectangle { visible: logoutArea.containsMouse; y: parent.height + 3 * bar.u; width: parent.width; height: Math.max(1, bar.u); color: bar.tok.accent }
            MouseArea {
                id: logoutArea
                anchors.fill: parent; anchors.margins: -8 * bar.u
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: bar.logoutRequested()
            }
        }
    }
    Rectangle {
        x: bar.margin; y: 47 * bar.u
        width: bar.width - 2 * bar.margin; height: Math.max(1, Math.round(bar.u))
        color: bar.tok.fg
    }
}

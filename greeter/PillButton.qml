// Pill button. kind: "outline" (fg border), "faint" (line border), "solid" (fg fill, with arrow disc).
import QtQuick

Item {
    id: btn
    property string text
    property string kind: "outline"
    property real u: 1
    property QtObject tok
    signal clicked()

    readonly property bool solid: kind === "solid"
    implicitHeight: 39 * u
    implicitWidth: pill.width + (solid ? disc.width - 4 * u : 0)
    opacity: enabled ? 1 : 0.4

    Rectangle {
        id: pill
        height: parent.height
        width: caption.implicitWidth + 2 * 24 * btn.u
        radius: height / 2
        color: btn.solid ? btn.tok.fg : (area.containsMouse ? Qt.rgba(btn.tok.fg.r, btn.tok.fg.g, btn.tok.fg.b, 0.06) : "transparent")
        border.width: btn.solid ? 0 : Math.max(1, btn.u)
        border.color: btn.kind === "faint" ? btn.tok.line : btn.tok.fg

        Label {
            id: caption
            anchors.centerIn: parent
            text: btn.text
            u: btn.u
            tok: btn.tok
            style: btn.tok.label
            color: btn.solid ? btn.tok.bg : btn.tok.fg
        }
    }

    Rectangle {
        id: disc
        visible: btn.solid
        x: pill.width - 4 * btn.u
        width: btn.height
        height: btn.height
        radius: height / 2
        color: btn.tok.fg
        Text {
            anchors.centerIn: parent
            text: "→"
            color: btn.tok.bg
            font.family: btn.tok.fontText
            font.pixelSize: 14 * btn.u
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        enabled: btn.enabled
        onClicked: btn.clicked()
    }
}

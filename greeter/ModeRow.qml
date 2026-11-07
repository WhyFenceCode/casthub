// One row of the MODE list: number, title, subtitle, state square.
import QtQuick

Rectangle {
    id: row
    property int number: 1
    property string title
    property string subtitle
    property bool selected: false
    property bool available: true
    property real u: 1
    property Tokens tok
    signal picked()

    height: 51 * u
    color: selected ? tok.fg : (area.containsMouse ? Qt.rgba(tok.fg.r, tok.fg.g, tok.fg.b, 0.04) : "transparent")
    readonly property color ink: selected ? tok.bg : tok.fg

    Behavior on color { ColorAnimation { duration: row.tok.motionFast } }

    Label {
        x: 10 * row.u
        anchors.verticalCenter: parent.verticalCenter
        text: (row.number < 10 ? "0" : "") + row.number
        u: row.u; tok: row.tok; textStyle: row.tok.label
        color: row.selected ? row.tok.bg : row.tok.muted
    }

    Column {
        x: 44 * row.u
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1 * row.u
        opacity: row.available ? 1 : 0.45
        Label { text: row.title; u: row.u; tok: row.tok; textStyle: row.tok.title; color: row.ink }
        Label { text: row.subtitle; u: row.u; tok: row.tok; textStyle: row.tok.small; color: row.ink }
    }

    Rectangle {
        width: 7 * row.u
        height: width
        anchors.right: parent.right
        anchors.rightMargin: 9 * row.u
        anchors.verticalCenter: parent.verticalCenter
        color: row.selected ? row.tok.accent : "transparent"
        border.width: row.selected ? 0 : Math.max(1, row.u * 0.8)
        border.color: row.tok.line
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: row.picked()
    }
}

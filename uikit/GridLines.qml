// The faint 12-column grid behind every full-screen surface (design pages 1-13).
import QtQuick

Item {
    id: grid
    property real u: 1
    property Tokens tok
    anchors.fill: parent

    readonly property real margin: tok.gridMargin * u
    readonly property real gutter: tok.gridGutter * u
    readonly property real col: (width - 2 * margin - (tok.gridColumns - 1) * gutter) / tok.gridColumns
    function colX(i) { return margin + i * (col + gutter) }

    Repeater {
        model: grid.tok.gridColumns * 2
        Rectangle {
            required property int index
            x: Math.round(grid.colX(Math.floor(index / 2)) + (index % 2 ? grid.col : 0))
            width: Math.max(1, Math.round(grid.u))
            height: grid.height
            color: grid.tok.line
            opacity: 0.4
        }
    }
}

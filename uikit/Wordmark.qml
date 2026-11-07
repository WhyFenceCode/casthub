// "TORSTEN YOUNG." with the accent full stop.
import QtQuick

Row {
    id: mark
    property string text: "Torsten Young"
    property real u: 1
    property Tokens tok
    baselineOffset: first.baselineOffset
    Label { id: first; text: mark.text; u: mark.u; tok: mark.tok; textStyle: mark.tok.wordmark }
    Label { text: "."; u: mark.u; tok: mark.tok; textStyle: mark.tok.wordmark; color: mark.tok.accent }
}

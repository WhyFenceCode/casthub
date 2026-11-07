// Plasma's login splash (KSplash) for Workstation: the theme background and grid, the
// wordmark, and a status line with a progress rule that follows Plasma's start-up stages.
// brand/build.py copies this into each look-and-feel package with the variant set;
// tools/install-theme.sh puts a copy of uikit/ beside it.
import QtQuick
import "uikit"

Rectangle {
    id: root
    property int stage            // set by ksplashqml: 1 … 6
    readonly property bool dark: false // @variant
    readonly property real u: height / t.frameHeight
    readonly property real margin: t.gridMargin * u

    Tokens { id: t; dark: root.dark }
    color: t.bg

    GridLines { u: root.u; tok: t }

    Item {
        id: content
        anchors.fill: parent
        opacity: 0
        Component.onCompleted: opacity = 1
        Behavior on opacity { NumberAnimation { duration: t.motionNormal } }

        Wordmark {
            x: root.margin
            y: 23.5 * root.u - height / 2
            u: root.u; tok: t
        }

        Row {
            id: status
            x: root.margin
            y: root.height - 36 * root.u - height / 2
            spacing: 7 * root.u
            Rectangle {
                width: 6 * root.u; height: width
                anchors.verticalCenter: parent.verticalCenter
                color: t.accent
            }
            Label { text: "Starting workstation"; u: root.u; tok: t; textStyle: t.label }
        }

        // Progress rule along the bottom of the top bar, like the greeter's top rule.
        Rectangle {
            id: track
            x: root.margin; y: 47 * root.u
            width: root.width - 2 * root.margin; height: Math.max(1, root.u)
            color: t.line
            Rectangle {
                height: parent.height * 2
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width * Math.min(1, Math.max(0, root.stage) / 6)
                color: t.fg
                Behavior on width { NumberAnimation { duration: t.motionNormal; easing.type: Easing.OutCubic } }
            }
        }
    }
}

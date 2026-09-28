import QtQuick
import QtQuick.Controls

AbstractButton {
    id: control
    property string fieldKey
    property string caption
    property string description: ""
    width: parent.width
    height: Math.max(32, labels.implicitHeight + 8)
    checkable: true
    hoverEnabled: true
    checked: Boolean(appController.form[fieldKey])
    onToggled: appController.setField(fieldKey, checked)
    Accessible.name: caption
    Column {
        id: labels
        width: parent.width - 56; anchors.verticalCenter: parent.verticalCenter
        UiText { text: control.caption; color: Theme.text; font.pixelSize: 14 }
        UiText { visible: text.length > 0; width: parent.width; text: control.description; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
    }
    // Notion-style pill switch: accent when on, neutral track when off.
    Rectangle {
        width: 30; height: 18; radius: 9
        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        color: control.checked ? Theme.accent : control.hovered ? Theme.outline : Theme.border
        Behavior on color { ColorAnimation { duration: 90 } }
        Rectangle {
            x: control.checked ? 14 : 2; y: 2; width: 14; height: 14; radius: 7
            color: "#ffffff"
            Behavior on x { NumberAnimation { duration: 90 } }
        }
        border.width: control.activeFocus ? 1 : 0
        border.color: Theme.muted
    }
}

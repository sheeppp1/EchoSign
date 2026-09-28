import QtQuick
import QtQuick.Controls

Column {
    id: field
    property string fieldKey: ""
    property string caption: ""
    property bool secret: false
    property bool revealed: false
    property string hint: ""
    property Flickable viewport
    spacing: 6
    width: parent.width
    UiText { text: field.caption; color: Theme.muted; font.pixelSize: 13 }
    TextField {
        id: input
        objectName: "field_" + field.fieldKey
        width: parent.width
        height: 32
        text: appController.form[field.fieldKey] ?? ""
        echoMode: field.secret && !field.revealed ? TextInput.Password : TextInput.Normal
        passwordCharacter: "•"
        font.family: Theme.body
        font.pixelSize: 13
        renderType: Text.QtRendering
        selectByMouse: true
        color: Theme.text
        selectionColor: Theme.accentFill
        selectedTextColor: Theme.text
        leftPadding: 9
        rightPadding: field.secret ? 54 : 9
        onTextEdited: appController.setField(field.fieldKey, text)
        onAccepted: if (field.fieldKey === "password") appController.login(false)
        background: Rectangle {
            radius: 4
            color: Theme.inset
            border.width: 1
            border.color: appController.state.errorField === field.fieldKey ? Theme.red : input.activeFocus ? Theme.accent : Theme.outline
        }
        UiButton {
            visible: field.secret
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 52; height: 28
            text: field.revealed ? "隐藏" : "显示"
            onClicked: field.revealed = !field.revealed
        }
    }
    UiText { visible: field.hint.length > 0; text: field.hint; width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 12; color: Theme.muted }
    Connections {
        target: appController
        function onNavigateSettings(key) {
            if (key !== field.fieldKey) return
            Qt.callLater(function() {
                input.forceActiveFocus()
                let panel = field.viewport
                // Centre the invalid field so its caption and hint stay in view.
                if (panel) panel.contentY = Math.max(0, Math.min(field.mapToItem(panel.contentItem, 0, 0).y - (panel.height - field.height) / 2, panel.contentHeight - panel.height))
            })
        }
    }
}

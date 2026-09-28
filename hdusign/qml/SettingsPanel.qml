import QtQuick
import QtQuick.Controls

Flickable {
    id: panel
    property real documentWidth: Math.min(1120, width - 96)
    clip: true
    contentWidth: width
    contentHeight: fields.height + 72
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    // Notion settings section: heading with a hairline beneath it.
    component Heading: Column {
        property alias text: title.text
        width: parent.width; spacing: 8
        Item { width: 1; height: 20 }
        UiText { id: title; font.family: Theme.heading; font.pixelSize: 16; font.weight: Font.DemiBold }
        Rectangle { width: parent.width; height: 1; color: Theme.border }
        Item { width: 1; height: 2 }
    }

    Column {
        id: fields
        objectName: "settingsDocument"
        x: (panel.width - width) / 2; y: 24
        width: panel.documentWidth; spacing: 12
        Column {
            width: parent.width; spacing: 6
            UiText { id: settingsHeading; objectName: "settingsHeading"; text: "设置"; font.family: Theme.heading; font.pixelSize: 32; font.weight: Font.Bold; lineHeight: 1.2 }
            UiText { width: parent.width; text: "账号、声音来源、签到辅助与识别规则"; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.Wrap }
        }
        Row {
            spacing: 8
            UiButton { outline: true; text: appController.state.dark ? "浅色外观" : "深色外观"; iconName: appController.state.dark ? "sun" : "moon"; onClicked: appController.toggleTheme() }
            UiButton { outline: true; text: appController.state.dirty ? "保存设置" : "已保存"; iconName: appController.state.dirty ? "" : "check"; enabled: appController.state.dirty; tooltip: "Ctrl+S"; onClicked: appController.save() }
        }

        Heading { text: "直播账号" }
        Field { viewport: panel; caption: "学号 / 账号"; fieldKey: "username"; enabled: !appController.state.busy }
        Field { viewport: panel; caption: "密码"; fieldKey: "password"; secret: true; enabled: !appController.state.busy }
        Row {
            spacing: 8
            UiButton { text: "登录并读取课程"; primary: true; enabled: !appController.state.busy; onClicked: appController.login(false) }
            UiButton { text: "单独登录直播账号"; outline: true; enabled: !appController.state.busy; onClicked: appController.login(true) }
        }

        Heading { text: "声音来源" }
        Toggle { caption: "后台直播音频"; description: "选择课程后直接接收直播声音，关闭时使用电脑声音。"; fieldKey: "live" }
        Field { viewport: panel; caption: "课程网址（选填）"; fieldKey: "url"; hint: "选课后自动填写，也可粘贴课程链接。" }
        UiButton { text: "打开课程页面 ↗"; outline: true; onClicked: appController.openCourse() }

        Heading { text: "签到辅助" }
        Toggle { caption: "自动签到"; description: "确认签到码后自动在签到页面提交。"; fieldKey: "autoSign" }
        Row {
            width: parent.width; spacing: 12
            Field { width: (parent.width - 12) / 2; viewport: panel; caption: "纬度"; fieldKey: "latitude" }
            Field { width: (parent.width - 12) / 2; viewport: panel; caption: "经度"; fieldKey: "longitude" }
        }
        UiText { width: parent.width; text: appController.state.locationHint; wrapMode: Text.Wrap; color: Theme.muted; font.pixelSize: 13 }
        Row {
            spacing: 8
            UiButton { text: "获取当前位置"; outline: true; enabled: !appController.state.busy; onClicked: appController.runAction("locate") }
            UiButton { text: "重新登录签到"; outline: true; enabled: !appController.state.busy; onClicked: appController.runAction("signin") }
        }

        Heading { text: "识别与通知" }
        Toggle { caption: "语义辅助识别"; description: "使用本地语义模型辅助判断签到提示。"; fieldKey: "semantic" }
        Column {
            width: parent.width; spacing: 6
            UiText { text: "签到关键词（每行一项）"; color: Theme.muted; font.pixelSize: 13 }
            ScrollView {
                width: parent.width; height: 132
                TextArea {
                    objectName: "field_rules"
                    text: appController.form.rules
                    onTextChanged: if (activeFocus) appController.setField("rules", text)
                    color: Theme.text; font.family: Theme.body; font.pixelSize: 13
                    wrapMode: TextEdit.Wrap; selectByMouse: true
                    selectionColor: Theme.accentFill; selectedTextColor: Theme.text
                    background: Rectangle { color: Theme.inset; radius: 4; border.width: 1; border.color: parent.activeFocus ? Theme.accent : Theme.outline }
                }
            }
        }
        Field { viewport: panel; caption: "企业微信 Webhook"; fieldKey: "webhook"; secret: true }
        UiButton { text: "测试推送"; outline: true; enabled: !appController.state.busy; onClicked: appController.runAction("webhook") }
        Item { width: 1; height: 12 }
        UiText { text: appController.state.version; color: Theme.subtle; font.pixelSize: 11 }
    }
}

import sys

import darkdetect
from PySide6.QtCore import QSize, QThread, Signal, QTimer, QRect, QUrl
from PySide6.QtGui import (
    QIcon,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QDesktopServices,
    QColor,
    Qt,
)
from PySide6.QtWidgets import QApplication

from app.components import CustomSystemTrayIcon
# from app.view.components import AddTaskOptionDialog
# from app.view.components import CustomSystemTrayIcon
# from app.view.pages import SettingPage
# from app.view.pages import TaskPage
from app.supports.utils import (
    bringWindowToTop,
    showMessageBox,
    isGreaterEqualWin10,
    isLessThanWin10,
    checkUpdate
)

from loguru import logger
from qfluentwidgets import FluentIcon as FIF, setTheme, Theme, isDarkTheme
from qfluentwidgets import NavigationItemPosition, MSFluentWindow

from app.view.components.splash_screen import CustomSplashScreen
from app.supports.config import cfg, Headers, FEEDBACK_URL
from app.supports.signal_bus import signalBus


def updateFrameless(self):
    stayOnTop = (
        Qt.WindowType.WindowStaysOnTopHint
        if self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        else 0
    )
    self.setWindowFlags(Qt.WindowType.FramelessWindowHint | stayOnTop)

    self.windowEffect.enableBlurBehindWindow(self.winId())
    self.windowEffect.addWindowAnimation(self.winId())
    self.windowEffect.addShadowEffect(self.winId())


class ThemeChangedListener(QThread):
    themeChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        darkdetect.listener(self.themeChanged.emit)


class MainWindow(MSFluentWindow):
    def __init__(self, silence=False):
        super().__init__()

        self.setMicaEffectEnabled(False)  # 自实现背景管理

        self.initWindow()

        if not silence:
            self.show()

        self.initSubpages()

        # 允许拖拽
        self.setAcceptDrops(True)

        # 自定义主题信号连接
        self.themeChangedListener = None

        self._onCustomThemeModeChanged(cfg.customThemeMode.value)
        cfg.customThemeMode.valueChanged.connect(self._onCustomThemeModeChanged)

        signalBus.appErrorSignal.connect(self.onAppError)
        signalBus.showMainWindowSignal.connect(lambda: bringWindowToTop(self))

        # 设置背景特效
        self.applyBackgroundEffectByCfg()

        # 启动剪切板监听器
        self.clipboard = None
        if cfg.enableClipboardListener.value:
            self.runClipboardListener()

        # 创建托盘
        self.tray = CustomSystemTrayIcon(self)
        self.tray.show()

        # 检查更新
        if cfg.checkUpdateAtStartUp.value:
            checkUpdate(self)

        self.splashScreen.finish()

    def systemTitleBarRect(self, size: QSize) -> QRect:
        """重写 macOS 三大件到左上角"""
        return QRect(0, 0 if self.isFullScreen() else 9, 75, size.height())

    def _onCustomThemeModeChanged(self, value: str):
        if value == "System":
            # 创建检测主题色更改线程
            self.themeChangedListener = ThemeChangedListener()
            self.themeChangedListener.themeChanged.connect(self.toggleTheme)
            self.themeChangedListener.start()
            setTheme(Theme.AUTO, save=False)
        else:
            if self.themeChangedListener is not None:
                self.themeChangedListener.terminate()
                self.themeChangedListener.deleteLater()
                self.themeChangedListener = None
            setTheme(Theme.DARK if value == "Dark" else Theme.LIGHT, save=False)

        self.applyBackgroundEffectByCfg()

    def runClipboardListener(self):
        if not self.clipboard:
            self.clipboard = QApplication.clipboard()
            self.clipboard.dataChanged.connect(self.__clipboardChanged)

    def stopClipboardListener(self):
        assert self.clipboard is not None
        self.clipboard.dataChanged.disconnect(self.__clipboardChanged)
        self.clipboard.deleteLater()
        self.clipboard = None

    def toggleTheme(self, callback: str):
        if callback == "Dark":  # MS 特性，需要重试
            setTheme(Theme.DARK, save=False, lazy=True)
            if cfg.backgroundEffect.value in ["Mica", "MicaBlur", "MicaAlt"]:
                QTimer.singleShot(500, self.applyBackgroundEffectByCfg)

        elif callback == "Light":
            setTheme(Theme.LIGHT, save=False, lazy=True)

        self.applyBackgroundEffectByCfg()

    def _normalBackgroundColor(self):
        if self.styleSheet() == "":  # 没有启动背景效果, 不透明
            return (
                self._darkBackgroundColor
                if isDarkTheme()
                else self._lightBackgroundColor
            )

        return QColor(0, 0, 0, 0)

    def applyBackgroundEffectByCfg(self):
        if sys.platform != "win32":
            return

        self.windowEffect.removeBackgroundEffect(self.winId())

        theme = cfg.customThemeMode.value
        isDark = darkdetect.isDark() if theme == "System" else (theme == "Dark")
        effect = cfg.backgroundEffect.value

        self.setStyleSheet("background-color: transparent" if effect != "None" else "")

        if effect == "Acrylic":
            color = "00000030" if isDark else "FFFFFF30"
            self.windowEffect.setAcrylicEffect(self.winId(), color)
        elif effect == "Mica":
            self.windowEffect.setMicaEffect(self.winId(), isDark)
        elif effect == "MicaBlur":
            from ctypes import byref, c_int

            self.windowEffect.setMicaEffect(self.winId(), isDark, isBlur=True)
            self.windowEffect.DwmSetWindowAttribute(
                self.winId(), 38, byref(c_int(3)), 4
            )
        elif effect == "MicaAlt":
            self.windowEffect.setMicaEffect(self.winId(), isDark, isAlt=True)
        elif effect == "Aero":
            self.windowEffect.setAeroEffect(self.winId())
            if isLessThanWin10():
                self.titleBar.closeBtn.hide()
                self.titleBar.minBtn.hide()
                self.titleBar.maxBtn.hide()
        elif effect == "None" and isLessThanWin10():
            self.titleBar.closeBtn.show()
            self.titleBar.minBtn.show()
            self.titleBar.maxBtn.show()

    def initSubpages(self):
        # self.taskInterface = TaskPage(self)
        # self.settingInterface = SettingPage(self)
        # # add navigation items
        # self.addSubInterface(self.taskInterface, FIF.DOWNLOAD, self.tr("任务列表"))
        # self.navigationInterface.addItem(
        #     routeKey="addTaskButton",
        #     text=self.tr("新建任务"),
        #     selectable=False,
        #     icon=FIF.ADD,
        #     onClick=lambda: self.showAddTaskDialog(),  # 否则会传奇怪的参数
        #     position=NavigationItemPosition.TOP,
        # )
        #
        # # self.addSubInterface(self.debugInterface, FIF.DEVELOPER_TOOLS, "调试信息")
        # self.addSubInterface(
        #     self.settingInterface,
        #     FIF.SETTING,
        #     self.tr("设置"),
        #     position=NavigationItemPosition.BOTTOM,
        # )
        ...

    def initWindow(self):
        if cfg.geometry.value == "Default":
            self.resize(960, 780)
            desktop = QApplication.screens()[0].availableGeometry()
            w, h = desktop.width(), desktop.height()
            self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        else:
            try:
                self.setGeometry(cfg.geometry.value)
            except Exception as e:
                logger.error(f"Failed to restore geometry: {e}")
                cfg.set(cfg.geometry, "Default")

                self.resize(960, 780)
                desktop = QApplication.screens()[0].availableGeometry()
                w, h = desktop.width(), desktop.height()
                self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)

        self.setWindowIcon(QIcon(":/image/logo.png"))
        self.setWindowTitle("Ghost Downloader")

        if sys.platform == "darwin":
            self.titleBar.hBoxLayout.insertSpacing(0, 58)

        # create splash screen
        self.splashScreen = CustomSplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        QApplication.processEvents()

    def onAppError(self, message: str):
        """app error slot"""
        QApplication.clipboard().setText(message)
        showMessageBox(
            self,
            self.tr("意料之外的错误!"),
            self.tr("错误消息已写入粘贴板和日志。是否报告?"),
            True,
            lambda: QDesktopServices.openUrl(QUrl(FEEDBACK_URL)),
        )

    def showAddTaskDialog(self, text: str = "", headers: dict = {}):
        # AddTaskOptionDialog.showAddTaskOptionDialog(text, self, headers)
        ...

    def closeEvent(self, event):
        # 拦截关闭事件，隐藏窗口而不是退出
        event.ignore()
        # 保存窗口位置，最大化时不保存
        if not self.isMaximized():
            cfg.set(cfg.geometry, self.geometry())

        self.hide()

    def nativeEvent(self, eventType, message):
        """处理窗口重复打开事件"""
        if eventType == "windows_generic_MSG":
            from ctypes.wintypes import MSG

            msg = MSG.from_address(message.__int__())

            # WIN_USER = 1024
            if msg.message == 1024 + 1:
                bringWindowToTop(self)
                return True, 0

        return super().nativeEvent(eventType, message)  # type: ignore

    def dragEnterEvent(self, event: QDragEnterEvent):
        logger.debug(f"Get event: {event}")
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def __setUrlsAndShowAddTaskMsg(self, text):
        QTimer.singleShot(10, lambda: self.showAddTaskDialog(text))

    def dropEvent(self, event: QDropEvent):
        mime = event.mimeData()
        if mime.hasUrls():
            urls = mime.urls()
            text = "\n".join(
                [url.toString() for url in urls if url.toString().startswith("http")]
            )
        elif mime.hasText():
            text = mime.text()
        else:
            return

        if text:
            self.__setUrlsAndShowAddTaskMsg(text)

        event.accept()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            if self.clipboard is not None:
                self.__setUrlsAndShowAddTaskMsg(self.clipboard.text())
        else:
            super().keyPressEvent(event)

    def __checkUrl(self, url):
        # try:
        #     _, fileName, __ = getLinkInfo(url, Headers)
        #     if fileName.lower().endswith(tuple(attachmentTypes.split())):
        #         return url
        #     return
        # except ValueError:
        #     return False
        ...

    def __clipboardChanged(self):
        assert self.clipboard is not None
        try:
            mime = self.clipboard.mimeData()
            if mime.data("application/x-gd3-copy") != b"":  # if not empty
                logger.debug("Clipboard changed from software itself")
                return  # 当剪贴板事件来源于软件本身时, 不执行后续代码
            if mime.hasText():
                urls = (
                    mime.text().lstrip().rstrip().split("\n")
                )  # .strip()主要去两头的空格
            elif mime.hasUrls():
                urls = [url.toString() for url in mime.urls()]
            else:
                return

            results = []

            for url in urls:
                if self.__checkUrl(url):
                    results.append(url)
                else:
                    logger.debug(f"Invalid url: {url}")

            if not results:
                return

            results = "\n".join(results)

            logger.debug(f"Clipboard changed: {results}")
            bringWindowToTop(self)
            self.__setUrlsAndShowAddTaskMsg(results)
        except Exception as e:
            logger.warning(f"Failed to check clipboard: {e}")


if isGreaterEqualWin10():  # Monkey Patch
    MainWindow.updateFrameless = updateFrameless

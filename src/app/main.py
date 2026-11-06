# Need calling multiprocessing.freeze_support to support multiprocessing in frozen app
# Divert the program flow in worker sub-process as soon as possible,
# before importing heavy-weight modules.
if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()

import asyncio
import logging
import os
import platform
import signal
import sys
import shutil
from logging.handlers import RotatingFileHandler

import psutil
import qasync
from anyio import Event, create_task_group, sleep
from PyQt6.QtCore import (QObject, QSettings, Qt, QtMsgType, QUrl, pyqtSlot,
                          qInstallMessageHandler, QCoreApplication, QStandardPaths)
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QMenu, QSplashScreen,
                             QStackedLayout, QSystemTrayIcon, QWidget)

_logger = logging.getLogger(__name__)


def init_log(_logger, config):
    if config.log.level == "DEBUG":
        os.environ["QT_LOGGING_RULES"] = "qt.webenginecontext.debug=true"

    def qt_message_handler(mode, context, message):

        position = "[QT] line: %d, func: %s(), file: %s\n" % (
            context.line,
            context.function,
            context.file,
        )
        msg = "[QT] %s\n" % message

        if mode == QtMsgType.QtInfoMsg:
            _logger.info(msg)
            _logger.info(position)
        elif mode == QtMsgType.QtWarningMsg:
            _logger.warning(msg)
            _logger.warning(position)
        elif mode == QtMsgType.QtCriticalMsg:
            _logger.error(msg)
            _logger.error(position)
        elif mode == QtMsgType.QtFatalMsg:
            _logger.fatal(msg)
            _logger.fatal(position)
        else:
            _logger.debug(msg)
            _logger.debug(position)

    qInstallMessageHandler(qt_message_handler)

    log_file = os.path.join(config.log.dir, "main.log")
    file_handler = RotatingFileHandler(
        log_file,
        encoding="utf-8",
        delay=True,
        maxBytes=config.log.max_bytes,
        backupCount=config.log.backup_count,
    )
    file_handler.setFormatter(
        logging.Formatter(
            "[{asctime}] [{levelname:<8}] {name}: {message}",
            "%Y-%m-%d %H:%M:%S",
            style="{",
        )
    )
    _logger.addHandler(file_handler)


class CustomWebEnginePage(QWebEnginePage):

    def createWindow(self, _type):
        page = CustomWebEnginePage(self)
        page.urlChanged.connect(self.open_browser)
        return page

    def open_browser(self, url):
        page = self.sender()
        QDesktopServices.openUrl(url)
        page.deleteLater()


class WebUICallable(QObject):
    def __init__(self, crynux_app):
        super().__init__()
        self.crynux_app = crynux_app
        self.settings = QSettings("crynux.io", "crynux_node")

    @pyqtSlot(str, result="QString")
    def get_settings_item(self, key):
        _logger.debug("reading settings item: " + key)
        self.settings.beginGroup("NodeWindow")
        value = self.settings.value(key, "")
        self.settings.endGroup()
        _logger.debug(value)

        return value

    @pyqtSlot(str, str)
    def set_settings_item(self, key, value):
        _logger.debug("writing settings item: " + key + ": " + str(value))
        self.settings.beginGroup("NodeWindow")
        self.settings.setValue(key, value)
        self.settings.endGroup()

    @pyqtSlot()
    def hide_window(self):
        self.crynux_app.hide()


class CrynuxApp(QWidget):

    def __init__(self):
        super().__init__()
        self.webpage = None
        self.webview = None
        self.webui_callable = WebUICallable(self)

        _logger.debug("Initializing Application UI...")
        self.initializing = True
        self.init_ui()
        _logger.debug("Application UI initialized")

    def init_ui(self):
        stack = QStackedLayout(self)
        self.webview = QWebEngineView()
        self.webpage = CustomWebEnginePage()
        self.webview.setPage(self.webpage)

        web_channel = QWebChannel(self)
        web_channel.registerObject("backend", self.webui_callable)
        self.webpage.setWebChannel(web_channel)

        settings = self.webpage.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True
        )

        stack.addWidget(self.webview)
        self.setLayout(stack)
        self.setGeometry(300, 300, 1300, 800)
        self.setWindowTitle("Crynux Node")

    def delayed_show(self):
        self.webview.load(QUrl("http://localhost:7412"))
        self.show()

    def show_recreate_window(self):
        if self.initializing:
            return

        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized
            | Qt.WindowState.WindowActive
        )
        self.activateWindow()

    def closeEvent(self, event):
        self.webpage.runJavaScript("window.appVM.closeWindow()")
        event.ignore()


def main():
    from crynux_server import config as crynux_config
    from crynux_server.run import CrynuxRunner

    _logger.debug("Starting Crynux node...")

    crynux_cfg = crynux_config.get_config()
    _logger.debug("Log file loaded")

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(os.path.join(crynux_cfg.resource_dir, "icon.ico")))
    app.setQuitOnLastWindowClosed(False)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    geometry = app.primaryScreen().availableGeometry()

    pixmap = QPixmap(os.path.join(crynux_cfg.resource_dir, "splash.png")).scaledToWidth(
        int(geometry.width() / 4), mode=Qt.TransformationMode.SmoothTransformation
    )

    splash_screen = QSplashScreen(pixmap=pixmap)
    splash_screen.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    _logger.debug("show splash screen")

    tray = QSystemTrayIcon()
    tray.setIcon(QIcon(os.path.join(crynux_cfg.resource_dir, "icon.ico")))
    tray.setVisible(True)

    tray_menu = QMenu()
    tray_menu_dashboard = QAction("Dashboard")
    tray_menu_discord = QAction("Crynux Discord")
    tray_menu_exit = QAction("Exit")

    tray_menu.addAction(tray_menu_dashboard)
    tray_menu.addAction(tray_menu_discord)
    tray_menu.addSeparator()
    tray_menu.addAction(tray_menu_exit)

    tray.setContextMenu(tray_menu)

    crynux_app = CrynuxApp()

    init_log(_logger, crynux_cfg)

    async def _main():

        splash_screen.show()

        _logger.debug("Creating runner and crynux_app")
        runner = CrynuxRunner()

        should_exit = False
        exit_event = Event()

        def set_should_exit(*_args):
            nonlocal should_exit

            should_exit = True
            _logger.debug("set should exit")

        signal.signal(signal.SIGINT, set_should_exit)
        signal.signal(signal.SIGTERM, set_should_exit)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, set_should_exit)

        async def check_should_exit():
            while not should_exit:
                await sleep(0.1)
            exit_event.set()
            _logger.debug("set exit event")

        async def wait_for_exit():
            await exit_event.wait()
            _logger.debug("exit event is set")
            await runner.stop()
            _logger.debug("runner stop")

        def system_tray_action(reason):
            if (
                platform.system() == "Windows"
                and reason != QSystemTrayIcon.ActivationReason.Context
            ):
                crynux_app.show_recreate_window()

        def go_to_discord():
            QDesktopServices.openUrl(QUrl("https://discord.gg/JRkuY9FW49"))

        tray.activated.connect(system_tray_action)
        tray_menu_dashboard.triggered.connect(crynux_app.show_recreate_window)
        tray_menu_discord.triggered.connect(go_to_discord)
        tray_menu_exit.triggered.connect(set_should_exit)

        def app_state_changed(reason):
            if platform.system() == "Darwin" and reason == Qt.ApplicationState.ApplicationActive:
                if app.activeWindow() is None:
                    crynux_app.show_recreate_window()

        app.applicationStateChanged.connect(app_state_changed)

        async with create_task_group() as tg:
            _logger.debug("Starting init task")
            tg.start_soon(check_should_exit)
            tg.start_soon(wait_for_exit)
            await tg.start(runner.run)
            await sleep(3.5)
            crynux_app.initializing = False
            _logger.debug("Starting the user interface")
            crynux_app.delayed_show()
            splash_screen.finish(crynux_app.activateWindow())

        _logger.debug("app _main finish")

    try:
        with loop:
            loop.run_until_complete(_main())
        _logger.debug("app quit")
    except asyncio.CancelledError as e:
        _logger.debug("app stopped for being cancelled")
        _logger.debug(e, exc_info=True)
    except Exception as e:
        _logger.debug("app stopped by exception")
        _logger.debug(e, exc_info=True)
    finally:
        proc = psutil.Process(os.getpid())
        for p in proc.children(recursive=True):
            try:
                _logger.info(f"Kill process: {p.ppid()}, {p.cmdline()}")
                p.kill()
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                _logger.error(e)

        proc.kill()


if __name__ == "__main__":
    handler = logging.StreamHandler()
    dt_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(
        "[{asctime}] [{levelname:<8}] {name}: {message}", dt_fmt, style="{"
    )
    handler.setFormatter(formatter)
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG)

    from crynux_server import config as crynux_config

    if getattr(sys, "frozen", False):
        app_path = os.path.dirname(sys.executable)
        system_name = platform.system()

        if system_name == "Darwin":
            resdir = os.path.join(os.path.dirname(app_path), "Resources")
            QCoreApplication.setOrganizationName("crynux.io")
            QCoreApplication.setApplicationName("Crynux Node")
            app_support_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            os.makedirs(app_support_dir, exist_ok=True)
            os.makedirs(os.path.join(app_support_dir, "tmp"), exist_ok=True)

            config_path = os.path.join(app_support_dir, "config", "config.yml")
            if not os.path.exists(config_path):
                source_config_path = os.path.join(resdir, "data", "config", "config.yml")
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                shutil.copy2(source_config_path, config_path)

            crynux_config.set_data_dir(app_support_dir)
            config_file_path = crynux_config.config_file_path()

            _logger.debug(f"Config file path on mac: {config_file_path}")

            cfg = crynux_config.get_config()
            cfg.web_dist = os.path.join(resdir, "webui/dist")
            cfg.resource_dir = os.path.join(resdir, "res")
            crynux_config.set_config(cfg)

        elif system_name == "Windows":
            crynux_config.set_data_dir("data")
            config_file_path = crynux_config.config_file_path()

            _logger.debug(f"Config file path on windows: {config_file_path}")
        else:
            error = RuntimeError(f"Unsupported platform: {system_name}")
            _logger.error(error)
            raise error

    else:
        # start from source
        index = __file__.rfind(os.path.sep + "src")
        root_dir = __file__[:index]

        crynux_config.set_data_dir(root_dir)
        config_file_path = crynux_config.config_file_path()

        _logger.debug(f"Config file path from source: {config_file_path}")

    config_file_path = crynux_config.config_file_path()
    _logger.info(f"Start Crynux Node from: {config_file_path}")

    main()

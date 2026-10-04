import sys
import os
import shutil
import time
import win32com.client
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QProgressBar, QFileDialog, QGraphicsDropShadowEffect, QStackedWidget)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import QIcon, QFont, QColor
from PyQt6.QtWebEngineWidgets import QWebEngineView

class InstallThread(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, source_dir, target_dir):
        super().__init__()
        self.source_dir = source_dir
        self.target_dir = target_dir

    def run(self):
        try:
            self.status.emit("Preparing installation...")
            self.progress.emit(10)
            time.sleep(1) # Let user see the text

            if os.path.exists(self.target_dir):
                self.status.emit("Removing old version...")
                shutil.rmtree(self.target_dir, ignore_errors=True)
            
            self.progress.emit(20)
            self.status.emit("Copying files... This might take a minute.")
            
            # Walk directory to calculate size or just use a simple copy tree
            if not os.path.exists(self.target_dir):
                os.makedirs(self.target_dir)

            total_files = sum([len(files) for r, d, files in os.walk(self.source_dir)])
            copied = 0
            
            for src_dir, dirs, files in os.walk(self.source_dir):
                dst_dir = src_dir.replace(self.source_dir, self.target_dir, 1)
                if not os.path.exists(dst_dir):
                    os.makedirs(dst_dir)
                for file_ in files:
                    src_file = os.path.join(src_dir, file_)
                    dst_file = os.path.join(dst_dir, file_)
                    shutil.copy2(src_file, dst_file)
                    copied += 1
                    if total_files > 0:
                        prog = 20 + int((copied / total_files) * 60)
                        if prog % 5 == 0:  # throttle signals
                            self.progress.emit(prog)

            self.progress.emit(80)
            self.status.emit("Creating shortcuts...")
            
            # Create Desktop Shortcut
            exe_path = os.path.join(self.target_dir, 'NIGHTFALLEvo.exe')
            if os.path.exists(exe_path):
                shell = win32com.client.Dispatch("WScript.Shell")
                
                # Dynamically resolve Desktop path (handles OneDrive, moved folders, etc.)
                desktop = shell.SpecialFolders("Desktop")
                shortcut_path = os.path.join(desktop, "NIGHTFALL Evo.lnk")
                
                try:
                    shortcut = shell.CreateShortCut(shortcut_path)
                    shortcut.Targetpath = exe_path
                    shortcut.WorkingDirectory = self.target_dir
                    shortcut.IconLocation = os.path.join(self.target_dir, 'assets', 'NIGHTFALL_Lite_Logo.ico')
                    shortcut.WindowStyle = 1 # Normal window
                    shortcut.save()
                except Exception as e:
                    print(f"Failed to create desktop shortcut: {e}")
                
                # Start menu (dynamically resolve Programs path)
                try:
                    start_menu = shell.SpecialFolders("Programs")
                    shortcut_path_sm = os.path.join(start_menu, "NIGHTFALL Evo.lnk")
                    shortcut_sm = shell.CreateShortCut(shortcut_path_sm)
                    shortcut_sm.Targetpath = exe_path
                    shortcut_sm.WorkingDirectory = self.target_dir
                    shortcut_sm.IconLocation = os.path.join(self.target_dir, 'assets', 'NIGHTFALL_Lite_Logo.ico')
                    shortcut_sm.WindowStyle = 1
                    shortcut_sm.save()
                except Exception as e:
                    print(f"Failed to create start menu shortcut: {e}")

            self.progress.emit(100)
            self.status.emit("Installation Complete!")
            time.sleep(0.5)
            self.finished.emit()
            
        except Exception as e:
            self.error.emit(str(e))

class InstallWizard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NIGHTFALL Evo - Setup")
        self.resize(800, 500)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Background Container
        self.bg_container = QWidget(self)
        self.bg_container.resize(self.size())
        
        # Web Engine View for Orb
        self.web_view = QWebEngineView(self.bg_container)
        self.web_view.resize(self.size())
        
        # Disable web view background to blend with transparent main window if needed
        self.web_view.page().setBackgroundColor(Qt.GlobalColor.transparent)

        # Find assets folder
        if hasattr(sys, '_MEIPASS'):
            self.base_dir = sys._MEIPASS
        else:
            self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        orb_path = os.path.join(self.base_dir, 'assets', 'web_background', 'index.html')
        if os.path.exists(orb_path):
            self.web_view.setUrl(QUrl.fromLocalFile(orb_path))

        # Overlay UI
        self.overlay = QWidget(self)
        self.overlay.resize(self.size())
        overlay_layout = QVBoxLayout(self.overlay)
        overlay_layout.setContentsMargins(50, 50, 50, 50)

        # Content Box
        self.content_box = QWidget()
        self.content_box.setStyleSheet("""
            QWidget {
                background-color: rgba(20, 20, 20, 200);
                border-radius: 15px;
                border: 1px solid rgba(255, 255, 255, 30);
            }
            QLabel {
                background: transparent;
                border: none;
                color: white;
            }
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a73e8, stop:1 #4285f4);
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4285f4, stop:1 #1a73e8);
            }
            QPushButton:disabled {
                background-color: #555;
                color: #aaa;
            }
            QProgressBar {
                border: none;
                background-color: rgba(255, 255, 255, 20);
                border-radius: 5px;
                text-align: center;
                color: white;
                height: 15px;
            }
            QProgressBar::chunk {
                background-color: #4285f4;
                border-radius: 5px;
            }
        """)
        box_layout = QVBoxLayout(self.content_box)
        
        # Title
        title = QLabel("NIGHTFALL Evo Setup")
        title.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_layout.addWidget(title)

        self.stacked_widget = QStackedWidget()
        box_layout.addWidget(self.stacked_widget)
        
        # PAGE 1: Welcome & Path
        self.page1 = QWidget()
        self.page1.setStyleSheet("background: transparent; border: none;")
        p1_layout = QVBoxLayout(self.page1)
        
        desc = QLabel("Welcome to the NIGHTFALL Evo Setup Wizard.\nClick Install to continue.")
        desc.setFont(QFont("Segoe UI", 12))
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p1_layout.addWidget(desc)
        
        # Default Path
        self.install_path = os.path.join(os.environ.get('LOCALAPPDATA', os.environ['USERPROFILE']), 'NIGHTFALL_Evo')
        path_label = QLabel(f"Destination Folder: {self.install_path}")
        path_label.setFont(QFont("Segoe UI", 10))
        path_label.setStyleSheet("color: #aaa;")
        path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p1_layout.addWidget(path_label)

        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.close)
        self.btn_install = QPushButton("Install")
        self.btn_install.clicked.connect(self.start_installation)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_install)
        btn_layout.addStretch()
        p1_layout.addLayout(btn_layout)
        
        self.stacked_widget.addWidget(self.page1)

        # PAGE 2: Installing
        self.page2 = QWidget()
        self.page2.setStyleSheet("background: transparent; border: none;")
        p2_layout = QVBoxLayout(self.page2)
        
        self.lbl_status = QLabel("Preparing to install...")
        self.lbl_status.setFont(QFont("Segoe UI", 12))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p2_layout.addWidget(self.lbl_status)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        p2_layout.addWidget(self.progress_bar)
        
        self.stacked_widget.addWidget(self.page2)

        # PAGE 3: Finished
        self.page3 = QWidget()
        self.page3.setStyleSheet("background: transparent; border: none;")
        p3_layout = QVBoxLayout(self.page3)
        
        lbl_finish = QLabel("Installation Complete!")
        lbl_finish.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        lbl_finish.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p3_layout.addWidget(lbl_finish)
        
        btn_finish = QPushButton("Launch NIGHTFALL Evo")
        btn_finish.clicked.connect(self.launch_app)
        
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)

        btn_layout_finish = QHBoxLayout()
        btn_layout_finish.addStretch()
        btn_layout_finish.addWidget(btn_close)
        btn_layout_finish.addWidget(btn_finish)
        btn_layout_finish.addStretch()
        
        p3_layout.addLayout(btn_layout_finish)
        
        self.stacked_widget.addWidget(self.page3)

        overlay_layout.addWidget(self.content_box)
        layout.addWidget(self.overlay)

        # Shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 0)
        self.content_box.setGraphicsEffect(shadow)
        
    def resizeEvent(self, event):
        self.bg_container.resize(self.size())
        self.web_view.resize(self.size())
        self.overlay.resize(self.size())
        super().resizeEvent(event)

    def start_installation(self):
        self.stacked_widget.setCurrentIndex(1)
        source_dir = os.path.join(self.base_dir, 'NIGHTFALLEvo')
        if not os.path.exists(source_dir):
            self.lbl_status.setText(f"Error: Payload missing.\n{source_dir}")
            return
            
        self.thread = InstallThread(source_dir, self.install_path)
        self.thread.progress.connect(self.progress_bar.setValue)
        self.thread.status.connect(self.lbl_status.setText)
        self.thread.finished.connect(self.on_finished)
        self.thread.error.connect(self.on_error)
        self.thread.start()
        
    def on_finished(self):
        self.stacked_widget.setCurrentIndex(2)
        
    def on_error(self, msg):
        self.lbl_status.setText(f"Installation failed:\n{msg}")
        self.lbl_status.setStyleSheet("color: #ff4444;")
        
    def launch_app(self):
        exe_path = os.path.join(self.install_path, 'NIGHTFALLEvo.exe')
        if os.path.exists(exe_path):
            os.startfile(exe_path)
        self.close()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    wizard = InstallWizard()
    wizard.show()
    sys.exit(app.exec())

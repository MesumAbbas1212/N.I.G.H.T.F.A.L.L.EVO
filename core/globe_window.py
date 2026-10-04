"""
NIGHTFALL 3D HoloGlobe Window Controller.
Hosts the WebGL 3D Earth, Great-Circle route visualizer, and live flight radar inside a modern PyQt6 window.
"""

import os
import json
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

# Hardware acceleration & WebGL flags for smooth 180fps+ rendering in Chromium
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist --enable-accelerated-2d-canvas --enable-webgl --enable-webgl2-compute-context --disable-frame-rate-limit --disable-gpu-vsync --num-raster-threads=4 --use-angle=d3d11 --disable-gpu-driver-bug-workarounds"
)

from PyQt6.QtCore import Qt, QUrl, pyqtSlot, QObject, pyqtSignal, QTimer, QPoint, QCoreApplication
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QWidget, QHBoxLayout, QLabel,
    QPushButton, QGraphicsDropShadowEffect, QApplication
)
from PyQt6.QtGui import QColor, QIcon, QSurfaceFormat

# Ensure OpenGL contexts sharing and unthrottled swap interval (180Hz+)
try:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setSwapInterval(0)
    QSurfaceFormat.setDefaultFormat(fmt)
    import PyQt6.QtWebEngineWidgets
except Exception:
    pass

from actions.geospatial_globe import (
    geocode_location,
    calculate_great_circle_route,
    fetch_live_flights_in_bounds,
    reverse_geocode_area,
    fetch_driving_route,
    fetch_live_iss,
    fetch_live_earthquakes,
    fetch_nearby_places,
    fetch_location_weather,
    fetch_radar_timestamp,
)


class GlobeBridge(QObject):
    """Bridge for communication between WebGL JavaScript and PyQt6."""
    close_requested = pyqtSignal()
    view_updated = pyqtSignal(dict)

    @pyqtSlot()
    def closeGlobe(self):
        self.close_requested.emit()

    @pyqtSlot(str)
    def reportView(self, data_json: str):
        try:
            data = json.loads(data_json)
            self.view_updated.emit(data)
        except Exception:
            pass


class GlobeWindow(QWidget):
    """High-tech 3D HoloGlobe In-Window Popup Overlay."""
    _cmd_sig = pyqtSignal(dict)
    _instance = None

    @classmethod
    def get_instance(cls, parent=None):
        if cls._instance is None:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("GlobeWindow must be initialized on the GUI thread before use.")
            cls._instance = GlobeWindow(parent)
        elif parent is not None and cls._instance.parent() != parent:
            try:
                cls._instance.setParent(parent)
            except Exception:
                pass
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        GlobeWindow._instance = self
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("GlobePopupOverlay")
        self.resize(760, 520)
        self.setMinimumSize(440, 320)
        self.hide()

        self._bridge = GlobeBridge()
        self._bridge.close_requested.connect(self.hide)
        self._bridge.view_updated.connect(self._on_view_updated)
        self._last_view_data = {}
        self._web_view = None
        self._is_fullscreen = False
        self._drag_pos = None

        self._is_page_loaded = False
        self._pending_route = None
        self._pending_driving_route = None
        self._pending_location = None
        self._pending_flyto = None
        self._pending_flights = None
        self._pending_iss = None
        self._pending_earthquakes = None
        self._pending_nearby = None
        self._pending_radar = None

        # Cross-thread command signal router
        self._cmd_sig.connect(self._handle_cmd)

        self._setup_ui()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)

        # Container Frame
        self._container = QWidget(self)
        self._container.setObjectName("GlobeContainer")
        self._container.setStyleSheet("""
            QWidget#GlobeContainer {
                background: #030712;
                border: 2px solid rgba(56, 189, 248, 0.45);
                border-radius: 20px;
            }
        """)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Top Title Bar
        title_bar = QWidget()
        title_bar.setFixedHeight(48)
        title_bar.setStyleSheet("""
            background: rgba(15, 23, 42, 0.95);
            border-top-left-radius: 19px;
            border-top-right-radius: 19px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        """)
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(18, 0, 14, 0)

        title_lbl = QLabel("🌐  NIGHTFALL MAPS // GEOSPATIAL INTELLIGENCE")
        title_lbl.setStyleSheet("color: #bae6fd; font-size: 12px; font-weight: 700; letter-spacing: 0.1em;")
        tb_layout.addWidget(title_lbl)

        tb_layout.addStretch()

        # Window Controls: Minimize / Fullscreen / Close
        btn_min = QPushButton("—")
        btn_min.setFixedSize(28, 28)
        btn_min.setToolTip("Minimize Globe")
        btn_min.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                color: #94a3b8;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
        """)
        btn_min.clicked.connect(self.showMinimized)
        tb_layout.addWidget(btn_min)

        btn_fs = QPushButton("⛶")
        btn_fs.setFixedSize(28, 28)
        btn_fs.setToolTip("Toggle Fullscreen")
        btn_fs.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                color: #94a3b8;
                font-size: 13px;
            }
            QPushButton:hover { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
        """)
        btn_fs.clicked.connect(self._toggle_fullscreen)
        tb_layout.addWidget(btn_fs)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setToolTip("Close Globe")
        btn_close.setStyleSheet("""
            QPushButton {
                background: rgba(239, 68, 68, 0.15);
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 8px;
                color: #fca5a5;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover { background: rgba(239, 68, 68, 0.35); color: #ffffff; }
        """)
        btn_close.clicked.connect(self.hide)
        tb_layout.addWidget(btn_close)

        container_layout.addWidget(title_bar)

        # Web View Area
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWebChannel import QWebChannel

            self._web_view = QWebEngineView()
            self._web_channel = QWebChannel()
            self._web_channel.registerObject("qtBridge", self._bridge)
            self._web_view.page().setWebChannel(self._web_channel)

            settings = self._web_view.settings()
            try:
                settings.setAttribute(settings.WebAttribute.WebGLEnabled, True)
                settings.setAttribute(settings.WebAttribute.JavascriptEnabled, True)
                settings.setAttribute(settings.WebAttribute.LocalContentCanAccessFileUrls, True)
                settings.setAttribute(settings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                settings.setAttribute(settings.WebAttribute.Accelerated2dCanvasEnabled, True)
                settings.setAttribute(settings.WebAttribute.ScrollAnimatorEnabled, False)
            except Exception:
                pass

            self._web_view.loadFinished.connect(self._on_load_finished)

            html_path = Path(__file__).resolve().parent.parent / "assets" / "globe" / "index.html"
            self._web_view.load(QUrl.fromLocalFile(str(html_path)))
            self._web_view.page().setBackgroundColor(QColor("#030712"))
            container_layout.addWidget(self._web_view)
        except Exception as e:
            lbl_err = QLabel(f"3D WebEngine Initialization Note: {e}\nEnsure PyQt6-WebEngine is installed.")
            lbl_err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_err.setStyleSheet("color: #f87171; padding: 40px;")
            container_layout.addWidget(lbl_err)

        root_layout.addWidget(self._container)

    def _on_load_finished(self, ok: bool):
        self._is_page_loaded = ok
        if ok and self._web_view:
            if not self.isVisible():
                self._web_view.page().runJavaScript("if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.pause) window.NIGHTFALLGlobe.pause();")
            else:
                self._web_view.page().runJavaScript("if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.resume) window.NIGHTFALLGlobe.resume();")
        if getattr(self, "_pending_mode", None) and self._web_view:
            pm = self._pending_mode
            self._pending_mode = None
            self._web_view.page().runJavaScript(f"if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.switchMode) window.NIGHTFALLGlobe.switchMode('{pm}');")
        if self._pending_route:
            d = self._pending_route
            self._pending_route = None
            QTimer.singleShot(300, lambda: self._do_route(d))
        if self._pending_location:
            loc = self._pending_location
            self._pending_location = None
            QTimer.singleShot(300, lambda: self._do_show_location(loc))
        if self._pending_flyto:
            f = self._pending_flyto
            self._pending_flyto = None
            QTimer.singleShot(300, lambda: self._do_fly_to(f["lat"], f["lon"]))
        if self._pending_flights:
            fl = self._pending_flights
            self._pending_flights = None
            QTimer.singleShot(300, lambda: self._do_flights(fl))
        if self._pending_driving_route:
            dr = self._pending_driving_route
            self._pending_driving_route = None
            QTimer.singleShot(300, lambda: self._do_driving_route(dr))
        if self._pending_iss:
            iss_d = self._pending_iss
            self._pending_iss = None
            QTimer.singleShot(300, lambda: self._do_iss(iss_d))
        if self._pending_earthquakes:
            eq = self._pending_earthquakes
            self._pending_earthquakes = None
            QTimer.singleShot(300, lambda: self._do_earthquakes(eq))
        if self._pending_nearby:
            nb = self._pending_nearby
            self._pending_nearby = None
            QTimer.singleShot(300, lambda: self._do_nearby(nb))
        if self._pending_radar is not None:
            rd = self._pending_radar
            self._pending_radar = None
            QTimer.singleShot(300, lambda: self._do_weather_radar(rd))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            w = self.width()
            h = self.height()
            margin = 12

            left = pos.x() <= margin
            right = pos.x() >= w - margin
            top = pos.y() <= margin
            bottom = pos.y() >= h - margin

            if left or right or top or bottom:
                self._resize_edge = (left, right, top, bottom)
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geom = self.geometry()
                event.accept()
                return

            if pos.y() <= 48:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        w = self.width()
        h = self.height()
        margin = 12

        if getattr(self, "_resize_edge", None) and getattr(self, "_resize_start_pos", None):
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geom = self._resize_start_geom
            left, right, top, bottom = self._resize_edge
            new_x = geom.x()
            new_y = geom.y()
            new_w = geom.width()
            new_h = geom.height()

            if right:
                new_w = max(self.minimumWidth(), geom.width() + delta.x())
            elif left:
                clamped_w = max(self.minimumWidth(), geom.width() - delta.x())
                new_x = geom.right() - clamped_w
                new_w = clamped_w

            if bottom:
                new_h = max(self.minimumHeight(), geom.height() + delta.y())
            elif top:
                clamped_h = max(self.minimumHeight(), geom.height() - delta.y())
                new_y = geom.bottom() - clamped_h
                new_h = clamped_h

            self.setGeometry(new_x, new_y, new_w, new_h)
            event.accept()
            return

        if event.buttons() == Qt.MouseButton.LeftButton and getattr(self, "_drag_pos", None) is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return

        # Update cursor shape when hovering near borders
        left = pos.x() <= margin
        right = pos.x() >= w - margin
        top = pos.y() <= margin
        bottom = pos.y() >= h - margin

        if (left and top) or (right and bottom):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif (right and top) or (left and bottom):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif left or right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif top or bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._resize_edge = None
        self._resize_start_pos = None

    def _position_overlay(self):
        p = self.parentWidget()
        if p:
            pw = p.width()
            ph = p.height()
            w = min(840, max(460, int(pw * 0.84)))
            h = min(560, max(340, int(ph * 0.80)))
            self.resize(w, h)
            x = (pw - w) // 2
            y = (ph - h) // 2
            self.move(max(8, x), max(8, y))

    def _toggle_fullscreen(self):
        p = self.parentWidget()
        if self._is_fullscreen:
            self._position_overlay()
            self._is_fullscreen = False
        else:
            if p:
                self.setGeometry(0, 0, p.width(), p.height())
            self._is_fullscreen = True

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._web_view and getattr(self, "_is_page_loaded", False):
            self._web_view.page().runJavaScript("if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.resume) window.NIGHTFALLGlobe.resume();")

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._web_view and getattr(self, "_is_page_loaded", False):
            self._web_view.page().runJavaScript("if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.pause) window.NIGHTFALLGlobe.pause();")

    def _handle_cmd(self, payload: dict):
        """Processes cross-thread requests safely on the main GUI thread."""
        action = payload.get("action")
        if action == "open":
            self._do_open(payload.get("location"), payload.get("mode"))
        elif action == "route":
            self._do_route(payload.get("data"))
        elif action == "show_location":
            self._do_show_location(payload.get("data"))
        elif action == "fly_to":
            self._do_fly_to(payload.get("lat"), payload.get("lon"))
        elif action == "flights":
            self._do_flights(payload.get("flights", []))
        elif action == "driving_route":
            self._do_driving_route(payload.get("data"))
        elif action == "iss":
            self._do_iss(payload.get("data"))
        elif action == "earthquakes":
            self._do_earthquakes(payload.get("data"))
        elif action == "nearby":
            self._do_nearby(payload.get("data"))
        elif action == "weather_radar":
            self._do_weather_radar(payload.get("data"))
        elif action == "close":
            self.hide()

    def _do_open(self, focus_location: Optional[str] = None, mode: Optional[str] = None):
        self._position_overlay()
        self.show()
        self.raise_()
        if self._web_view and self._is_page_loaded:
            self._web_view.page().runJavaScript("if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.resume) window.NIGHTFALLGlobe.resume();")
            carto_fix = 'if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.updateCartoKey) { window.NIGHTFALLGlobe.updateCartoKey("cb1_3xgr_1_35c1dc6a9a9b23cb25386cb6"); }'
            self._web_view.page().runJavaScript(carto_fix)
            if mode:
                self._web_view.page().runJavaScript(f"if (window.NIGHTFALLGlobe && window.NIGHTFALLGlobe.switchMode) window.NIGHTFALLGlobe.switchMode('{mode}');")
        elif mode:
            self._pending_mode = mode
        if focus_location:
            QTimer.singleShot(600, lambda: self.fly_to(focus_location))

    def _do_route(self, data: dict):
        self._do_open()
        if not self._is_page_loaded:
            self._pending_route = data
            return
        if self._web_view and data:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.showRoute({json.dumps(data)});"
            self._web_view.page().runJavaScript(js_call)

    def _do_show_location(self, data: dict):
        self._do_open()
        if not self._is_page_loaded:
            self._pending_location = data
            return
        if self._web_view and data:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.showLocation({json.dumps(data)});"
            self._web_view.page().runJavaScript(js_call)

    def _do_fly_to(self, lat: float, lon: float):
        self._do_open()
        if not self._is_page_loaded:
            self._pending_flyto = {"lat": lat, "lon": lon}
            return
        if self._web_view:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.flyTo({lat}, {lon}, 160);"
            self._web_view.page().runJavaScript(js_call)

    def _do_flights(self, flights: list):
        self._do_open()
        if not self._is_page_loaded:
            self._pending_flights = flights
            return
        if self._web_view:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.setFlights({json.dumps(flights)});"
            self._web_view.page().runJavaScript(js_call)

    def _do_driving_route(self, data: dict):
        self._do_open(mode="STREET")
        if not self._is_page_loaded:
            self._pending_driving_route = data
            return
        if self._web_view and data:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.showDrivingRoute({json.dumps(data)});"
            self._web_view.page().runJavaScript(js_call)

    def _do_iss(self, data: dict):
        self._do_open()
        if not self._is_page_loaded:
            self._pending_iss = data
            return
        if self._web_view and data:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.setIssData({json.dumps(data)});"
            self._web_view.page().runJavaScript(js_call)

    def _do_earthquakes(self, data: list):
        self._do_open()
        if not self._is_page_loaded:
            self._pending_earthquakes = data
            return
        if self._web_view and data:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.setEarthquakes({json.dumps(data)});"
            self._web_view.page().runJavaScript(js_call)

    def _do_nearby(self, data: dict):
        self._do_open(mode="STREET")
        if not self._is_page_loaded:
            self._pending_nearby = data
            return
        if self._web_view and data:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.setNearbyPlaces({json.dumps(data)});"
            self._web_view.page().runJavaScript(js_call)

    def _do_weather_radar(self, data: dict):
        self._do_open()
        if not self._is_page_loaded:
            self._pending_radar = data
            return
        if self._web_view and data:
            js_call = f"if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.toggleWeatherRadar({json.dumps(data)});"
            self._web_view.page().runJavaScript(js_call)

    def open_globe(self, focus_location: Optional[str] = None, mode: Optional[str] = None):
        """Open the 3D HoloGlobe display safely from any thread."""
        if threading.current_thread() is threading.main_thread():
            self._do_open(focus_location, mode)
        else:
            self._cmd_sig.emit({"action": "open", "location": focus_location, "mode": mode})

    def close_globe(self):
        """Close/hide the globe display safely from any thread."""
        if threading.current_thread() is threading.main_thread():
            self.hide()
        else:
            self._cmd_sig.emit({"action": "close"})

    def show_route(self, origin: str, destination: str) -> Dict[str, Any]:
        """
        Calculate and display a 3D Great-Circle flight route between two cities.
        Returns the computed route telemetry summary immediately.
        """
        lat1, lon1, origin_label = geocode_location(origin)
        lat2, lon2, dest_label = geocode_location(destination)

        route_info = calculate_great_circle_route(lat1, lon1, lat2, lon2)

        data = {
            "originLat": lat1,
            "originLon": lon1,
            "originName": origin_label.split(",")[0],
            "destLat": lat2,
            "destLon": lon2,
            "destName": dest_label.split(",")[0],
            "distance_km": route_info["distance_km"],
            "distance_miles": route_info["distance_miles"],
            "distance_nm": route_info["distance_nm"],
            "flight_time": route_info["flight_time_estimate"],
            "bearing": route_info["bearing_deg"],
            "waypoints": route_info.get("waypoints", []),
        }

        if threading.current_thread() is threading.main_thread():
            self._do_route(data)
        else:
            self._cmd_sig.emit({"action": "route", "data": data})

        return {
            "origin": origin_label,
            "destination": dest_label,
            "distance_km": route_info["distance_km"],
            "distance_miles": route_info["distance_miles"],
            "flight_time": route_info["flight_time_estimate"],
            "bearing": route_info["bearing_deg"],
        }

    def show_location(self, location_name: str) -> Dict[str, Any]:
        """Navigate to, pin, and display any city, country, or location on the map."""
        lat, lon, label = geocode_location(location_name)
        norm = location_name.strip().lower()
        country_terms = {
            "america", "usa", "united states", "india", "russia", "china",
            "australia", "canada", "brazil", "uk", "united kingdom", "japan",
            "germany", "france", "italy", "spain", "mexico", "indonesia"
        }
        zoom = 4 if (norm in country_terms or len(norm) <= 3) else 7

        weather_info = fetch_location_weather(lat, lon)
        data = {
            "lat": lat,
            "lon": lon,
            "label": label,
            "name": label.split(",")[0],
            "zoom": zoom,
            "weather": weather_info,
        }
        if threading.current_thread() is threading.main_thread():
            self._do_show_location(data)
        else:
            self._cmd_sig.emit({"action": "show_location", "data": data})

        return {"location": label, "lat": lat, "lon": lon, "weather": weather_info}

    def show_driving_route(self, origin: str, destination: str) -> Dict[str, Any]:
        """Calculate and display a real highway road route via OSRM."""
        lat1, lon1, origin_label = geocode_location(origin)
        lat2, lon2, dest_label = geocode_location(destination)

        route_info = fetch_driving_route(lat1, lon1, lat2, lon2)
        data = {
            "originLat": lat1,
            "originLon": lon1,
            "originName": origin_label.split(",")[0],
            "destLat": lat2,
            "destLon": lon2,
            "destName": dest_label.split(",")[0],
            "distance_km": route_info.get("distance_km", 0),
            "distance_miles": route_info.get("distance_miles", 0),
            "duration_str": route_info.get("duration_str", ""),
            "mode": route_info.get("mode", "driving"),
            "waypoints": route_info.get("waypoints", []),
        }

        if threading.current_thread() is threading.main_thread():
            self._do_driving_route(data)
        else:
            self._cmd_sig.emit({"action": "driving_route", "data": data})

        return {
            "origin": origin_label,
            "destination": dest_label,
            "distance_km": route_info.get("distance_km", 0),
            "duration_str": route_info.get("duration_str", ""),
            "mode": route_info.get("mode", "driving"),
        }

    def show_iss_tracker(self) -> Dict[str, Any]:
        """Fetch and track the International Space Station in real time."""
        iss_info = fetch_live_iss()
        if threading.current_thread() is threading.main_thread():
            self._do_iss(iss_info)
        else:
            self._cmd_sig.emit({"action": "iss", "data": iss_info})
        return iss_info

    def show_earthquakes(self, min_mag: float = 2.5) -> List[Dict[str, Any]]:
        """Fetch and display recent USGS earthquakes worldwide."""
        quakes = fetch_live_earthquakes(min_mag)
        if threading.current_thread() is threading.main_thread():
            self._do_earthquakes(quakes)
        else:
            self._cmd_sig.emit({"action": "earthquakes", "data": quakes})
        return quakes

    def show_nearby(self, query: str, location_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search and display tactical POIs (hospitals, hotels, food, etc.) around a location."""
        places = fetch_nearby_places(query, location_name=location_name)
        data = {
            "query": query,
            "location": location_name or "Current Location",
            "places": places,
        }
        if threading.current_thread() is threading.main_thread():
            self._do_nearby(data)
        else:
            self._cmd_sig.emit({"action": "nearby", "data": data})
        return places

    def toggle_weather_radar(self, enable: bool = True) -> bool:
        """Toggle live RainViewer Doppler precipitation radar tiles."""
        ts = fetch_radar_timestamp()
        data = {"enable": enable, "timestamp": ts}
        if threading.current_thread() is threading.main_thread():
            self._do_weather_radar(data)
        else:
            self._cmd_sig.emit({"action": "weather_radar", "data": data})
        return enable

    def show_weather(self, location_name: str) -> Dict[str, Any]:
        """Fetch and present live weather conditions for a location."""
        lat, lon, label = geocode_location(location_name)
        weather_info = fetch_location_weather(lat, lon)
        self.show_location(location_name)
        return {"location": label, "lat": lat, "lon": lon, "weather": weather_info}

    def fly_to(self, location_name: str) -> Dict[str, Any]:
        """Fly camera smoothly to any city or coordinates."""
        lat, lon, label = geocode_location(location_name)
        if threading.current_thread() is threading.main_thread():
            self._do_fly_to(lat, lon)
        else:
            self._cmd_sig.emit({"action": "fly_to", "lat": lat, "lon": lon})
        return {"location": label, "lat": lat, "lon": lon}

    def show_live_flights(self, area_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch live flights in area and display them on the 3D globe."""
        target_area = area_name or "current"
        lat, lon, label = geocode_location(target_area)

        delta = 6.0  # ~600km box around focus point
        flights = fetch_live_flights_in_bounds(lat - delta, lat + delta, lon - delta, lon + delta)

        if threading.current_thread() is threading.main_thread():
            self._do_flights(flights)
        else:
            self._cmd_sig.emit({"action": "flights", "flights": flights})

        return flights

    def inspect_current_view(self, callback=None):
        """Query the 3D globe for what the user is currently viewing on screen."""
        if not self._web_view:
            if callback:
                callback({"area_name": "Unknown", "flight_count": 0})
            return

        def _js_cb(res):
            if isinstance(res, dict):
                lat = float(res.get("centerLat", 0.0))
                lon = float(res.get("centerLon", 0.0))
                bounds = res.get("bounds") or {}
                area_name = reverse_geocode_area(lat, lon)

                flights = []
                if bounds:
                    flights = fetch_live_flights_in_bounds(
                        bounds.get("minLat", lat - 5),
                        bounds.get("maxLat", lat + 5),
                        bounds.get("minLon", lon - 5),
                        bounds.get("maxLon", lon + 5),
                    )

                result = {
                    "center_lat": round(lat, 2),
                    "center_lon": round(lon, 2),
                    "area_name": area_name,
                    "flight_count": len(flights),
                    "flights_sample": flights[:5],
                }
                if callback:
                    callback(result)

        def _do_query():
            if self._web_view:
                self._web_view.page().runJavaScript("if (window.NIGHTFALLGlobe) window.NIGHTFALLGlobe.getCurrentView();", _js_cb)

        if threading.current_thread() is threading.main_thread():
            _do_query()
        else:
            QTimer.singleShot(0, _do_query)

    def _on_view_updated(self, data: dict):
        self._last_view_data = data

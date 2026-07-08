import sys
import math
import subprocess
import os
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, 
                             QHBoxLayout, QVBoxLayout, QLabel, 
                             QPushButton, QSizePolicy)

# Customize ASCII grid dimensions here (Width, Height).
RES_W = 26  
RES_H = 12  
# Change ASCII shading characters here (from empty/bright space to dense/dark character).
ASCII_CHARS = " .:-=+*#%@"

class ASCIIMiniplayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.shape_index = 0  
        self.wave_phase = 0.0 
        self.status = "Paused"
        self.title = "No Songs Detected"
        self.artist = "No Artist Detected"
        self.position = 0.0
        self.duration = 180  
        
        self.initUI()
        
    def initUI(self):
        self.setWindowTitle("ascii_miniplayer")
        self.setObjectName("ascii_miniplayer")
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.widget_expanded = QWidget(self)
        self.widget_expanded.setFixedSize(380, 106) 
        # Change expanded window style here (RGBA colors 0-255, alpha opacity 0-255, and corner border radius).
        self.widget_expanded.setStyleSheet("""
            background-color: rgba(15, 15, 15, 230);
            border-radius: 10px;
        """)
        
        layout_exp = QHBoxLayout(self.widget_expanded)
        layout_exp.setContentsMargins(10, 8, 40, 8)
        layout_exp.setSpacing(12)
        
        self.ascii_label = QLabel()
        # Adjust ASCII art typography here (Font Family, Size, and Weight).
        self.ascii_label.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self.ascii_label.setStyleSheet("color: #ffffff; background: transparent;")
        self.ascii_label.setFixedSize(140, 90)
        self.ascii_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout_exp.addWidget(self.ascii_label)
        
        right_layout = QVBoxLayout()
        right_layout.setSpacing(4)
        
        self.info_label = QLabel("Título\nArtista")
        # Customize track metadata typography here (Font Family, Size, Weight, and Text Hex Color).
        self.info_label.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        self.info_label.setStyleSheet("color: white; background: transparent;")
        right_layout.addWidget(self.info_label)
        
        self.progress_label = QLabel()
        # Adjust the progress timer typography here (Font Family, Size, and Weight).
        self.progress_label.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self.progress_label.setStyleSheet("background: transparent;")
        right_layout.addWidget(self.progress_label)
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        
        # Change media buttons styling here (Background color, font color, hover background color, and corners).
        button_style = """
            QPushButton { 
                background-color: #282828; 
                color: white; 
                border-radius: 4px; 
                padding: 0px 4px;
                text-align: center;
            }
            QPushButton:hover { background-color: #3e3e3e; }
        """
        
        self.btn_prev = QPushButton("⏮")
        self.btn_prev.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.btn_prev.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Adjust media buttons height on the Y-axis here (lower values make them shorter).
        self.btn_prev.setFixedHeight(26) 
        self.btn_prev.setStyleSheet(button_style)
        self.btn_prev.clicked.connect(self.prev_track)
        btn_layout.addWidget(self.btn_prev)
        
        self.btn_play = QPushButton("⏵")
        self.btn_play.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.btn_play.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_play.setFixedHeight(26) 
        self.btn_play.setStyleSheet(button_style)
        self.btn_play.clicked.connect(self.toggle_playback)
        btn_layout.addWidget(self.btn_play)
        
        self.btn_next = QPushButton("⏭")
        self.btn_next.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.btn_next.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_next.setFixedHeight(26) 
        self.btn_next.setStyleSheet(button_style)
        self.btn_next.clicked.connect(self.next_track)
        btn_layout.addWidget(self.btn_next)
        
        right_layout.addLayout(btn_layout)
        layout_exp.addLayout(right_layout)
        
        self.btn_collapse = QPushButton("🗕", self.widget_expanded)
        self.btn_collapse.setGeometry(380 - 36, 0, 36, 30)
        self.btn_collapse.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        # Customize the minimize button color, corner radius matching, and hover overlay transparency here.
        self.btn_collapse.setStyleSheet("""
            QPushButton { 
                background-color: transparent; 
                color: #ff5555; 
                border-top-right-radius: 10px; 
                border-bottom-left-radius: 6px;
            }
            QPushButton:hover { background-color: rgba(255, 85, 85, 30); }
        """)
        self.btn_collapse.clicked.connect(self.collapse_player)

        self.widget_collapsed = QWidget(self)
        self.widget_collapsed.setFixedSize(36, 36)
        # Customize the collapsed status circle appearance here (Hex background color and round border radius).
        self.widget_collapsed.setStyleSheet("""
            background-color: #1DB954; 
            border-radius: 18px;
        """)
        
        self.btn_expand = QPushButton("♫", self.widget_collapsed)
        self.btn_expand.setGeometry(0, 0, 36, 36)
        self.btn_expand.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        # Style the music note icon inside the collapsed state circle here (Font color, weight, and hover effect).
        self.btn_expand.setStyleSheet("""
            QPushButton { background: transparent; color: black; font-weight: bold; border-radius: 18px; }
            QPushButton:hover { color: white; }
        """)
        self.btn_expand.clicked.connect(self.expand_player)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_frame)
        self.anim_timer.start(50) 
        
        self.player_timer = QTimer(self)
        self.player_timer.timeout.connect(self.update_player_data)
        self.player_timer.start(300) 
        
        self.pattern_timer = QTimer(self)
        self.pattern_timer.timeout.connect(self.rotate_ascii_pattern)
        self.pattern_timer.start(3000) 
        
        self.widget_expanded.hide()
        self.widget_collapsed.show()
        self.snap_to_top_right(36, 36)
        self.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'widget_collapsed'):
            self.widget_collapsed.move(self.width() - 36, 0)

    def snap_to_top_right(self, width, height):
        screen = QApplication.primaryScreen().geometry()
        # Change screen anchoring offsets here (Distance in pixels from Right and Top edges of the monitor).
        margin_right, margin_top = 5, 5
        x = screen.width() - width - margin_right
        y = margin_top
        self.setFixedSize(width, height)
        self.move(x, y)

    def collapse_player(self):
        self.widget_expanded.hide()
        self.widget_collapsed.show()
        self.snap_to_top_right(36, 36)

    def expand_player(self):
        self.snap_to_top_right(380, 106)
        self.widget_collapsed.hide()
        self.widget_expanded.show()
        self.update_player_data()

    def rotate_ascii_pattern(self):
        self.shape_index = (self.shape_index + 1) % 4

    def update_player_data(self):
        try:
            meta = subprocess.check_output(
                ["playerctl", "metadata", "--format", "{{status}};;{{title}};;{{artist}};;{{mpris:length}};;{{player_name}};;{{position}}"], 
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            parts = meta.split(";;")
            if len(parts) >= 6:
                self.status = parts[0] if parts[0] else "Paused"
                self.title = parts[1][:20] if parts[1] else "Sem título"
                artist_raw = parts[2] if parts[2] else ""
                player_name = parts[4].lower() if len(parts) > 4 else ""
                if not artist_raw and any(b in player_name for b in ["firefox", "chrome", "chromium", "brave", "opera"]):
                    self.artist = "YouTube/Navegador"
                else:
                    self.artist = artist_raw[:20] if artist_raw else "Desconhecido"
                if parts[3].isdigit(): self.duration = max(1, int(float(parts[3]) / 1000000))
                if parts[5].isdigit(): self.position = float(parts[5]) / 1000000
        except: self.status = "Paused"

    def toggle_playback(self):
        try:
            subprocess.Popen(["playerctl", "play-pause"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            QTimer.singleShot(50, self.update_player_data)
        except Exception as e: print(f"Erro: {e}")

    def prev_track(self):
        try:
            subprocess.Popen(["playerctl", "previous"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            QTimer.singleShot(50, self.update_player_data)
        except Exception as e: print(f"Erro: {e}")

    def next_track(self):
        try:
            subprocess.Popen(["playerctl", "next"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            QTimer.singleShot(50, self.update_player_data)
        except Exception as e: print(f"Erro: {e}")

    def format_time(self, secs):
        s = int(secs)
        return f"{s // 60}:{s % 60:02d}"

    def update_frame(self):
        if self.status == "Playing":
            self.wave_phase += 0.18  
            if self.position < self.duration: self.position += 0.05
            self.btn_play.setText("⏸")
        else:
            self.wave_phase += 0.02  
            self.btn_play.setText("⏵")
            
        ascii_lines = []
        center_x = RES_W / 2
        for y in range(RES_H):
            line = ""
            for x in range(RES_W):
                if self.shape_index == 0:
                    w1 = math.sin(x * 0.35 + self.wave_phase) * 2.2
                    w2 = math.cos(x * 0.18 - self.wave_phase * 0.8) * 1.2
                    wave_y = 4.5 + w1 + w2  
                    if y > wave_y:
                        diff = y - wave_y
                        idx = min(int(diff * 1.8), len(ASCII_CHARS) - 1)
                        line += ASCII_CHARS[idx]
                    else: line += " "
                elif self.shape_index == 1:
                    if x % 2 == 0:
                        w = 4.0 + math.sin(x * 0.5 + self.wave_phase) * 3.0 + math.cos(x * 0.2 + self.wave_phase * 1.5) * 1.0
                        if y > w: line += "@" 
                        elif abs(y - w) < 1.0: line += ":"
                        else: line += " "
                    else: line += " "
                elif self.shape_index == 2:
                    wy1 = 4.5 + math.sin(x * 0.3 + self.wave_phase) * 2.8
                    wy2 = 4.5 + math.sin(x * 0.2 - self.wave_phase + math.pi) * 2.5
                    if abs(y - wy1) < 0.65 or abs(y - wy2) < 0.65: line += "#"
                    else: line += " "
                else:
                    dist = abs(x - center_x)
                    wy = 4.5 + math.sin(dist * 0.6 - self.wave_phase * 1.4) * 3.0
                    if y > wy: line += "="
                    else: line += " "
            ascii_lines.append(line)
            
        self.ascii_label.setText("\n".join(ascii_lines))
        
        bar_width = 12
        pct = min(max(self.position / self.duration, 0.0), 1.0) if self.duration > 0 else 0.0
        total_steps = bar_width * 8
        filled_steps = int(pct * total_steps)
        full_blocks = filled_steps // 8
        frac_index = filled_steps % 8
        frac_chars = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
        
        filled_bar = "█" * full_blocks
        if frac_index > 0: filled_bar += frac_chars[frac_index]
        remaining_spaces = bar_width - full_blocks - (1 if frac_index > 0 else 0)
        empty_bar = "░" * remaining_spaces
        
        # Change custom HTML progress bar colors here (#1DB954 is active tracking green, #333333 is base track, #aaaaaa is duration text).
        html_progress = (
            f'<span style="color: #1DB954;">{filled_bar}</span>'
            f'<span style="color: #333333;">{empty_bar}</span>'
            f' <span style="color: #aaaaaa;">{self.format_time(self.position)}/{self.format_time(self.duration)}</span>'
        )
        self.progress_label.setText(html_progress)
        self.info_label.setText(f"♫ {self.title}\n  {self.artist}")

if __name__ == '__main__':
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    player = ASCIIMiniplayer()
    sys.exit(app.exec())
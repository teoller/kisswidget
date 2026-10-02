
#!/usr/bin/env python3
"""Floating Linux mini-player with MPRIS controls and synced lyrics."""


from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import syncedlyrics  # type: ignore
except ImportError:
    syncedlyrics = None

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QGraphicsOpacityEffect,
)


# ============================================================================
# EASY CUSTOMIZATION
# ============================================================================

APP_NAME = "ascii_miniplayer"

# --- Timing ---
POLL_INTERVAL_MS = 400          # Player metadata polling interval
FRAME_INTERVAL_MS = 50          # UI refresh interval
FORCE_REFRESH_MS = 120          # Delay after playback controls
LYRICS_DELAY = 0.10             # Extra lyric delay, in seconds
NEGATIVE_LYRICS_CACHE_SECONDS = 300.0

# --- Window ---
COLLAPSED_SIZE = 36
COLLAPSED_OPACITY = 0.50
EXPANDED_HEIGHT = 106
MIN_EXPANDED_WIDTH = 500
MAX_EXPANDED_WIDTH = 1250
RIGHT_PANEL_WIDTH = 250
OUTER_MARGINS_WIDTH = 62
SAFETY_BUFFER = 28
WINDOW_MARGIN_RIGHT = 5
WINDOW_MARGIN_TOP = 5

# --- Colors ---
BG_COLOR = "rgba(15, 15, 15, 232)"
TEXT_COLOR = "#ffffff"
MUTED_COLOR = "#aaaaaa"
ACCENT_COLOR = "#fffdd0"
EMPTY_BAR_COLOR = "#333333"
BUTTON_BG = "#282828"
BUTTON_HOVER = "#3e3e3e"
BUTTON_PRESSED = "#505050"
BUTTON_DISABLED = "#666666"
COLLAPSED_BG = "#000000"
COLLAPSED_SYMBOL_COLOR = "#fffdd0"
COLLAPSED_SYMBOL_HOVER_COLOR = "#000000"

# --- Fonts ---
LYRICS_FONT_FAMILY = "Courier New"
LYRICS_FONT_SIZE = 8
INFO_FONT_FAMILY = "Arial"
INFO_FONT_SIZE = 9
BUTTON_FONT_FAMILY = "Arial"
BUTTON_FONT_SIZE = 10
COLLAPSED_SYMBOL_FONT_SIZE = 12
COLLAPSE_BUTTON_FONT_SIZE = 11

# --- Layout ---
EXPANDED_CONTENT_MARGINS = (12, 8, 38, 8)
EXPANDED_SPACING = 12
RIGHT_PANEL_SPACING = 5
BUTTON_SPACING = 6
BUTTON_HEIGHT = 26

# --- External services ---
CACHE_DIR = Path(os.path.expanduser("~/.cache/ascii_miniplayer_lyrics"))
PLAYERCTL_NAME = "playerctl"
PLAYERCTL_PLAYERS = "spotify,%any"
PLAYERCTL_TIMEOUT = 0.8

CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# MODELS / HELPERS
# ============================================================================

@dataclass(slots=True)
class PlayerSnapshot:
    status: str
    title: str
    artist: str
    duration: float
    position: float
    player_name: str
    album: str

    @property
    def available(self) -> bool:
        return bool(self.title or self.player_name)

    @property
    def is_playing(self) -> bool:
        return self.status.lower() == "playing"


@dataclass(slots=True)
class PositionState:
    value: float = 0.0
    sampled_at: float = 0.0


# ============================================================================
# PLAYERCTL
# ============================================================================

PLAYERCTL_PATH = shutil.which(PLAYERCTL_NAME)


def run_playerctl(*args: str, timeout: float = PLAYERCTL_TIMEOUT) -> Optional[str]:
    """Run playerctl with a bounded timeout."""
    if not PLAYERCTL_PATH:
        return None

    try:
        result = subprocess.run(
            [PLAYERCTL_PATH, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def fire_playerctl(*args: str) -> bool:
    """Start a playerctl command without waiting for completion."""
    if not PLAYERCTL_PATH:
        return False

    try:
        subprocess.Popen(
            [PLAYERCTL_PATH, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number != number or number < 0:  # Reject NaN and negative values.
            return default
        return number
    except (TypeError, ValueError):
        return default


def read_player_snapshot() -> Optional[PlayerSnapshot]:
    """Read one consistent MPRIS metadata snapshot through playerctl."""
    output = run_playerctl(
        "-p",
        PLAYERCTL_PLAYERS,
        "metadata",
        "--format",
        "{{status}};;{{title}};;{{artist}};;{{album}};;{{mpris:length}};;{{player_name}};;{{position}}",
    )
    if not output:
        return None

    parts = output.split(";;")
    if len(parts) < 7:
        return None

    status = parts[0].strip() or "Paused"
    title = parts[1].strip()
    artist_raw = parts[2].strip()
    album = parts[3].strip()
    player_name = parts[5].strip()

    # Browsers often provide no artist; use YouTube as a neutral source label.
    browser_names = ("firefox", "chrome", "chromium", "brave", "opera", "vivaldi", "edge")
    if not artist_raw and any(name in player_name.lower() for name in browser_names):
        artist = "YouTube"
    else:
        artist = artist_raw or "Unknown"

    # MPRIS length and position are normally reported in microseconds.
    duration = parse_float(parts[4]) / 1_000_000.0
    position = parse_float(parts[6]) / 1_000_000.0

    if duration > 0:
        duration = max(duration, 0.001)
        position = min(position, duration)

    return PlayerSnapshot(
        status=status,
        title=title or "No title",
        artist=artist,
        album=album,
        duration=duration,
        position=position,
        player_name=player_name,
    )


# ============================================================================
# LYRICS / CACHE
# ============================================================================

_LRC_TIMESTAMP_RE = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_LRC_OFFSET_RE = re.compile(r"^\[offset\s*:\s*([+-]?\d+)\]\s*$", re.IGNORECASE)


def normalize_match_text(value: str) -> str:
    """Normalize metadata for accent- and punctuation-insensitive matching."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", value)
    value = re.sub(r"\b(feat|ft|featuring)\.?\s+[^-]+$", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def metadata_similarity(a: str, b: str) -> float:
    """Return a simple similarity score tolerant of version suffixes."""
    from difflib import SequenceMatcher
    na = normalize_match_text(a)
    nb = normalize_match_text(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def lyrics_cache_key(artist: str, title: str, duration: float = 0.0, album: str = "") -> str:
    # Include duration and album so different releases get different cache keys.
    normalized = (
        f"v2\n{artist.strip().casefold()}\n{title.strip().casefold()}\n"
        f"{album.strip().casefold()}\n{round(duration):d}"
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    readable = re.sub(r"[^a-z0-9]+", "_", f"{artist}_{title}".casefold()).strip("_")
    readable = readable[:70] or "track"
    return f"{readable}_{digest}"


def lyrics_cache_path(artist: str, title: str, duration: float = 0.0, album: str = "") -> Path:
    return CACHE_DIR / f"{lyrics_cache_key(artist, title, duration, album)}.lrc"


def atomic_write(path: Path, content: str) -> None:
    """Write atomically by replacing the old cache only after a full write."""
    fd, tmp_name = tempfile.mkstemp(prefix=".lyrics_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    """Convert LRC text into sorted (seconds, text) pairs."""
    parsed: list[tuple[float, str]] = []
    offset_seconds = 0.0

    # Read the optional global offset first.
    for raw_line in lrc_text.splitlines():
        match = _LRC_OFFSET_RE.match(raw_line.strip())
        if match:
            offset_seconds = int(match.group(1)) / 1000.0
            break

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue

        # Ignore LRC metadata tags that are not lyric timestamps.
        if re.match(r"^\[(ar|ti|al|by|re|ve|length|offset):", line, re.IGNORECASE):
            continue

        matches = list(_LRC_TIMESTAMP_RE.finditer(line))
        if not matches:
            continue

        text = _LRC_TIMESTAMP_RE.sub("", line, count=0).strip()
        if not text:
            continue

        # Ignore common Chinese songwriter/composer credit lines.
        if text.startswith(("作词", "作曲")):
            continue

        for match in matches:
            minutes = int(match.group(1))
            seconds = parse_float(match.group(2).replace(":", "."))
            # Positive offsets move lyrics earlier; negative offsets move them later.
            # Subtracting the offset from the timestamp implements that rule.
            timestamp = max(0.0, minutes * 60.0 + seconds - offset_seconds)
            parsed.append((timestamp, text))

    parsed.sort(key=lambda item: item[0])

    # Remove exact duplicate timestamp/text pairs.
    deduped: list[tuple[float, str]] = []
    for item in parsed:
        if deduped and abs(deduped[-1][0] - item[0]) < 1e-6 and deduped[-1][1] == item[1]:
            continue
        deduped.append(item)

    return deduped


def fetch_lrclib_exact(artist: str, title: str, duration: float, album: str = "") -> Optional[str]:
    """Query LRCLIB using title, artist, album, and track duration."""
    if not artist or not title or duration <= 0 or artist == "Unknown":
        return None

    params = {
        "track_name": title,
        "artist_name": artist,
        "duration": str(max(1, round(duration))),
    }
    if album:
        params["album_name"] = album

    url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ascii_miniplayer/2.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            import json
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    synced = data.get("syncedLyrics")
    if not synced or not parse_lrc(synced):
        return None

    # Reject metadata that is clearly different from the active track.
    returned_title = str(data.get("trackName") or data.get("name") or "")
    returned_artist = str(data.get("artistName") or "")
    returned_duration = parse_float(str(data.get("duration", 0)))

    if metadata_similarity(title, returned_title) < 0.72:
        return None
    if metadata_similarity(artist, returned_artist) < 0.72:
        return None
    if returned_duration > 0 and abs(returned_duration - duration) > 2.5:
        return None

    return synced


def get_synced_lyrics(
    artist: str,
    title: str,
    duration: float = 0.0,
    album: str = "",
    force_refresh: bool = False,
) -> Optional[str]:
    """Load synced lyrics with a duration-aware LRCLIB lookup first."""
    cache_path = lyrics_cache_path(artist, title, duration, album)

    if not force_refresh:
        try:
            if cache_path.exists():
                cached = cache_path.read_text(encoding="utf-8")
                if parse_lrc(cached):
                    return cached
        except OSError:
            pass

    # Prefer an exact duration-aware match.
    raw = fetch_lrclib_exact(artist, title, duration, album)
    if raw:
        try:
            atomic_write(cache_path, raw)
        except OSError:
            pass
        return raw

    if syncedlyrics is None:
        return None

    query = f"{title} {artist}".strip()
    providers = ["Musixmatch", "NetEase", "Megalobiz"]

    # Fall back to other synced lyric providers when LRCLIB has no match.
    for provider in providers:
        try:
            raw = syncedlyrics.search(query, synced_only=True, providers=[provider])
        except Exception:
            raw = None
        if not raw:
            continue
        if not parse_lrc(raw):
            continue

        try:
            atomic_write(cache_path, raw)
        except OSError:
            pass
        return raw

    return None


# ============================================================================
# LYRICS WORKER
# ============================================================================

class LyricsSignals(QObject):
    finished = pyqtSignal(str, str, list)


class LyricsWorker(QRunnable):
    """Fetch lyrics outside the GUI thread."""

    def __init__(self, request_id: str, artist: str, title: str, duration: float, album: str, force_refresh: bool = False):
        super().__init__()
        self.request_id = request_id
        self.artist = artist
        self.title = title
        self.duration = duration
        self.album = album
        self.force_refresh = force_refresh
        self.signals = LyricsSignals()

    def run(self) -> None:
        try:
            raw = get_synced_lyrics(
                self.artist,
                self.title,
                self.duration,
                self.album,
                self.force_refresh,
            )
            lyrics = parse_lrc(raw) if raw else []
        except Exception:
            lyrics = []

        self.signals.finished.emit(self.request_id, self.title, lyrics)


# ============================================================================
# MAIN WIDGET
# ============================================================================

class ASCIIMiniplayer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.status = "Paused"
        self.title = "No track"
        self.artist = "Unknown"
        self.duration = 0.0
        self.position = PositionState()
        self.player_name = ""
        self.album = ""
        self.current_track_id = ""
        self.lyrics: list[tuple[float, str]] = []
        self.current_lyric_text = "No synced lyrics"

        self.is_expanded = False
        self.target_width = MIN_EXPANDED_WIDTH
        self._last_display_text = ""
        self._last_info_text = ""
        self._closing = False
        self._consecutive_player_failures = 0
        self._pending_lyrics_request: Optional[str] = None
        self._negative_lyrics_cache: dict[str, float] = {}

        # Keep the worker pool local to this application.
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(3)

        # Preserve the original top-right startup position.
        self._target_screen = QGuiApplication.primaryScreen()

        self.init_ui()
        self._setup_timers()

        self.expanded_widget.hide()
        self.collapsed_widget.show()
        self.snap_to_top_right(COLLAPSED_SIZE, COLLAPSED_SIZE)
        self.show()

        # Fetch metadata immediately at startup.
        QTimer.singleShot(0, self.update_player_data)

    # UI

    def init_ui(self) -> None:
        self.setObjectName(APP_NAME)
        self.setWindowTitle(APP_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # XCB + X11BypassWindowManagerHint keeps the overlay outside normal WM handling.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
        )

        # Expanded panel
        self.expanded_widget = QWidget(self)
        self.expanded_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {BG_COLOR};
                border-radius: 10px;
            }}
            """
        )

        exp_layout = QHBoxLayout(self.expanded_widget)
        exp_layout.setContentsMargins(*EXPANDED_CONTENT_MARGINS)
        exp_layout.setSpacing(EXPANDED_SPACING)

        self.lyrics_label = QLabel()
        self.lyrics_label.setFont(QFont(LYRICS_FONT_FAMILY, LYRICS_FONT_SIZE, QFont.Weight.Bold))
        self.lyrics_label.setStyleSheet(
            f"color: {TEXT_COLOR}; background: transparent; border: none;"
        )
        self.lyrics_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.lyrics_label.setWordWrap(False)
        self.lyrics_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        exp_layout.addWidget(self.lyrics_label, alignment=Qt.AlignmentFlag.AlignLeft)

        # Right panel
        self.right_container = QWidget()
        self.right_container.setFixedWidth(RIGHT_PANEL_WIDTH)
        self.right_container.setStyleSheet("background: transparent; border: none;")

        right_layout = QVBoxLayout(self.right_container)
        right_layout.setSpacing(RIGHT_PANEL_SPACING)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel("♫ No track\n  Unknown")
        self.info_label.setFont(QFont(INFO_FONT_FAMILY, INFO_FONT_SIZE, QFont.Weight.Bold))
        self.info_label.setStyleSheet(
            f"color: {TEXT_COLOR}; background: transparent; border: none;"
        )
        self.info_label.setFixedWidth(RIGHT_PANEL_WIDTH)
        self.info_label.setMinimumHeight(34)
        right_layout.addWidget(self.info_label)

        self.progress_label = QLabel()
        self.progress_label.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self.progress_label.setStyleSheet("background: transparent; border: none;")
        self.progress_label.setFixedWidth(RIGHT_PANEL_WIDTH)
        right_layout.addWidget(self.progress_label)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(BUTTON_SPACING)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        btn_style = f"""
            QPushButton {{
                background-color: {BUTTON_BG};
                color: {TEXT_COLOR};
                border: none;
                border-radius: 4px;
                padding: 0px 4px;
                text-align: center;
            }}
            QPushButton:hover {{ background-color: {BUTTON_HOVER}; }}
            QPushButton:pressed {{ background-color: {BUTTON_PRESSED}; }}
            QPushButton:disabled {{ color: {BUTTON_DISABLED}; }}
        """

        self.btn_prev = QPushButton("⏮")
        self.btn_play = QPushButton("⏵")
        self.btn_next = QPushButton("⏭")
        self.btn_refresh = QPushButton("↻")

        for button in (self.btn_prev, self.btn_play, self.btn_next, self.btn_refresh):
            button.setFont(QFont(BUTTON_FONT_FAMILY, BUTTON_FONT_SIZE, QFont.Weight.Bold))
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setFixedHeight(BUTTON_HEIGHT)
            button.setStyleSheet(btn_style)
            btn_layout.addWidget(button)

        self.btn_prev.setToolTip("Previous track")
        self.btn_play.setToolTip("Play / pause")
        self.btn_next.setToolTip("Next track")
        self.btn_refresh.setToolTip("Refresh lyrics")

        self.btn_prev.clicked.connect(self.prev_track)
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_next.clicked.connect(self.next_track)
        self.btn_refresh.clicked.connect(self.refresh_lyrics)

        right_layout.addLayout(btn_layout)
        exp_layout.addWidget(self.right_container, alignment=Qt.AlignmentFlag.AlignRight)

        # Collapse button
        self.btn_collapse = QPushButton("×", self.expanded_widget)
        self.btn_collapse.setFont(QFont(BUTTON_FONT_FAMILY, COLLAPSE_BUTTON_FONT_SIZE, QFont.Weight.Bold))
        self.btn_collapse.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                color: #ff5555;
                border: none;
                border-top-right-radius: 10px;
                border-bottom-left-radius: 6px;
            }
            QPushButton:hover { background: rgba(255, 85, 85, 30); }
            """
        )
        self.btn_collapse.setToolTip("Collapse")
        self.btn_collapse.clicked.connect(self.collapse_player)

        # Collapsed state
        self.collapsed_widget = QWidget(self)
        self.collapsed_widget.setFixedSize(COLLAPSED_SIZE, COLLAPSED_SIZE)
        self.collapsed_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {COLLAPSED_BG};
                border-radius: 18px;
            }}
            """
        )

        self.btn_expand = QPushButton("♫", self.collapsed_widget)
        self.btn_expand.setGeometry(0, 0, COLLAPSED_SIZE, COLLAPSED_SIZE)
        self.btn_expand.setFont(QFont(BUTTON_FONT_FAMILY, COLLAPSED_SYMBOL_FONT_SIZE, QFont.Weight.Bold))
        self.btn_expand.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {COLLAPSED_SYMBOL_COLOR};
                font-weight: bold;
                border: none;
                border-radius: 18px;
            }}
            QPushButton:hover {{ color: {COLLAPSED_SYMBOL_HOVER_COLOR}; background: {COLLAPSED_SYMBOL_COLOR}; }}
            """
        )
        self.btn_expand.setToolTip("Expand mini-player")
        self.btn_expand.clicked.connect(self.expand_player)

        # Apply opacity to the collapsed widget itself for reliable X11 overlays.
        self.collapsed_opacity_effect = QGraphicsOpacityEffect(self.collapsed_widget)
        self.collapsed_opacity_effect.setOpacity(COLLAPSED_OPACITY)
        self.collapsed_widget.setGraphicsEffect(self.collapsed_opacity_effect)

    def _setup_timers(self) -> None:
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(FRAME_INTERVAL_MS)
        self.anim_timer.timeout.connect(self.update_frame)
        self.anim_timer.start()

        self.player_timer = QTimer(self)
        self.player_timer.setInterval(POLL_INTERVAL_MS)
        self.player_timer.timeout.connect(self.update_player_data)
        self.player_timer.start()

    # Window

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "collapsed_widget"):
            self.collapsed_widget.move(self.width() - COLLAPSED_SIZE, 0)

    def _screen_for_widget(self):
        if self._target_screen is not None:
            return self._target_screen
        screen = self.screen()
        if screen is not None:
            return screen
        return QGuiApplication.primaryScreen()

    def _capture_target_screen(self) -> None:
        # Keep the selected screen after startup.
        if self._target_screen is None:
            self._target_screen = QGuiApplication.primaryScreen()

    def snap_to_top_right(self, width: int, height: int) -> None:
        if self._target_screen is None:
            self._capture_target_screen()
        screen = self._screen_for_widget()
        if screen is None:
            return

        available = screen.availableGeometry()
        margin_right = WINDOW_MARGIN_RIGHT
        margin_top = WINDOW_MARGIN_TOP

        width = max(1, min(width, available.width()))
        height = max(1, min(height, available.height()))

        x = available.right() - width + 1 - margin_right
        y = available.top() + margin_top

        self.setFixedSize(width, height)
        self.move(x, y)

    # Layout

    def _calculate_target_width(self, display_text: str) -> int:
        fm = QFontMetrics(self.lyrics_label.font())
        lines = display_text.split("\n")
        max_line_width = max((fm.horizontalAdvance(line) for line in lines), default=160)
        target = max(
            MIN_EXPANDED_WIDTH,
            max_line_width + RIGHT_PANEL_WIDTH + OUTER_MARGINS_WIDTH + SAFETY_BUFFER,
        )
        return min(MAX_EXPANDED_WIDTH, target)

    def update_dynamic_size(self, display_text: str, force: bool = False) -> None:
        if not self.is_expanded:
            return

        if not force and display_text == self._last_display_text:
            return
        self._last_display_text = display_text
        self.target_width = self._calculate_target_width(display_text)

    def animate_layout_width(self) -> None:
        if not self.is_expanded:
            return

        current = self.width()
        target = self.target_width
        if abs(target - current) < 2:
            new_width = target
        else:
            # Ease expansion faster than contraction to reduce visual jitter.
            factor = 0.35 if target > current else 0.22
            new_width = int(current + (target - current) * factor)

        if new_width == current:
            return

        available_lyrics_width = max(
            160,
            new_width - RIGHT_PANEL_WIDTH - OUTER_MARGINS_WIDTH - SAFETY_BUFFER,
        )

        self.lyrics_label.setFixedWidth(available_lyrics_width)
        self.expanded_widget.setFixedSize(new_width, EXPANDED_HEIGHT)
        self.btn_collapse.setGeometry(new_width - 36, 0, 36, 30)
        self.snap_to_top_right(new_width, EXPANDED_HEIGHT)

    def collapse_player(self) -> None:
        self._capture_target_screen()
        self.is_expanded = False
        self.expanded_widget.hide()
        self.collapsed_widget.show()
        self.snap_to_top_right(COLLAPSED_SIZE, COLLAPSED_SIZE)

    def expand_player(self) -> None:
        self._capture_target_screen()
        self.is_expanded = True
        self.collapsed_widget.hide()
        self.expanded_widget.show()
        self._last_display_text = ""
        self.update_player_data()
        self.update_frame()
        self.update_dynamic_size(self._build_display_text(), force=True)
        self.animate_layout_width()

    # Lyrics

    def _track_id(self, title: str, artist: str, album: str = "", duration: float = 0.0) -> str:
        # Distinguish releases that share title and artist.
        return (
            f"{title.strip()}\x00{artist.strip()}\x00{album.strip()}\x00{round(duration):d}"
        ).casefold()

    def fetch_lyrics_in_background(
        self, title: str, artist: str, request_id: str, duration: float = 0.0, album: str = "", force_refresh: bool = False
    ) -> None:
        if not title or title == "No title":
            self.lyrics = []
            self.current_lyric_text = "No lyrics available"
            return

        negative_until = self._negative_lyrics_cache.get(request_id, 0.0)
        if negative_until > time.monotonic():
            self.lyrics = []
            self.current_lyric_text = "No synced lyrics"
            return

        self._pending_lyrics_request = request_id
        worker = LyricsWorker(request_id, artist, title, duration, album, force_refresh)
        worker.signals.finished.connect(self.on_lyrics_result)
        self.thread_pool.start(worker)

    def on_lyrics_result(self, request_id: str, title: str, lyrics: list) -> None:
        # Ignore results that belong to an older track.
        if self._closing or request_id != self.current_track_id:
            return

        self._pending_lyrics_request = None
        if lyrics:
            self.lyrics = [(float(t), str(text)) for t, text in lyrics]
            self.current_lyric_text = ""
        else:
            self.lyrics = []
            self.current_lyric_text = "No synced lyrics"
            self._negative_lyrics_cache[request_id] = time.monotonic() + NEGATIVE_LYRICS_CACHE_SECONDS

        self._last_display_text = ""
        self.update_frame()

    def refresh_lyrics(self) -> None:
        if not self.title or self.title == "No track":
            return

        self._negative_lyrics_cache.pop(self.current_track_id, None)
        self.lyrics = []
        self.current_lyric_text = "Fetching lyrics..."
        self._last_display_text = ""
        self.fetch_lyrics_in_background(
            self.title, self.artist, self.current_track_id, self.duration, self.album, force_refresh=True
        )
        self.update_frame()

    @staticmethod
    def parse_lrc_text(lrc_text: str) -> list[tuple[float, str]]:
        return parse_lrc(lrc_text)

    # Player

    def _apply_snapshot(self, snapshot: PlayerSnapshot) -> None:
        new_track_id = self._track_id(
            snapshot.title, snapshot.artist, snapshot.album, snapshot.duration
        )
        track_changed = new_track_id != self.current_track_id

        self.status = snapshot.status
        self.title = snapshot.title
        self.artist = snapshot.artist
        self.album = snapshot.album
        self.player_name = snapshot.player_name
        self.duration = snapshot.duration

        # Resync to the real MPRIS position; frames interpolate between polls.
        self.position.value = snapshot.position
        self.position.sampled_at = time.monotonic()

        if track_changed:
            self.current_track_id = new_track_id
            self.lyrics = []
            self.current_lyric_text = "Fetching lyrics..."
            self._last_display_text = ""
            self.fetch_lyrics_in_background(
                self.title, self.artist, new_track_id, self.duration, self.album
            )

        self._consecutive_player_failures = 0

    def update_player_data(self) -> None:
        if self._closing:
            return

        snapshot = read_player_snapshot()
        if snapshot is None:
            self._consecutive_player_failures += 1

            # Keep the last metadata during transient playerctl failures.
            if self._consecutive_player_failures >= 4:
                self.status = "Paused"
                self.player_name = ""
            return

        self._apply_snapshot(snapshot)

    # Controls

    def _command_and_refresh(self, *args: str) -> None:
        if not fire_playerctl("-p", PLAYERCTL_PLAYERS, *args):
            return
        QTimer.singleShot(FORCE_REFRESH_MS, self.update_player_data)

    def toggle_playback(self) -> None:
        self._command_and_refresh("play-pause")

    def prev_track(self) -> None:
        self._command_and_refresh("previous")

    def next_track(self) -> None:
        self._command_and_refresh("next")

    # Display

    @staticmethod
    def format_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def current_position(self) -> float:
        """Estimate position from the last MPRIS sample and monotonic elapsed time."""
        value = max(0.0, self.position.value)
        if self.status.lower() != "playing" or self.position.sampled_at <= 0:
            return min(value, self.duration) if self.duration > 0 else value

        elapsed = time.monotonic() - self.position.sampled_at
        # Cap interpolation during long playerctl outages.
        elapsed = min(max(0.0, elapsed), 2.0)
        value += elapsed

        if self.duration > 0:
            value = min(value, self.duration)
        return value

    def _current_lyrics(self, position: float) -> tuple[str, str, str]:
        if not self.lyrics:
            return self.current_lyric_text, "", ""

        adjusted = max(0.0, position - LYRICS_DELAY)

        lo, hi = 0, len(self.lyrics)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.lyrics[mid][0] <= adjusted:
                lo = mid + 1
            else:
                hi = mid

        idx = lo - 1
        if idx < 0:
            return "  Intro...", "", ""

        prev_line = self.lyrics[idx - 1][1] if idx > 0 else ""
        curr_line = self.lyrics[idx][1] or "♪ ♪ ♪"
        next_line = self.lyrics[idx + 1][1] if idx + 1 < len(self.lyrics) else ""
        return prev_line, curr_line, next_line

    def _build_display_text(self) -> str:
        position = self.current_position()

        if self.lyrics:
            prev_line, curr_line, next_line = self._current_lyrics(position)
            return f"{prev_line}\n> {curr_line}\n{next_line}"

        return self.current_lyric_text

    def _set_lyrics_label(self, display_text: str) -> None:
        if display_text == self._last_display_text and self.lyrics_label.text():
            return

        if self.lyrics:
            prev_line, curr_line, next_line = display_text.split("\n", 2)
            html_text = (
                f"{html.escape(prev_line)}<br>"
                f'<span style="color: {ACCENT_COLOR};">&gt; {html.escape(curr_line[2:] if curr_line.startswith("> ") else curr_line)}</span><br>'
                f"{html.escape(next_line)}"
            )
        else:
            html_text = html.escape(display_text).replace("\n", "<br>")

        self.lyrics_label.setText(html_text)

    def _update_progress_label(self, position: float) -> None:
        duration = max(self.duration, 0.0)
        if duration > 0:
            pct = min(max(position / duration, 0.0), 1.0)
        else:
            pct = 0.0

        bar_width = 12
        total_steps = bar_width * 8
        filled_steps = int(round(pct * total_steps))
        full_blocks = min(bar_width, filled_steps // 8)
        frac_index = filled_steps % 8
        frac_chars = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]

        filled_bar = "█" * full_blocks
        if full_blocks < bar_width and frac_index:
            filled_bar += frac_chars[frac_index]

        occupied = full_blocks + (1 if full_blocks < bar_width and frac_index else 0)
        empty_bar = "░" * max(0, bar_width - occupied)

        html_progress = (
            f'<span style="color: {ACCENT_COLOR};">{filled_bar}</span>'
            f'<span style="color: {EMPTY_BAR_COLOR};">{empty_bar}</span>'
            f' <span style="color: {MUTED_COLOR};">'
            f"{self.format_time(position)}/{self.format_time(duration)}"
            f"</span>"
        )
        self.progress_label.setText(html_progress)

    def _update_info_label(self) -> None:
        fm_info = QFontMetrics(self.info_label.font())
        elided_title = fm_info.elidedText(
            f"♫ {self.title}",
            Qt.TextElideMode.ElideRight,
            RIGHT_PANEL_WIDTH - 5,
        )
        elided_artist = fm_info.elidedText(
            f"  {self.artist}",
            Qt.TextElideMode.ElideRight,
            RIGHT_PANEL_WIDTH - 5,
        )
        info_text = f"{elided_title}\n{elided_artist}"

        if info_text != self._last_info_text:
            self._last_info_text = info_text
            self.info_label.setText(info_text)

    def update_frame(self) -> None:
        if self._closing:
            return

        position = self.current_position()
        display_text = self._build_display_text()

        # Reflect the real player state.
        self.btn_play.setText("⏸" if self.status.lower() == "playing" else "⏵")

        self._set_lyrics_label(display_text)
        self.update_dynamic_size(display_text)
        self._update_progress_label(position)
        self._update_info_label()
        self.animate_layout_width()

    # Lifecycle

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._closing = True
        self.anim_timer.stop()
        self.player_timer.stop()
        super().closeEvent(event)


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:
    # Force XCB when XWayland is available so the overlay behaves like the original.
    if os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    player = ASCIIMiniplayer()

    # Keep the UI usable even when playerctl is not installed.
    if not PLAYERCTL_PATH:
        player.current_lyric_text = "playerctl not found"
        player.update_frame()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

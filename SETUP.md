# kisswidget - ASCII Miniplayer :)

A transparent, lightweight, and borderless ASCII audio miniplayer for Linux.

**Note:** This widget works **ONLY on Linux** (both X11 and Wayland). It will **not** work on Windows.
**Note2:** The "forwards"and "backwards" buttons dont work properly on youtube, but Spotify works perfectly, i will try to fix this later!

## Prerequisites & Setup

Before running the player, you need to install `playerctl` and `PyQt6`.

### 1. System Dependencies
Install `playerctl` using your distribution's package manager:

```bash
# Ubuntu / Debian / Pop!_OS
sudo apt install playerctl

# Arch Linux / Manjaro
sudo pacman -S playerctl

# Fedora
sudo dnf install playerctl
```

### 2. Python Dependencies
Also install PyQt6

```bash
pip install PyQt6
```

### 3. Run!
Now, run the command
```bash
python miniplayer.py
```

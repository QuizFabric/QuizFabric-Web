"""Stream the QuizFabric server log to the ESP32 log display.

The display advertises itself on the network via mDNS as
quizfabriclcd.local, so its IP never needs to be known. This service
resolves that name, connects to the display's TCP listener (port 8266),
and pushes every new line of today's server log to it, reconnecting
whenever the display reboots.

Runs as its own systemd unit (quizfabric-logstream) so it can never
block or crash the main server.
"""
import os
import re
import socket
import time

DISPLAY_HOST = "quizfabriclcd.local"
DISPLAY_PORT = 8266
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# "2026-10-22 15:36:22,123 [INFO] " - dropped so the display shows just
# the message (timestamps eat half the screen width).
LINE_PREFIX = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[[A-Z]+\] ')

# ANSI color codes the display understands. Colors are decided HERE so
# the display firmware never needs updating for color changes.
ANSI = {
    "red":       "\x1b[31m",
    "green":     "\x1b[32m",
    "yellow":    "\x1b[33m",
    "magenta":   "\x1b[35m",
    "cyan":      "\x1b[36m",
    "white":     "\x1b[37m",
    "gray":      "\x1b[90m",
    "lightblue": "\x1b[94m",
    # Custom colors are truecolor (38;2;R;G;B) - change them freely,
    # the display renders whatever RGB arrives with no firmware updates.
    "orange":     "\x1b[38;2;255;140;0m",
    "brown":      "\x1b[38;2;139;69;19m",
    "pink":       "\x1b[38;2;255;182;193m",
    "brightred":  "\x1b[38;2;255;60;60m",
    "darkpurple": "\x1b[38;2;75;0;130m",
}


def colorize_line(line):
    """Wrap a log line in ANSI colors per its type/parts."""
    if any(k in line for k in ("Traceback", "Error", "error")):
        return ANSI["red"] + line
    if line.startswith("["):
        return ANSI["cyan"] + line
    parts = line.split(" ", 2)
    tag = parts[0] if parts else ""
    ip = parts[1] if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""
    if tag == "REQ":
        mparts = rest.split(" ", 1)
        method = mparts[0] if mparts else ""
        path = mparts[1] if len(mparts) > 1 else ""
        mcolor = ANSI["green"] if method == "GET" else (
            ANSI["orange"] if method == "POST" else ANSI["gray"])
        return (ANSI["magenta"] + "REQ " + ANSI["yellow"] + ip + " "
                + mcolor + method + ANSI["lightblue"]
                + (" " + path if path else ""))
    if tag == "SRH":
        return ANSI["red"] + "SRH " + ANSI["yellow"] + ip + ANSI["white"] + (" " + rest if rest else "")
    if tag == "CVR":
        return ANSI["orange"] + "CVR " + ANSI["yellow"] + ip + ANSI["darkpurple"] + (" " + rest if rest else "")
    if tag == "IMG":
        qparts = rest.split(" ", 1)
        qnum = qparts[0] if qparts else ""
        name = qparts[1] if len(qparts) > 1 else ""
        return (ANSI["brown"] + "IMG " + ANSI["yellow"] + ip + " "
                + ANSI["brightred"] + qnum + ANSI["darkpurple"]
                + (" " + name if name else ""))
    if tag == "CAH":
        return ANSI["pink"] + "CAH " + ANSI["yellow"] + ip + ANSI["darkpurple"] + (" " + rest if rest else "")
    return ANSI["white"] + line


def current_log_path():
    return os.path.join(LOG_DIR, time.strftime("log_%Y%m%d.txt"))


def resolve_display():
    """Resolve the display via mDNS. Returns its IP or None."""
    try:
        info = socket.getaddrinfo(DISPLAY_HOST, DISPLAY_PORT,
                                  socket.AF_INET, socket.SOCK_STREAM)
        return info[0][4][0]
    except OSError:
        return None


def open_uart():
    """Open the wired UART link to the display (best-effort).

    Tries a USB-UART adapter first, then the Pi's own GPIO14/15 UART
    (requires `sudo raspi-config nonint do_serial 2` + reboot). Returns a
    non-blocking fd or None when no device exists. Writes to a UART with
    nothing attached must never block, so the fd is O_NONBLOCK and
    BlockingIOError is swallowed by the caller.
    """
    import termios
    for path in ("/dev/ttyUSB0", "/dev/serial0", "/dev/ttyAMA0"):
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            attrs = termios.tcgetattr(fd)
            # Python's termios has no cfsetispeed - set the speed fields
            # directly before tcsetattr.
            attrs[4] = termios.B115200   # ispeed
            attrs[5] = termios.B115200   # ospeed
            # 8N1, raw
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
            attrs[2] |= termios.CS8
            attrs[3] = 0
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            return fd
        except OSError:
            continue
    return None


def send_both(sock, uart_fd, payload):
    """Send a payload to the TCP stream and, best-effort, the wired UART."""
    sock.sendall(payload)
    if uart_fd is not None:
        try:
            os.write(uart_fd, payload)
        except (BlockingIOError, OSError):
            pass   # UART not attached or buffer full - WiFi covers it


def stream_once(ip, uart_fd):
    """Stream the log to the display until the connection breaks."""
    sock = socket.create_connection((ip, DISPLAY_PORT), timeout=10)
    sock.settimeout(5)
    try:
        # Start from the current end of the log: only live lines are shown
        # (plus a greeting so the screen confirms the link is up).
        log_path = current_log_path()
        pos = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        send_both(sock, uart_fd, (ANSI["cyan"] + "[log] live stream attached\n").encode("utf-8"))
        tail = b""
        while True:
            log_path = current_log_path()
            if os.path.exists(log_path):
                size = os.path.getsize(log_path)
                if size < pos:   # log file rotated to a new day
                    pos = 0
                    tail = b""
                if size > pos:
                    with open(log_path, "rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                        pos = size
                    data = tail + chunk
                    *complete, tail = data.split(b'\n')
                    out = []
                    for raw in complete:
                        line = raw.decode("utf-8", "replace")
                        line = LINE_PREFIX.sub("", line)
                        out.append(colorize_line(line))
                    if out:
                        send_both(sock, uart_fd, "\n".join(out).encode("utf-8") + b"\n")
            time.sleep(0.5)
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main():
    print(f"[logstream] looking for {DISPLAY_HOST} ...", flush=True)
    uart_fd = open_uart()
    if uart_fd is not None:
        print("[logstream] wired UART link available", flush=True)
    else:
        print("[logstream] no UART device found - wifi only", flush=True)
    while True:
        ip = resolve_display()
        if ip is None:
            time.sleep(2)
            continue
        print(f"[logstream] display found at {ip} - streaming", flush=True)
        stream_once(ip, uart_fd)
        print("[logstream] connection lost - reconnecting", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()

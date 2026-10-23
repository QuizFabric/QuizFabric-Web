# Converts quiz images into a format the 3DS client can upload straight to
# its GPU (no decoder on-device): u16 width, u16 height, then the image as
# RGBA8 pixels SWIZZLED into the PICA200's native tiled layout (8x8 Morton
# blocks, blocks row-major), padded to power-of-two dimensions with white.
# The 3DS requests the converted copy by swapping the file extension for
# ".565" (e.g. photo.png -> photo.565).
#
# All functions are best-effort and never raise: if Pillow is missing or the
# image is unreadable, the web side keeps working and the 3DS just skips the
# picture.

import os
import struct

MAX_W, MAX_H = 256, 96


def _pow2(v):
    p = 8
    while p < v:
        p <<= 1
    return p


# The PICA200's tiled layout is not plain Morton: devkitPro's own tex3ds
# (source/swizzle.cpp) applies this exact permutation to the 64 row-major
# texel positions inside each 8x8 block. `_PERM[j]` = which row-major INPUT
# position tex3ds places at output position j; the hardware reads texel j
# from output position j.
def _build_perm():
    perm = list(range(64))
    # Swizzle each foursome: values rotate a<-b, b<-c, c<-d, d<-a.
    table = [
        (2, 8, 16, 4), (3, 9, 17, 5), (6, 10, 24, 20),
        (7, 11, 25, 21), (14, 26, 28, 22), (15, 27, 29, 23),
        (34, 40, 48, 36), (35, 41, 49, 37), (38, 42, 56, 52),
        (39, 43, 57, 53), (46, 58, 60, 54), (47, 59, 61, 55),
    ]
    for a, b, c, d in table:
        perm[a], perm[b], perm[c], perm[d] = perm[b], perm[c], perm[d], perm[a]
    # (un)swizzle each pair.
    for a, b in ((12, 18), (13, 19), (44, 50), (45, 51)):
        perm[a], perm[b] = perm[b], perm[a]
    return perm

_PERM = _build_perm()


def _tile_rgba8(im, pw, ph):
    """Returns pw*ph*4 bytes in PICA200 tiled order, padded with opaque white."""
    px = im.load()
    w, h = im.size
    out = bytearray(pw * ph * 4)
    for by in range(ph >> 3):
        for bx in range(pw >> 3):
            base = (by * (pw >> 3) + bx) * 64
            for j in range(64):  # output position within the block
                s = _PERM[j]     # source position within the block
                sx = bx * 8 + (s % 8)
                sy = by * 8 + (s // 8)
                if sx < w and sy < h:
                    r, g, b, a = px[sx, sy]
                else:
                    r, g, b, a = 255, 255, 255, 255
                off = (base + j) * 4
                # The PICA200 stores RGBA8 texels as A,B,G,R in memory (see
                # tex3ds' source/encode.cpp rgba8888()).
                out[off:off + 4] = bytes((a, b, g, r))
    return bytes(out)


def converted_path(original_path):
    """The .565 filename a client derives from an original image path."""
    root, _ = os.path.splitext(original_path)
    return root + ".565"


def png_to_rgb8(src_path, dst_path, max_w=MAX_W, max_h=MAX_H):
    """Converts src_path (any Pillow-supported format) to the raw tiled format.

    Animated GIFs (and APNGs) become a multi-frame file: u16 w, u16 h,
    u16 frame_count, u16 delay (centiseconds), then frame_count tiled RGBA8
    payloads. EVERY frame is kept (capped at 2048) so the whole GIF plays;
    frames are capped at 128x128. Static images keep the 4-byte header.
    This is the QUESTION-image format; list thumbnails use
    image_to_rgb8_animated_bytes instead."""
    try:
        from PIL import Image

        with Image.open(src_path) as im:
            n_frames = getattr(im, "n_frames", 1)
            if im.format in ("GIF", "PNG") and n_frames > 1:
                # Animated QUESTION image. EVERY frame is kept (the download
                # gets big, but the 3DS asks for the whole GIF and caches it
                # on its SD card). Frames are capped at 128x128. The delay is
                # a u16 (centiseconds) so long pauses work. The format is:
                # u16 w, u16 h, u16 frame_count, u16 delay_centis, then
                # frame_count tiled RGBA8 payloads.
                delays_ms = []
                for i in range(min(n_frames, 2048)):
                    try:
                        im.seek(i)
                    except Exception:
                        break
                    try:
                        # GIF: int ms per frame; APNG: may be a list.
                        d = im.info.get("duration", 100) or 100
                        if isinstance(d, (list, tuple)):
                            d = d[0] if d else 100
                        d = int(d)
                    except Exception:
                        d = 100
                    delays_ms.append(max(1, min(d, 60000)))
                n = len(delays_ms)
                if n >= 2:
                    total_cs = sum(max(1, round(d / 10)) for d in delays_ms)
                    delay = max(1, min(total_cs // max(n, 1), 65535))
                    frames = []
                    for i in range(n):
                        im.seek(i)
                        fr = im.convert("RGBA")
                        bg = Image.new("RGBA", fr.size, (255, 255, 255, 255))
                        bg.alpha_composite(fr)
                        fr = bg
                        fr.thumbnail((128, 128), Image.LANCZOS)
                        frames.append(fr)
                    size = frames[0].size
                    frames = [f if f.size == size else f.resize(size, Image.LANCZOS)
                              for f in frames]
                    w, h = size
                    pw, ph = _pow2(w), _pow2(h)
                    data = b"".join(_tile_rgba8(f, pw, ph) for f in frames)
                    with open(dst_path, "wb") as f:
                        f.write(struct.pack("<HHHH", w, h, len(frames), delay))
                        f.write(data)
                    return True

            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg  # already RGBA, opaque background applied
            im.thumbnail((max_w, max_h), Image.LANCZOS)
            w, h = im.size
            pw, ph = _pow2(w), _pow2(h)
            data = _tile_rgba8(im, pw, ph)
        with open(dst_path, "wb") as f:
            f.write(struct.pack("<HH", w, h))
            f.write(data)
        return True
    except Exception:
        return False


def image_to_rgb8_bytes(im, max_w=MAX_W, max_h=MAX_H):
    """Converts a PIL image (any mode, any size) into the complete .565
    payload (u16 w, u16 h header + tiled RGBA8) as bytes. Static images
    only - an animated GIF yields its first frame. Used for quiz banners
    and the Kahoot cover proxy where no file ever touches disk."""
    try:
        from PIL import Image
    except ImportError:
        return b""
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    im = bg
    im.thumbnail((max_w, max_h), Image.LANCZOS)
    w, h = im.size
    pw, ph = _pow2(w), _pow2(h)
    return struct.pack("<HH", w, h) + _tile_rgba8(im, pw, ph)


def image_to_rgb8_animated_bytes(im, max_w=MAX_W, max_h=MAX_H):
    """Like image_to_rgb8_bytes but keeps animation when the source has
    frames: u16 w, u16 h, u8 frame_count, u8 delay (centiseconds), then
    frame_count tiled RGBA8 payloads. Falls back to the static format for
    single-frame images. Used for the 64x64 list thumbnails and the big
    cover preview."""
    try:
        from PIL import Image
    except ImportError:
        return b""
    try:
        n_frames = getattr(im, "n_frames", 1)
    except Exception:
        n_frames = 1
    if n_frames <= 1 or im.format not in ("GIF", "PNG"):
        return image_to_rgb8_bytes(im, max_w, max_h)
    frames = []
    delays = []
    for i in range(min(n_frames, 24)):
        try:
            im.seek(i)
        except Exception:
            break
        fr = im.convert("RGBA")
        bg = Image.new("RGBA", fr.size, (255, 255, 255, 255))
        bg.alpha_composite(fr)
        fr = bg
        fr.thumbnail((max_w, max_h), Image.LANCZOS)
        frames.append(fr)
        try:
            # GIF: int ms per frame; APNG: may be a list.
            d = im.info.get("duration", 100) or 100
            if isinstance(d, (list, tuple)):
                d = d[0] if d else 100
            d = max(1, min(round(int(d) / 10), 255))
        except Exception:
            d = 10
        delays.append(d)
    if len(frames) < 2:
        return image_to_rgb8_bytes(im, max_w, max_h)
    size = frames[0].size
    frames = [f if f.size == size else f.resize(size, Image.LANCZOS)
              for f in frames]
    w, h = size
    pw, ph = _pow2(w), _pow2(h)
    avg_delay = max(1, min(sum(delays) // len(delays), 255))
    data = b"".join(_tile_rgba8(f, pw, ph) for f in frames)
    return struct.pack("<HHBB", w, h, len(frames), avg_delay) + data


def _needs_reconversion(path):
    """True when `path` is an animated .565 written by an older converter:
    6/7-byte legacy headers (24-frame cap - only plays a few seconds), or
    8-byte files with frames larger than 128x128 (the 256px-era converter).
    These must be regenerated."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        if len(head) < 8:
            return False
        w, h = struct.unpack("<HH", head[:4])
        if w < 1 or h < 1:
            return False
        pw, ph = _pow2(w), _pow2(h)
        fb = pw * ph * 4
        size = os.path.getsize(path)
        n8 = head[4]
        n16 = head[4] | (head[5] << 8)
        if 2 <= n16 <= 2048 and size == 8 + n16 * fb:
            # Current format: regenerate only copies with oversized frames
            # (from the 256px-era converter).
            return w > 128 or h > 128
        if 2 <= n8 <= 24 and size in (6 + n8 * fb, 7 + n8 * fb):
            return True
        return False
    except OSError:
        return False


def convert_on_demand(dst_565):
    """Creates dst_565 from a sibling image of any supported extension, if one
    exists. Used by the image-serving route so every uploaded image works on
    the 3DS without a separate conversion step. Regenerates when the copy is
    missing, older than its source, or written by an old converter (so stale
    truncated or oversized GIF copies heal themselves on the next request)."""
    base, _ = os.path.splitext(dst_565)
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        src = base + ext
        if os.path.exists(src):
            need = True
            if os.path.exists(dst_565):
                try:
                    fresh = os.path.getmtime(src) <= os.path.getmtime(dst_565)
                except OSError:
                    fresh = False
                need = (not fresh) or _needs_reconversion(dst_565)
            if need:
                png_to_rgb8(src, dst_565)
            return


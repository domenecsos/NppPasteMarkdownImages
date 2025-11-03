from Npp import notepad, editor
import os, re, datetime, ctypes, ctypes.wintypes as wt

LPBYTE = ctypes.POINTER(ctypes.c_ubyte)

# ----- Win32 constants -----
CF_BITMAP  = 2
CF_DIB     = 8
CF_DIBV5   = 17

# ----- ctypes prototypes (64-bit safe) -----
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdiplus = ctypes.windll.gdiplus

# user32
user32.OpenClipboard.argtypes  = [wt.HWND]
user32.OpenClipboard.restype   = wt.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype  = wt.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.IsClipboardFormatAvailable.restype  = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype  = wt.HANDLE

# kernel32
kernel32.GlobalLock.argtypes   = [wt.HGLOBAL]
kernel32.GlobalLock.restype    = wt.LPVOID
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype  = wt.BOOL

# ----- GDI+ setup -----
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", wt.ULONG),
        ("DebugEventCallback", wt.LPVOID),
        ("SuppressBackgroundThread", wt.BOOL),
        ("SuppressExternalCodecs", wt.BOOL),
    ]

# ImageCodecInfo (only the fields we need)
class ImageCodecInfo(ctypes.Structure):
    _fields_ = [
        ("Clsid", GUID),
        ("FormatID", GUID),
        ("CodecName", wt.LPWSTR),
        ("DllName", wt.LPWSTR),
        ("FormatDescription", wt.LPWSTR),
        ("FilenameExtension", wt.LPWSTR),
        ("MimeType", wt.LPWSTR),
        ("Flags", wt.DWORD),
        ("Version", wt.DWORD),
        ("SigCount", wt.DWORD),
        ("SigSize", wt.DWORD),
        ("SigPattern", LPBYTE),
        ("SigMask", LPBYTE),
    ]

# Prototypes
gdiplus.GdiplusStartup.argtypes = [ctypes.POINTER(wt.ULONG), ctypes.POINTER(GdiplusStartupInput), wt.LPVOID]
gdiplus.GdiplusStartup.restype  = wt.ULONG
gdiplus.GdiplusShutdown.argtypes = [wt.ULONG]

gdiplus.GdipCreateBitmapFromGdiDib.argtypes = [wt.LPVOID, wt.LPVOID, ctypes.POINTER(wt.LPVOID)]
gdiplus.GdipCreateBitmapFromGdiDib.restype  = wt.ULONG
gdiplus.GdipCreateBitmapFromHBITMAP.argtypes = [wt.HBITMAP, wt.HPALETTE, ctypes.POINTER(wt.LPVOID)]
gdiplus.GdipCreateBitmapFromHBITMAP.restype  = wt.ULONG
gdiplus.GdipSaveImageToFile.argtypes = [wt.LPVOID, wt.LPWSTR, ctypes.POINTER(GUID), wt.LPVOID]
gdiplus.GdipSaveImageToFile.restype  = wt.ULONG
gdiplus.GdipDisposeImage.argtypes    = [wt.LPVOID]
gdiplus.GdipDisposeImage.restype     = wt.ULONG
gdiplus.GdipGetImageEncodersSize.argtypes = [ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT)]
gdiplus.GdipGetImageEncodersSize.restype  = wt.ULONG
gdiplus.GdipGetImageEncoders.argtypes = [wt.UINT, wt.UINT, wt.LPVOID]
gdiplus.GdipGetImageEncoders.restype  = wt.ULONG

def gdip_start():
    token = wt.ULONG(0)
    si = GdiplusStartupInput(1, None, False, False)
    status = gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(si), None)
    if status != 0:
        return None
    return token

def gdip_shutdown(token):
    if token:
        gdiplus.GdiplusShutdown(token)

def get_encoder_clsid(mime="image/png"):
    num = wt.UINT(0)
    size = wt.UINT(0)
    if gdiplus.GdipGetImageEncodersSize(ctypes.byref(num), ctypes.byref(size)) != 0 or size.value == 0:
        return None
    buf = (ctypes.c_byte * size.value)()
    if gdiplus.GdipGetImageEncoders(num, size, ctypes.byref(buf)) != 0:
        return None
    # Cast buffer to array of ImageCodecInfo
    arr = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(ImageCodecInfo))
    for i in range(num.value):
        info = arr[i]
        if info.MimeType and info.MimeType.lower() == mime:
            return info.Clsid
    return None

# ----- helpers -----
def sanitize_filename(name):
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', name)
    return name.rstrip(' .')

def dedupe_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while True:
        cand = "{}-{}{}".format(base, i, ext)
        if not os.path.exists(cand):
            return cand
        i += 1

def save_clipboard_image_as_png(target_path):
    """Returns True if saved, False to indicate 'no image' so caller can fallback to text paste."""
    if not user32.OpenClipboard(None):
        return False
    try:
        token = gdip_start()
        if not token:
            return False

        try:
            img_handle = None  # will hold a ctypes.c_void_p if created

            # Try DIBV5
            if user32.IsClipboardFormatAvailable(CF_DIBV5):
                h = user32.GetClipboardData(CF_DIBV5)
                if h:
                    p = kernel32.GlobalLock(h)
                    if p:
                        tmp = wt.LPVOID()
                        status = gdiplus.GdipCreateBitmapFromGdiDib(p, None, ctypes.byref(tmp))
                        kernel32.GlobalUnlock(h)
                        if status == 0 and tmp:
                            img_handle = tmp

            # Try DIB
            if (not img_handle) and user32.IsClipboardFormatAvailable(CF_DIB):
                h = user32.GetClipboardData(CF_DIB)
                if h:
                    p = kernel32.GlobalLock(h)
                    if p:
                        tmp = wt.LPVOID()
                        status = gdiplus.GdipCreateBitmapFromGdiDib(p, None, ctypes.byref(tmp))
                        kernel32.GlobalUnlock(h)
                        if status == 0 and tmp:
                            img_handle = tmp

            # Fallback: CF_BITMAP
            if (not img_handle) and user32.IsClipboardFormatAvailable(CF_BITMAP):
                hbm = user32.GetClipboardData(CF_BITMAP)
                if hbm:
                    tmp = wt.LPVOID()
                    status = gdiplus.GdipCreateBitmapFromHBITMAP(hbm, None, ctypes.byref(tmp))
                    if status == 0 and tmp:
                        img_handle = tmp

            if not img_handle:
                gdiplus.GdiplusShutdown(token)
                return False  # no usable image in clipboard

            # Save PNG
            clsid = get_encoder_clsid("image/png")
            if not clsid:
                gdiplus.GdipDisposeImage(img_handle)
                gdiplus.GdiplusShutdown(token)
                return False

            path_w = wt.LPWSTR(target_path)
            status = gdiplus.GdipSaveImageToFile(img_handle, path_w, ctypes.byref(clsid), None)

            gdiplus.GdipDisposeImage(img_handle)
            gdiplus.GdiplusShutdown(token)
            return status == 0

        finally:
            # safety: ensure we always shut GDI+ down
            try:
                gdiplus.GdiplusShutdown(token)
            except:
                pass

    finally:
        user32.CloseClipboard()


def capitalizeDescription(s):
    # s = s.lower()
    result = []
    capitalize_next = False

    for ch in s:
        if ch == " ":
            capitalize_next = True
        else:
            if capitalize_next:
                result.append(ch.upper())
                capitalize_next = False
            else:
                result.append(ch)
    
    return "".join(result)


def paste_image_or_text_with_prompt():
    # Decide paths
    current_file = notepad.getCurrentFilename()
    base_dir = os.path.dirname(current_file) if current_file else os.path.expanduser("~/Desktop")
    folder = os.path.join(base_dir, "img")
    if not os.path.isdir(folder):
        os.makedirs(folder)

    # Ask description
    default_name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") 
    description = notepad.prompt("Enter image description:", "Write markdown alt text for image", default_name)    

    # Ask filename
    default_name = capitalizeDescription(description) + ".png"
    name = notepad.prompt("Enter filename for the image (without path):", "Save Clipboard Image", default_name)
    if name is None:
        return
    name = sanitize_filename(name)
    if not name.lower().endswith(".png"):
        name += ".png"
    fullpath = dedupe_path(os.path.join(folder, name))

    # Try save clipboard image
    if save_clipboard_image_as_png(fullpath):
        rel = os.path.relpath(fullpath, start=base_dir).replace("\\", "/")
        # alt = os.path.splitext(os.path.basename(fullpath))[0]
        alt = description
        editor.addText("![{}]({})".format(alt, rel))
    else:
        editor.paste()

paste_image_or_text_with_prompt()

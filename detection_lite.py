"""
DetectionLite - ระบบตรวจจับภาพหน้าจอและคำสั่งสัมผัสสำหรับรันบน Redfinger / Android
รองรับทั้งโหมด Native บนมือถือ (screencap/input) และโหมด ADB สำหรับทดสอบบน LDPlayer
"""

import os
import sys
import time
import random
import shutil
import subprocess
import cv2
import numpy as np
from typing import Optional, Tuple, List, Dict

# Prevent console encoding issues on Windows
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

# Cache templates in RAM
TEMPLATES: Dict[str, np.ndarray] = {}
ADB_TARGET: Optional[str] = None
ADB_PATH: Optional[str] = None


def get_adb_executable() -> str:
    global ADB_PATH
    if ADB_PATH and os.path.isfile(ADB_PATH):
        return ADB_PATH

    which_adb = shutil.which("adb")
    if which_adb:
        ADB_PATH = which_adb
        return which_adb

    candidates = [
        r"C:\LDPlayer\LDPlayer14\adb.exe",
        r"C:\LDPlayer\LDPlayer12\adb.exe",
        r"C:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\LDPlayer\LDPlayer4.0\adb.exe",
        r"C:\leidian\LDPlayer14\adb.exe",
        r"C:\leidian\LDPlayer9\adb.exe",
        r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            ADB_PATH = p
            return p
    return "adb"


def set_adb_target(target: Optional[str]):
    global ADB_TARGET
    ADB_TARGET = target
    if target:
        print(f"🔌 [DetectionLite] Using ADB Target: {target} via {get_adb_executable()}")


_root_checked = False
_has_root = False
ACTUAL_SCREEN_W = 1280
ACTUAL_SCREEN_H = 720
_last_cap_err = 0.0
_has_logged_success_cap = False


def check_root() -> bool:
    global _root_checked, _has_root
    if _root_checked:
        return _has_root
    try:
        p = subprocess.run(["su", "-c", "id"], capture_output=True, timeout=3)
        if p.returncode == 0 and (b"uid=0" in p.stdout or b"root" in p.stdout):
            _has_root = True
            _root_checked = True
            print("👑 [Root] Root access confirmed (uid=0)!")
            return True
        else:
            print(f"⚠️ [Root] 'su' check code {p.returncode}: {p.stderr.decode('utf-8', 'ignore')}")
    except Exception as e:
        print(f"⚠️ [Root] 'su' check: {e}")
    _has_root = False
    _root_checked = True
    print("⚠️ [Root] No root available via su. If capture fails, please grant Root in LDPlayer/Redfinger!")
    return False


def load_templates():
    if not os.path.exists(TEMPLATE_DIR):
        print(f"❌ [Template] Dir not found: {TEMPLATE_DIR}")
        return
    count = 0
    for fname in os.listdir(TEMPLATE_DIR):
        if fname.lower().endswith((".png", ".jpg")):
            path = os.path.join(TEMPLATE_DIR, fname)
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                TEMPLATES[fname] = img
                count += 1
    print(f"🖼️ [Template] Loaded {count} templates successfully.")


# Try importing ScreenBridge from Android Kotlin runtime
try:
    from com.cookierun.cloudbot import ScreenBridge
except Exception:
    ScreenBridge = None


def capture_screen_native() -> Optional[np.ndarray]:
    """Captures screenshot via No-Root ScreenBridge (MediaProjection) or ADB / Root fallback."""
    global _last_cap_err, _has_logged_success_cap, ACTUAL_SCREEN_W, ACTUAL_SCREEN_H
    now = time.time()

    # Priority 1: No-Root ScreenBridge (MediaProjection)
    if ScreenBridge:
        bridge = getattr(ScreenBridge, "INSTANCE", ScreenBridge)
        try:
            if bridge.isReady():
                jpg_bytes = bridge.getLatestFrameJpeg()
                if jpg_bytes is not None:
                    arr = np.frombuffer(bytes(jpg_bytes), dtype=np.uint8)
                    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if img is not None:
                        ACTUAL_SCREEN_H, ACTUAL_SCREEN_W = img.shape[:2]
                        if not _has_logged_success_cap:
                            _has_logged_success_cap = True
                            print(f"📸 [No-Root ScreenBridge] Capture active ({ACTUAL_SCREEN_W}x{ACTUAL_SCREEN_H})")
                        return img
        except Exception as e:
            if now - _last_cap_err > 5.0:
                _last_cap_err = now
                print(f"⚠️ [ScreenBridge Error] {e}")

    # Priority 2: PC ADB mode
    if ADB_TARGET:
        try:
            cmd = [get_adb_executable(), "-s", ADB_TARGET, "exec-out", "screencap", "-p"]
            proc = subprocess.run(cmd, capture_output=True, timeout=2)
            if proc.returncode == 0 and proc.stdout:
                img_bytes = proc.stdout.replace(b"\r\n", b"\n")
                img = cv2.imdecode(np.frombuffer(img_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    ACTUAL_SCREEN_H, ACTUAL_SCREEN_W = img.shape[:2]
                    return img
        except Exception as e:
            if now - _last_cap_err > 5.0:
                _last_cap_err = now
                print(f"❌ [ADB Capture Error] {e}")
        return None

    # Priority 3: Root Fallback
    is_root = check_root()
    if is_root:
        try:
            proc = subprocess.run(["su", "-c", "screencap -p"], capture_output=True, timeout=2)
            if proc.returncode == 0 and proc.stdout:
                data = proc.stdout
                idx = data.find(b"\x89PNG\r\n\x1a\n")
                if idx != -1:
                    data = data[idx:]
                img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    data_fixed = data.replace(b"\r\n", b"\n")
                    img = cv2.imdecode(np.frombuffer(data_fixed, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    ACTUAL_SCREEN_H, ACTUAL_SCREEN_W = img.shape[:2]
                    return img
        except Exception:
            pass

        try:
            tmp_file = "/sdcard/sc_cookierun.png"
            p = subprocess.run(["su", "-c", f"screencap -p {tmp_file} && chmod 666 {tmp_file}"], capture_output=True, timeout=3)
            if p.returncode == 0 and os.path.exists(tmp_file):
                img = cv2.imread(tmp_file)
                if img is not None:
                    ACTUAL_SCREEN_H, ACTUAL_SCREEN_W = img.shape[:2]
                    return img
        except Exception:
            pass

    if now - _last_cap_err > 5.0:
        _last_cap_err = now
        print("⏳ [Capture] Waiting for ScreenBridge / MediaProjection frame...")
    return None


def tap_native(x: int, y: int, jitter: bool = True):
    """Sends humanized tap event with Gaussian coordinate jitter and realistic touch hold duration."""
    global ACTUAL_SCREEN_W, ACTUAL_SCREEN_H
    
    # Anti-Cheat / Humanization: Add natural Gaussian finger touch jitter (clamped to +/- 18 pixels)
    if jitter:
        gx = int(max(-18, min(18, round(random.gauss(0, 5.5)))))
        gy = int(max(-18, min(18, round(random.gauss(0, 5.5)))))
        jx = max(5, min(1275, x + gx))
        jy = max(5, min(715, y + gy))
    else:
        jx, jy = x, y

    real_x = int(jx * ACTUAL_SCREEN_W / 1280)
    real_y = int(jy * ACTUAL_SCREEN_H / 720)
    hold_ms = random.randint(65, 125)

    # Priority 1: No-Root AccessibilityService
    if ScreenBridge:
        bridge = getattr(ScreenBridge, "INSTANCE", ScreenBridge)
        try:
            if bridge.isAccessibilityReady():
                success = bridge.tap(real_x, real_y)
                if success:
                    return
        except Exception as e:
            print(f"⚠️ [Accessibility Tap Error] {e}")

    # Priority 2: PC ADB (with human touch hold duration via swipe)
    if ADB_TARGET:
        try:
            cmd = [get_adb_executable(), "-s", ADB_TARGET, "shell", "input", "swipe", str(real_x), str(real_y), str(real_x), str(real_y), str(hold_ms)]
            subprocess.run(cmd, check=False)
            return
        except Exception:
            pass

    # Priority 3: Root Fallback
    if check_root():
        try:
            subprocess.run(["su", "-c", f"input swipe {real_x} {real_y} {real_x} {real_y} {hold_ms}"], check=False)
            return
        except Exception:
            pass

    try:
        subprocess.run(["input", "swipe", str(real_x), str(real_y), str(real_x), str(real_y), str(hold_ms)], check=False)
    except Exception:
        pass


def match_template_in_region(screen_gray: np.ndarray, tmpl_name: str, region: Optional[Tuple[int, int, int, int]] = None, threshold: float = 0.78) -> bool:
    tmpl = TEMPLATES.get(tmpl_name)
    if tmpl is None or screen_gray is None:
        return False

    roi = screen_gray
    if region is not None:
        x1, y1, x2, y2 = region
        h, w = screen_gray.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            roi = screen_gray[y1:y2, x1:x2]

    th, tw = tmpl.shape[:2]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return False

    res = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)
    return max_val >= threshold


# Stage configurations with multi-template fallback (Specific states prioritized first)
STAGE_TEMPLATES: Dict[str, Tuple[List[str], Tuple[int, int, int, int]]] = {
    "ANTI_BOT": (["ANTI_BOT_1.png"], (309, 10, 968, 111)),
    "PURCHASE_ITEM": (["PURCHASE_ITEM_1.png"], (420, 75, 680, 175)),
    "GAME_COMPLETE": (["GAME_COMPLETE_1.png"], (523, 27, 757, 116)),
    "MYSTERY_BOX": (["MYSTERY_BOX_1.png"], (503, 46, 782, 107)),
    "CONGRATULATIONS": (["CONGRATULATIONS_1.png", "CONGRATULATIONS_2.png"], (372, 85, 909, 200)),
    "LEVEL_UP": (["LEVEL_UP_1.png"], (481, 34, 798, 130)),
    "PREVIOUS_RANK_RESULTS": (["PREVIOUS_RANK_RESULTS_1.png"], (454, 42, 832, 115)),
    "TOO_MANY_TREASURES": (["TOO_MANY_TREASURES_1.png"], (353, 154, 931, 476)),
    "OVERTAKE_BREAK_SCORE": (["OVERTAKE_BREAK_SCORE_1.png"], (479, 64, 799, 132)),
    "DAILY_CHECKIN": (["DAILY_CHECKIN_1.png"], (470, 20, 810, 90)),
    "DAILY_CHECKIN_BOOST_SET": (["DAILY_CHECKIN_BOOST_SET_1.png"], (510, 80, 770, 190)),
    "DAILY_TREASURE": (["DAILY_TREASURE_1.png"], (420, 100, 860, 220)),
    "DAILY_NEW": (["DAILY_NEW_1.png"], (420, 80, 860, 220)),
    "ENTER_LEAGUE": (["ENTER_LEAGUE_1.png"], (300, 100, 980, 320)),
    "CONNECTION_LOST": (["CONNECTION_LOST_1.png", "CONNECTION_LOST_2.png"], (350, 150, 930, 420)),
    "INACTIVE": (["INACTIVE_1.png"], (350, 150, 930, 350)),
    "DEVPLAY_LOGIN": (["DEVPLAY_LOGIN_1.png"], (400, 580, 850, 695)),
    "GAME_START": (["GAME_START_1.png"], (573, 265, 733, 427)),
    "GAME_RELAY": (["GAME_RELAY_1.png"], (573, 265, 733, 427)),
    "MAINMENU": (["MAINMENU_1.png", "MAINMENU_2.png"], (150, 140, 1060, 720)),
    "YOU_ARE_NOT_READY": (["YOU_ARE_NOT_READY_1.png"], (350, 200, 930, 500)),
}



# Anti-Bot 6-Card Coordinates (1280x720)
ANTI_BOT_CARD_COORDS = [
    (346, 179), (542, 179), (738, 179),
    (346, 435), (542, 435), (738, 435),
]
ANTI_BOT_CARD_WIDTH = 183
ANTI_BOT_CARD_HEIGHT = 241


def detect_anti_bot_odd_cards(screen: np.ndarray) -> List[int]:
    """Return 0-based indices of the 2 cards that differ from the majority 4."""
    if screen is None or screen.size == 0:
        return []
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen_1280 = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_1280 = screen

    crops = []
    for cx, cy in ANTI_BOT_CARD_COORDS:
        crop = screen_1280[cy:cy + ANTI_BOT_CARD_HEIGHT, cx:cx + ANTI_BOT_CARD_WIDTH]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        crops.append(gray)

    n = len(crops)
    sim = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                result = cv2.matchTemplate(crops[i], crops[j], cv2.TM_CCOEFF_NORMED)
                sim[i][j] = result[0][0]

    avg_sim = sim.sum(axis=1) / (n - 1)
    odd_indices = [int(x) for x in np.argsort(avg_sim)[:2]]
    return odd_indices


def solve_anti_bot_captcha(screen: np.ndarray) -> List[int]:
    """Solves Anti-Bot captcha by tapping the 2 odd cards with natural human delays."""
    odd_indices = detect_anti_bot_odd_cards(screen)
    if not odd_indices or len(odd_indices) < 2:
        return []

    print(f"🃏 [Anti-Bot] Found odd cards: Card #{odd_indices[0] + 1} and Card #{odd_indices[1] + 1}")
    for idx in odd_indices:
        cx, cy = ANTI_BOT_CARD_COORDS[idx]
        margin_x = 25
        margin_y = 30
        tx = random.randint(cx + margin_x, cx + ANTI_BOT_CARD_WIDTH - margin_x)
        ty = random.randint(cy + margin_y, cy + ANTI_BOT_CARD_HEIGHT - margin_y)
        print(f"  👆 Tapping odd Card #{idx + 1} at ({tx}, {ty})...")
        tap_native(tx, ty)
        time.sleep(random.uniform(0.9, 1.5))

    print("✅ [Anti-Bot] Captcha solved successfully!")
    return odd_indices


def is_item_checked(screen: np.ndarray, item_type: str) -> bool:
    """
    Checks if the item checkbox is checked (has bright green checkmark ✓).
    item_type: 'cookie_relay' or 'fast_start'
    """
    if screen is None or screen.size == 0:
        return False
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen_1280 = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_1280 = screen

    if item_type == "cookie_relay":
        crop = screen_1280[665:700, 425:465]
    elif item_type == "fast_start":
        crop = screen_1280[665:700, 305:345]
    else:
        return False

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 120, 100]), np.array([85, 255, 255]))
    return bool(np.sum(mask > 0) >= 200)


def detect_devplay_login(screen: np.ndarray) -> bool:
    """
    Detects if the DevPlay Login title screen is visible.
    Immune to false positives by verifying template matching in region (400, 580, 850, 695).
    """
    if screen is None or screen.size == 0:
        return False
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen_1280 = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_1280 = screen

    gray = cv2.cvtColor(screen_1280, cv2.COLOR_BGR2GRAY) if len(screen_1280.shape) == 3 else screen_1280
    return match_template_in_region(gray, "DEVPLAY_LOGIN_1.png", (400, 580, 850, 695), threshold=0.80)


def detect_current_stage(screen: np.ndarray, in_game: bool = False) -> Optional[str]:
    if screen is None or screen.size == 0:
        return None
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if len(screen.shape) == 3 else screen

    # When in-game: strictly filter stages to prevent any false positives triggering jumps/slides
    # Crucial: Must include CONNECTION_LOST, INACTIVE, DEVPLAY_LOGIN so network disconnects are handled!
    if in_game:
        in_game_stages = ["ANTI_BOT", "CONNECTION_LOST", "INACTIVE", "DEVPLAY_LOGIN", "GAME_RELAY", "GAME_COMPLETE"]
        for stage_name in in_game_stages:
            if stage_name in STAGE_TEMPLATES:
                tmpls, region = STAGE_TEMPLATES[stage_name]
                if stage_name == "GAME_RELAY":
                    thresh = 0.68  # Robust match against pulsating glow animation
                elif stage_name in ("CONNECTION_LOST", "INACTIVE", "DEVPLAY_LOGIN"):
                    thresh = 0.78
                else:
                    thresh = 0.82
                for tname in tmpls:
                    if match_template_in_region(gray, tname, region, threshold=thresh):
                        return stage_name
        return None

    for stage_name, (tmpls, region) in STAGE_TEMPLATES.items():
        for tname in tmpls:
            if match_template_in_region(gray, tname, region):
                return stage_name
    return None


def detect_first_box(screen: np.ndarray) -> bool:
    """Detects in-game mystery box drop notification with banner & x1 icon fallback."""
    if screen is None or screen.size == 0:
        return False
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if len(screen.shape) == 3 else screen
    # Region: y: 220..310, x: 70..230
    box_region = (70, 220, 230, 310)
    if match_template_in_region(gray, "INGAME_BOX_NOTIFICATION.png", box_region, threshold=0.74):
        return True
    if match_template_in_region(gray, "INGAME_BOX_X1.png", box_region, threshold=0.74):
        return True
    return False


POPUP_DISMISS_TEMPLATES = [
    ("cancel_grey",       "POPUP_CANCEL_GREY_BTN.png",   0.70, (150, 300, 750, 700)),
    ("confirm_send_life", "CONFIRM_SEND_LIFE_1.png",     0.75, (550, 320, 1050, 650)),
    ("universal_close_x", "UNIVERSAL_POPUP_CLOSE_X.png", 0.75, (700, 10, 1260, 260)),
    ("dialog_close_x",    "DIALOG_CLOSE_X.png",          0.75, (700, 10, 1260, 260)),
    ("close_round_x",     "POPUP_CLOSE_ROUND_X.png",     0.75, (700, 10, 1260, 260)),
    ("confirm_green",     "POPUP_CONFIRM_GREEN_BTN.png", 0.75, (300, 350, 980, 680)),
    ("confirm_reward",    "POPUP_CONFIRM_REWARD.png",    0.75, (300, 350, 980, 680)),
    ("confirm_board",     "POPUP_CONFIRM_BOARD.png",     0.75, (300, 450, 980, 715)),
]


def is_level_up_popup(screen: np.ndarray) -> bool:
    """Checks if the Level Up popup header or title is visible on screen."""
    if screen is None or screen.size == 0:
        return False
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if len(screen.shape) == 3 else screen
    tmpl = TEMPLATES.get("LEVEL_UP_1.png")
    if tmpl is None:
        return False

    roi = gray[20:180, 400:880]
    for scale in [0.88, 0.95, 1.0, 1.05, 1.12]:
        t_scaled = tmpl if scale == 1.0 else cv2.resize(tmpl, (0, 0), fx=scale, fy=scale)
        th, tw = t_scaled.shape[:2]
        if roi.shape[0] < th or roi.shape[1] < tw:
            continue
        res = cv2.matchTemplate(roi, t_scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        if max_val >= 0.72:
            return True
    return False


def find_popup_dismiss_action(screen: np.ndarray, is_mainmenu: bool = False) -> Optional[Tuple[int, int]]:
    """Detects any popup or modal dismiss buttons (Cancel, Close X, OK, Confirm) to prevent getting stuck."""
    if screen is None or screen.size == 0:
        return None
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if len(screen.shape) == 3 else screen

    # Check if this is a Level Up popup - if so, confirm it directly!
    if is_level_up_popup(screen):
        print("⭐ [Popup Dismiss] LEVEL_UP popup detected! Finding Confirm button...")
        return find_level_up_confirm(screen)

    for action_name, filename, threshold, (x1, y1, x2, y2) in POPUP_DISMISS_TEMPLATES:
        # On MAINMENU: only allow Close X and Cancel buttons, never Confirm buttons
        if is_mainmenu and action_name.startswith("confirm"):
            continue

        tmpl = TEMPLATES.get(filename)
        if tmpl is None:
            continue
        roi = gray[y1:y2, x1:x2]
        
        scales = [0.85, 0.92, 1.0, 1.08, 1.15] if action_name in ("cancel_grey", "confirm_send_life", "confirm_green") else [1.0]
        for scale in scales:
            t_scaled = tmpl if scale == 1.0 else cv2.resize(tmpl, (0, 0), fx=scale, fy=scale)
            th, tw = t_scaled.shape[:2]
            if roi.shape[0] < th or roi.shape[1] < tw:
                continue
            res = cv2.matchTemplate(roi, t_scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val >= threshold:
                cx = x1 + max_loc[0] + tw // 2
                cy = y1 + max_loc[1] + th // 2
                if action_name == "confirm_send_life":
                    # Only offset to Cancel if Confirm is on the right (cx >= 700) and NOT Level Up
                    if cx >= 700:
                        cancel_x = max(100, int(cx - 314 * scale))
                        print(f"📋 [Popup Dismiss] Found 'confirm_send_life' at ({cx}, {cy}) -> Tapping Cancel button at ({cancel_x}, {cy}) [score: {max_val:.2f}, scale: {scale}]")
                        return (cancel_x, cy)
                    else:
                        print(f"⭐ [Popup Dismiss] Centered Confirm button at ({cx}, {cy}) -> Tapping Confirm directly [score: {max_val:.2f}, scale: {scale}]")
                        return (cx, cy)
                print(f"📋 [Popup Dismiss] Found '{action_name}' ({filename}) at ({cx}, {cy}) [score: {max_val:.2f}, scale: {scale}]")
                return (cx, cy)
    return None


def find_level_up_confirm(screen: np.ndarray) -> Tuple[int, int]:
    """Finds the green Confirm button on the Level Up popup screen.
    
    Returns (cx, cy) of the Confirm button to tap, falling back to (640, 630).
    """
    if screen is None or screen.size == 0:
        return (640, 630)
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if len(screen.shape) == 3 else screen

    # Try matching both POPUP_CONFIRM_GREEN_BTN and CONFIRM_SEND_LIFE_1
    candidate_templates = [
        "POPUP_CONFIRM_GREEN_BTN.png",
        "CONFIRM_SEND_LIFE_1.png",
        "POPUP_CONFIRM_REWARD.png",
        "POPUP_CONFIRM_BUTTON.png",
    ]
    x1, y1, x2, y2 = 300, 380, 980, 720
    roi = gray[y1:y2, x1:x2]

    best_val = 0.0
    best_loc = None

    for tname in candidate_templates:
        tmpl = TEMPLATES.get(tname)
        if tmpl is None:
            continue
        for scale in [0.85, 0.92, 1.0, 1.08, 1.15, 1.2]:
            t_scaled = tmpl if scale == 1.0 else cv2.resize(tmpl, (0, 0), fx=scale, fy=scale)
            th, tw = t_scaled.shape[:2]
            if roi.shape[0] < th or roi.shape[1] < tw:
                continue
            res = cv2.matchTemplate(roi, t_scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_val:
                best_val = max_val
                best_loc = (x1 + max_loc[0] + tw // 2, y1 + max_loc[1] + th // 2)

    if best_val >= 0.70 and best_loc is not None:
        print(f"⭐ [LevelUp Confirm] Found Confirm button at {best_loc} [score: {best_val:.2f}]")
        return best_loc

    print(f"⭐ [LevelUp Confirm] Template not found (best score: {best_val:.2f}), using fallback coordinate (640, 630)")
    return (640, 630)


def restart_game_app(package: str = "com.devsisters.crg") -> bool:
    """Restarts CookieRun app using No-Root Android Intent via ScreenBridge, with Root/ADB fallbacks."""
    print(f"🔄 [RestartApp] Attempting to restart {package}...")

    # Priority 1: ScreenBridge (In-App Kotlin No-Root + Accessibility + Intent)
    if ScreenBridge:
        bridge = getattr(ScreenBridge, "INSTANCE", ScreenBridge)
        try:
            if hasattr(bridge, "restartGameApp"):
                ok = bridge.restartGameApp(package)
                if ok:
                    print(f"✅ [RestartApp] Successfully relaunched {package} via ScreenBridge!")
                    return True
        except Exception as e:
            print(f"⚠️ [RestartApp] ScreenBridge error: {e}")

    # Priority 2: PC ADB (if ADB_TARGET set)
    if ADB_TARGET:
        adb = get_adb_executable()
        try:
            subprocess.run([adb, "-s", ADB_TARGET, "shell", "am", "force-stop", package], timeout=5, check=False)
            time.sleep(1.5)
            subprocess.run([adb, "-s", ADB_TARGET, "shell", "am", "start", "-n", f"{package}/com.devsisters.CookieRunForKakao.OvenbreakX"], timeout=5, check=False)
            print(f"✅ [RestartApp] Successfully restarted {package} via ADB ({ADB_TARGET})")
            return True
        except Exception as e:
            print(f"⚠️ [RestartApp] ADB restart failed: {e}")

    # Priority 3: On-device Root
    if check_root():
        try:
            subprocess.run(["su", "-c", f"am force-stop {package}"], timeout=5, check=False)
            time.sleep(1.5)
            subprocess.run(["su", "-c", f"am start -n {package}/com.devsisters.CookieRunForKakao.OvenbreakX"], timeout=5, check=False)
            print(f"✅ [RestartApp] Successfully restarted {package} via root su")
            return True
        except Exception as e:
            print(f"⚠️ [RestartApp] Root restart failed: {e}")

    # Priority 4: Direct am start
    try:
        subprocess.run(["am", "start", "-n", f"{package}/com.devsisters.CookieRunForKakao.OvenbreakX"], timeout=5, check=False)
        print(f"✅ [RestartApp] Relaunched {package} via direct am start")
        return True
    except Exception as e:
        print(f"⚠️ [RestartApp] Fallback am start failed: {e}")

    return False


# =====================================================================
# MYSTERY BOX & TREASURE TICKET DETECTION (Genuine PC Bot Port)
# =====================================================================

BOX_GRADE_TEMPLATES = {
    "wood": "BOX_WOOD.png",
    "silver": "BOX_SILVER.png",
    "gold": "BOX_GOLD.png",
    "rainbow": "BOX_RAINBOW.png",
}


def detect_mystery_box_grades(screen: np.ndarray) -> List[str]:
    """
    Detects mystery box locations and accurately classifies their grades on the screen.
    Returns a list of grade strings (e.g. ['wood', 'silver', 'gold', 'rainbow']).
    """
    if screen is None or screen.size == 0:
        return []

    h_screen, w_screen = screen.shape[:2]
    if (w_screen, h_screen) != (1280, 720):
        screen_bgr = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_bgr = screen

    h_screen, w_screen = screen_bgr.shape[:2]
    y1, y2 = max(0, int(h_screen * 0.20)), min(h_screen, int(h_screen * 0.75))
    x1, x2 = max(0, int(w_screen * 0.15)), min(w_screen, int(w_screen * 0.85))
    search_crop = screen_bgr[y1:y2, x1:x2]

    templates = {}
    for grade, tmpl_fn in BOX_GRADE_TEMPLATES.items():
        t = TEMPLATES.get(tmpl_fn)
        if t is not None:
            templates[grade] = t

    if not templates:
        return []

    search_gray = cv2.cvtColor(search_crop, cv2.COLOR_BGR2GRAY) if len(search_crop.shape) == 3 else search_crop

    all_matches = []
    for grade, tmpl in templates.items():
        for scale in [0.95, 1.0, 1.05, 1.1, 1.15]:
            t_scaled = tmpl if scale == 1.0 else cv2.resize(tmpl, (0, 0), fx=scale, fy=scale)
            if t_scaled.shape[0] > search_gray.shape[0] or t_scaled.shape[1] > search_gray.shape[1]:
                continue
            res = cv2.matchTemplate(search_gray, t_scaled, cv2.TM_CCOEFF_NORMED)
            locs = np.where(res >= 0.60)
            for pt in zip(*locs[::-1]):
                all_matches.append({
                    "score": float(res[pt[1], pt[0]]),
                    "x": int(pt[0] + x1),
                    "y": int(pt[1] + y1),
                    "w": int(t_scaled.shape[1]),
                    "h": int(t_scaled.shape[0]),
                    "grade": grade
                })

    all_matches = sorted(all_matches, key=lambda m: m["score"], reverse=True)
    detected_boxes = []
    for m in all_matches:
        overlap = False
        for b in detected_boxes:
            if abs(m["x"] - b["x"]) < 120 and abs(m["y"] - b["y"]) < 120:
                overlap = True
                break
        if not overlap:
            bx, by = max(0, m["x"]), max(0, m["y"])
            bw, bh = m["w"], m["h"]
            crop = search_gray[max(0, by - y1):min(search_gray.shape[0], by - y1 + bh), max(0, bx - x1):min(search_gray.shape[1], bx - x1 + bw)]
            if crop.size == 0:
                continue

            best_grade = m["grade"]
            best_corr = -1
            for g, tmpl in templates.items():
                t_resized = cv2.resize(tmpl, (crop.shape[1], crop.shape[0]))
                corr = float(cv2.matchTemplate(crop, t_resized, cv2.TM_CCOEFF_NORMED)[0][0])
                if corr > best_corr:
                    best_corr = corr
                    best_grade = g

            m["grade"] = best_grade
            m["score"] = best_corr
            detected_boxes.append(m)

    detected_boxes = sorted(detected_boxes, key=lambda b: b["x"])
    return [b["grade"] for b in detected_boxes]


def detect_treasure_tickets(screen: np.ndarray, box_count: int = 1) -> dict:
    """
    Analyzes the opened mystery box loot screen to detect if any Treasure Draw Tickets were dropped.
    Accurately distinguishes Rainbow (Supreme Treasure) and Gold (Great Treasure) tickets.
    Uses multi-scale template matching against genuine extracted ticket sprites
    combined with strict color profile verification (HSV) to prevent false positives and cross-detection.
    Returns: {'rainbow': int, 'gold': int, 'total': int}
    """
    if screen is None or screen.size == 0:
        return {"rainbow": 0, "gold": 0, "total": 0}

    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen_bgr = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_bgr = screen

    tmpl_t_gold = TEMPLATES.get("TICKET_GOLD.png")
    tmpl_t_rainbow = TEMPLATES.get("TICKET_RAINBOW.png")

    if tmpl_t_gold is None and tmpl_t_rainbow is None:
        return {"rainbow": 0, "gold": 0, "total": 0}

    # Loot card pedestals sit in this region: y from 180 to 500, x from 160 to 1120
    y1, y2, x1, x2 = 180, 500, 160, 1120
    roi_bgr = screen_bgr[y1:y2, x1:x2]
    roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY) if len(roi_bgr.shape) == 3 else roi_bgr

    def find_peaks(tmpl_gray, scales=(0.80, 0.88, 0.94, 1.0, 1.06, 1.12, 1.20), thresh=0.45):
        if tmpl_gray is None:
            return []
        best_map = np.zeros(roi_gray.shape, dtype=np.float32)
        best_size = (tmpl_gray.shape[1], tmpl_gray.shape[0])
        for sc in scales:
            tw, th = int(tmpl_gray.shape[1] * sc), int(tmpl_gray.shape[0] * sc)
            if roi_gray.shape[0] < th or roi_gray.shape[1] < tw:
                continue
            resized = cv2.resize(tmpl_gray, (tw, th))
            res = cv2.matchTemplate(roi_gray, resized, cv2.TM_CCOEFF_NORMED)
            pad_h = roi_gray.shape[0] - res.shape[0]
            pad_w = roi_gray.shape[1] - res.shape[1]
            padded = np.pad(res, ((0, pad_h), (0, pad_w)), mode='constant', constant_values=0)
            best_map = np.maximum(best_map, padded)

        locs = np.where(best_map >= thresh)
        if len(locs[0]) == 0:
            return []

        pts = sorted(zip(locs[1], locs[0], best_map[locs]), key=lambda p: p[2], reverse=True)
        used = []
        peaks = []
        for px, py, score in pts:
            if any(abs(px - ux) < 70 and abs(py - uy) < 70 for ux, uy in used):
                continue
            used.append((px, py))
            peaks.append((px, py, float(score), best_size))
        return peaks

    def analyze_crop(crop):
        if crop is None or crop.size == 0 or len(crop.shape) < 3:
            return 0.0, 0.0, 0.0
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        val_mask = (hsv[:, :, 2] > 55) & (hsv[:, :, 1] > 35)
        n = np.sum(val_mask)
        if n < 120:
            return 0.0, 0.0, 0.0
        hues = hsv[:, :, 0][val_mask]
        cb_ratio = np.sum((hues >= 85) & (hues <= 165)) / n
        yo_ratio = np.sum((hues >= 14) & (hues <= 38)) / n
        hue_std = float(np.std(hues))
        return cb_ratio, yo_ratio, hue_std

    gold_peaks = find_peaks(tmpl_t_gold) if tmpl_t_gold is not None else []
    rainbow_peaks = find_peaks(tmpl_t_rainbow) if tmpl_t_rainbow is not None else []

    gold_found = []
    rainbow_found = []

    for px, py, score, (tw, th) in gold_peaks:
        crop = roi_bgr[py:py+th, px:px+tw]
        cb, yo, std = analyze_crop(crop)
        # Gold ticket ("Great Treasure"): strong yellow/orange body (yo >= 0.40)
        if score >= 0.55 and yo >= 0.40 and cb < 0.20:
            gold_found.append((px, py, score))
        elif score >= 0.45 and yo >= 0.60:
            gold_found.append((px, py, score))

    for px, py, score, (tw, th) in rainbow_peaks:
        crop = roi_bgr[py:py+th, px:px+tw]
        cb, yo, std = analyze_crop(crop)
        # Rainbow ticket ("Supreme Treasure"): pink/cyan/purple body, low yellow
        if score >= 0.55 and cb >= 0.08 and yo < 0.30:
            rainbow_found.append((px, py, score))
        elif score >= 0.45 and cb >= 0.10 and std >= 18:
            rainbow_found.append((px, py, score))

    final_gold = []
    final_rainbow = []

    for gx, gy, gs in gold_found:
        conflict = [r for r in rainbow_found if abs(gx - r[0]) < 70]
        if not conflict:
            final_gold.append((gx, gy))
        else:
            rx, ry, rs = conflict[0]
            crop_g = roi_bgr[gy:gy+tmpl_t_gold.shape[0], gx:gx+tmpl_t_gold.shape[1]]
            cb, yo, _ = analyze_crop(crop_g)
            if cb >= 0.08:
                pass
            elif gs >= rs or yo >= 0.30:
                final_gold.append((gx, gy))

    for rx, ry, rs in rainbow_found:
        conflict = [g for g in gold_found if abs(rx - g[0]) < 70]
        if not conflict:
            final_rainbow.append((rx, ry))
        else:
            gx, gy, gs = conflict[0]
            crop_r = roi_bgr[ry:ry+tmpl_t_rainbow.shape[0], rx:rx+tmpl_t_rainbow.shape[1]]
            cb, yo, _ = analyze_crop(crop_r)
            if cb >= 0.08:
                final_rainbow.append((rx, ry))

    def dedup(pts):
        res = []
        for p in pts:
            if not any(abs(p[0] - r[0]) < 60 for r in res):
                res.append(p)
        return res

    final_gold = dedup(final_gold)
    final_rainbow = dedup(final_rainbow)

    total_gold = len(final_gold)
    total_rainbow = len(final_rainbow)
    if box_count > 0 and (total_gold + total_rainbow) > box_count:
        while (total_gold + total_rainbow) > box_count:
            if total_gold > 0:
                total_gold -= 1
            else:
                total_rainbow -= 1

    return {
        "rainbow": total_rainbow,
        "gold": total_gold,
        "total": total_rainbow + total_gold,
    }


_digit_templates_cache: Dict[str, np.ndarray] = {}


def _load_digit_templates() -> Dict[str, np.ndarray]:
    global _digit_templates_cache
    if not _digit_templates_cache:
        digits_dir = os.path.join(TEMPLATE_DIR, "digits")
        for d in "0123456789":
            p = os.path.join(digits_dir, f"{d}.png")
            if os.path.exists(p):
                img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    _digit_templates_cache[d] = img
    return _digit_templates_cache


def extract_result_coins(screen: np.ndarray) -> int:
    """Extract exact in-game coins number from the GAME_COMPLETE (Result) screen."""
    if screen is None or screen.size == 0:
        return 0
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen_1280 = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_1280 = screen

    crop = screen_1280[360:450, 750:1150]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 125, 255, cv2.THRESH_BINARY_INV)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if 16 <= bh <= 38 and 4 <= bw <= 34 and x >= 140:
            boxes.append((x, y, bw, bh))

    if not boxes:
        return 0

    boxes = sorted(boxes, key=lambda b: b[0])
    digits = _load_digit_templates()
    if not digits:
        return 0

    digits_str = ""
    for (x, y, bw, bh) in boxes:
        char_img = thresh[y:y+bh, x:x+bw]
        best_d = "0"
        best_score = -1.0
        for d, tmpl in digits.items():
            t_scaled = cv2.resize(tmpl, (char_img.shape[1], char_img.shape[0]))
            res = float(cv2.matchTemplate(char_img, t_scaled, cv2.TM_CCOEFF_NORMED)[0][0])
            if res > best_score:
                best_score = res
                best_d = d
        digits_str += best_d

    try:
        return int(digits_str) if digits_str else 0
    except ValueError:
        return 0


def extract_result_xp(screen: np.ndarray) -> int:
    """Extract exact in-game XP number from the GAME_COMPLETE (Result) screen."""
    if screen is None or screen.size == 0:
        return 0
    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen_1280 = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_1280 = screen

    crop = screen_1280[440:520, 750:1150]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 125, 255, cv2.THRESH_BINARY_INV)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if 16 <= bh <= 38 and 4 <= bw <= 34 and x >= 140:
            boxes.append((x, y, bw, bh))

    if not boxes:
        return 0

    boxes = sorted(boxes, key=lambda b: b[0])
    digits = _load_digit_templates()
    if not digits:
        return 0

    digits_str = ""
    for (x, y, bw, bh) in boxes:
        char_img = thresh[y:y+bh, x:x+bw]
        best_d = "0"
        best_score = -1.0
        for d, tmpl in digits.items():
            t_scaled = cv2.resize(tmpl, (char_img.shape[1], char_img.shape[0]))
            res = float(cv2.matchTemplate(char_img, t_scaled, cv2.TM_CCOEFF_NORMED)[0][0])
            if res > best_score:
                best_score = res
                best_d = d
        digits_str += best_d

    try:
        return int(digits_str) if digits_str else 0
    except ValueError:
        return 0


def detect_result_screen_mystery_box(screen: np.ndarray) -> List[str]:
    """
    Checks the Result screen (GAME_COMPLETE) to see if mystery boxes were obtained.
    Returns a list of detected box grades (e.g. ['wood']).
    """
    if screen is None or screen.size == 0:
        return []

    h, w = screen.shape[:2]
    if (w, h) != (1280, 720):
        screen_1280 = cv2.resize(screen, (1280, 720), interpolation=cv2.INTER_AREA)
    else:
        screen_1280 = screen

    crop = screen_1280[int(720 * 0.35):int(720 * 0.55), int(1280 * 0.55):int(1280 * 0.85)]
    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    tmpl_path = os.path.join(TEMPLATE_DIR, "RESULT_BOX_BADGE.png")
    if not os.path.exists(tmpl_path):
        return []

    tmpl_gray = cv2.imread(tmpl_path, cv2.IMREAD_GRAYSCALE)
    if tmpl_gray is None:
        return []

    res = cv2.matchTemplate(crop_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)

    if max_val >= 0.70:
        return ["wood"]
    return []

"""
CookieRun Cloud Bot - Client Agent (Redfinger Edition)
???????????????? Redfinger ?????????????????, ??????????? Server ????,
??????????????????????????? (Stagger Sync / Anti-Collision)
???????????????????????????? (Anti-Captcha / Humanization) ????????????????? (Instant Stop)
"""

import os
import sys
import time
import random
import json
import base64
import sys

# --- Bulletproof Chaquopy / Android Thread Safety ---
# Neutralize all OS signal handling across all modules (_signal, signal, asyncio)
for _mod_name in ("_signal", "signal"):
    try:
        _m = sys.modules.get(_mod_name) or __import__(_mod_name)
        for _fn in ("signal", "getsignal", "set_wakeup_fd", "siginterrupt", "pthread_sigmask"):
            if hasattr(_m, _fn):
                setattr(_m, _fn, lambda *args, **kwargs: None)
    except Exception:
        pass

import signal
import asyncio
from typing import Optional

try:
    asyncio.BaseEventLoop.add_signal_handler = lambda *args, **kwargs: None
    asyncio.BaseEventLoop.remove_signal_handler = lambda *args, **kwargs: None
    if hasattr(asyncio, "get_event_loop_policy"):
        _policy = asyncio.get_event_loop_policy()
        if hasattr(_policy, "set_child_watcher"):
            _policy.set_child_watcher(None)
except Exception:
    pass

# Prevent UnicodeEncodeError on Windows Thai (CP874) console
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    import cv2
    import websockets
except ImportError:
    print("? Missing dependencies! Please run: pip install opencv-python websockets")
    sys.exit(1)

from screen_scaler import ScreenScaler

# Try importing ScreenBridge from Android Kotlin runtime
try:
    from com.cookierun.cloudbot import ScreenBridge
except Exception:
    ScreenBridge = None
from detection_lite import (
    load_templates,
    capture_screen_native,
    tap_native,
    detect_current_stage,
    detect_first_box,
    find_popup_dismiss_action,
    restart_game_app,
    match_template_in_region,
    is_item_checked,
    solve_anti_bot_captcha,
    detect_devplay_login,
    detect_mystery_box_grades,
    detect_result_screen_mystery_box,
    detect_treasure_tickets,
    extract_result_coins,
    extract_result_xp,
    find_level_up_confirm,
    is_level_up_popup,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_client.json")


def sanitize_ws_url(url: str) -> str:
    """Auto-converts https:// -> wss:// and http:// -> ws:// and trims slashes."""
    url = (url or "").strip().rstrip("/")
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    elif not url.startswith("ws://") and not url.startswith("wss://"):
        return "wss://" + url
    return url


def signal_handler(sig, frame):
    sys.exit(0)


class RedfingerBotClient:
    def __init__(self):
        self.load_config()
        self.scaler = ScreenScaler()
        self.is_running = True
        self.is_bot_active = False          # Wait for user to manually click START on Web Dashboard!
        self.is_in_game = False
        self.in_run_start_time = 0.0
        self.first_box_detected = False
        self.rounds_played = 0
        self.current_stage = "IDLE (Stopped)"
        self.ws = None
        self.last_frame_sent_time = 0.0
        self.can_start_future: Optional[asyncio.Future] = None
        self.last_stage_change_time = time.time()
        self.last_detected_stage = None
        self.boosts_prepared = False
        self.game_complete_confirm_count = 0
        self.current_round_boxes: list = []
        self.current_round_tickets: dict = {"rainbow": 0, "gold": 0}
        self.current_round_box_loot_sent: bool = False
        self.connection_lost_start_time = 0.0
        self.connection_time = 0.0

        # Anti-Detection & Fatigue System
        self.rounds_since_fatigue = 0
        self.next_fatigue_threshold = random.randint(9, 13)
        self.rounds_since_long_break = 0
        self.next_long_break_threshold = random.randint(25, 32)

    def load_config(self):
        default_config = {
            "server_ws_url": "https://cookierun-cloud-server.onrender.com",
            "device_id": "redfinger-01",
            "room_id": "pair_room_1",
            "auto_scale_resolution": True,
            "use_fast_start": False,
            "use_cookie_relay": True,
            "use_random_boost": False,
            "desired_boost": "ALL"
        }
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = default_config
        else:
            self.config = default_config
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4)

    def is_active(self) -> bool:
        """Returns True ONLY if the bot is running and user has NOT stopped it."""
        return self.is_running and self.is_bot_active

    async def human_delay(self, min_s: float = 0.5, max_s: float = 1.0) -> bool:
        """
        Anti-Detection & Responsive Stop:
        Waits for a randomized duration between min_s and max_s.
        Checks every 50ms whether the bot was stopped by the user.
        Returns False immediately if stopped, aborting any scheduled tap or action.
        """
        target = random.uniform(min_s, max_s)
        start = time.time()
        while time.time() - start < target:
            if not self.is_active():
                return False
            await asyncio.sleep(0.05)
        return True

    async def send_frame_preview(self, screen):
        """Sends a compressed JPEG preview frame to the web dashboard."""
        now = time.time()
        if now - self.last_frame_sent_time >= 1.5 and self.ws:
            self.last_frame_sent_time = now
            try:
                small = cv2.resize(screen, (640, 360), interpolation=cv2.INTER_AREA)
                _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])
                b64 = base64.b64encode(buf).decode("utf-8")
                await self.ws.send(json.dumps({"type": "FRAME", "data": b64}))
            except Exception as e:
                print(f"?? Error sending frame preview: {e}")

    async def single_receive_loop(self):
        """
        Dedicated single receive loop for WebSocket messages.
        Prevents concurrent recv() conflicts in asyncio.
        """
        while self.is_running and self.ws:
            try:
                msg_str = await self.ws.recv()
                msg = json.loads(msg_str)
                mtype = msg.get("type")

                if mtype == "CAN_START_RESPONSE":
                    if self.can_start_future and not self.can_start_future.done():
                        self.can_start_future.set_result(msg)

                elif mtype in ("CONFIG", "SETTINGS_UPDATE"):
                    cfg = msg.get("config") or msg.get("settings") or {}
                    if isinstance(cfg, dict):
                        self.config.update(cfg)
                        print(f"?? [Config] Updated settings from server: {cfg}")

                elif mtype == "COMMAND":
                    cmd = msg.get("command")
                    if cmd == "STAGGER_PAUSE":
                        dur = float(msg.get("duration", 10.0))
                        reason = msg.get("reason", "")
                        print(f"⏱️ [Stagger Sync] Received PAUSE command for {dur:.1f}s ({reason})")
                        # Tap in-game Pause button at top-right (1195, 45) - matched to PC bot
                        tap_native(1195, 45)
                        try:
                            await asyncio.sleep(dur)
                        finally:
                            # Always unpause so game never stays frozen
                            tap_native(631, 288)
                            await asyncio.sleep(0.35)
                            tap_native(631, 288)
                            await asyncio.sleep(0.5)
                            print("▶️ [Stagger Sync] Resumed running!")

                    elif cmd == "STOP":
                        print("⏹️ [Command] Received STOP command from Web Dashboard. Transitioning to IDLE standby.")
                        self.is_bot_active = False
                        self.is_in_game = False
                        self.current_stage = "IDLE (Stopped)"
                        if self.can_start_future and not self.can_start_future.done():
                            self.can_start_future.cancel()

                    elif cmd == "START":
                        print("▶️ [Command] Received START command from Web Dashboard. Bot is now ACTIVE!")
                        self.is_bot_active = True
                        self.current_stage = "RUNNING"
                        self.last_stage_change_time = time.time()

                    elif cmd == "RESET_APP":
                        print("?? [Command] Received RESET_APP command from Web Dashboard! Relaunching CookieRun...")
                        self.is_in_game = False
                        self.last_stage_change_time = time.time()
                        restart_game_app()

                    elif cmd == "TAP":
                        tx = int(msg.get("x", 640))
                        ty = int(msg.get("y", 360))
                        print(f"?? [Remote Touch] Tapping ({tx}, {ty}) from Web Dashboard...")
                        tap_native(tx, ty)

            except Exception:
                break

    async def heartbeat_loop(self):
        """Dedicated background task that guarantees a heartbeat every 2.5s unconditionally."""
        while self.is_running and self.ws:
            try:
                status = "RUNNING" if self.is_active() else "IDLE"
                stage = self.current_stage if self.is_active() else "IDLE (Stopped)"
                await self.ws.send(json.dumps({
                    "type": "HEARTBEAT",
                    "status": status,
                    "current_stage": stage,
                    "is_in_game": self.is_in_game,
                    "rounds_played": self.rounds_played
                }))
            except Exception:
                break
            await asyncio.sleep(2.5)

    async def streaming_loop(self):
        """
        Dedicated high-frequency screen streaming loop.
        Streams at ~10-12 FPS directly from Android hardware capture buffer.
        Runs continuously in background, completely independent of bot actions, sleeps, or delays!
        """
        last_sent_bytes = None
        last_sent_time = 0.0
        while self.is_running and self.ws:
            try:
                frame_bytes = None
                if ScreenBridge is not None:
                    try:
                        bridge = getattr(ScreenBridge, "INSTANCE", ScreenBridge)
                        if bridge.isReady():
                            raw = bridge.getLatestFrameJpeg()
                            if raw is not None:
                                frame_bytes = bytes(raw)
                    except Exception:
                        frame_bytes = None

                now = time.time()
                if frame_bytes is not None and len(frame_bytes) > 0:
                    if frame_bytes != last_sent_bytes or (now - last_sent_time >= 1.2):
                        last_sent_bytes = frame_bytes
                        last_sent_time = now
                        b64 = base64.b64encode(frame_bytes).decode("utf-8")
                        await self.ws.send(json.dumps({"type": "FRAME", "data": b64}))
                else:
                    # Fallback capture (ADB test mode or while ScreenBridge initializes)
                    if now - last_sent_time >= 0.2:
                        last_sent_time = now
                        screen = capture_screen_native()
                        if screen is not None:
                            small = cv2.resize(screen, (640, 360), interpolation=cv2.INTER_AREA)
                            _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 55])
                            b64 = base64.b64encode(bytes(buf)).decode("utf-8")
                            await self.ws.send(json.dumps({"type": "FRAME", "data": b64}))
                await asyncio.sleep(0.08)
            except Exception:
                await asyncio.sleep(0.15)

    async def handle_connection_or_inactive(self, stage_name: str):
        """
        Full PC Bot Port for Connection Lost & Inactive Handling:
        1. Resets in-run state flags.
        2. Taps 'Confirm' button (centered at 640, 460).
        3. Waits and checks up to 2 retry attempts (3.5s each) to see if popup dismissed.
        4. If still stuck, force-stops and relaunches the app (restart_game_app).
        5. Enters active recovery loop (up to 90s) waiting for game to boot:
           - Taps Confirm if popup reappears
           - Handles DevPlay login screen (640, 640)
           - Automatically dismisses announcements and daily popups
           - Once playable stage (MAINMENU / PURCHASE_ITEM / GAME_START) is detected -> resumes execution!
        """
        print(f"🔌 [Recovery] ตรวจพบหน้าต่างแจ้งเตือน: {stage_name} — เตรียมกด Confirm (640, 460) เพื่อรีสตาร์ทเกม...")
        self.is_in_game = False
        self.in_run_start_time = 0.0
        self.first_box_detected = False
        self.current_stage = stage_name
        self.last_stage_change_time = time.time()
        self.connection_lost_start_time = 0.0

        # 1. First Confirm tap
        print("👆 [Recovery] กำลังกดปุ่ม Confirm (640, 460) บนหน้าจอ...")
        tap_native(640, 460)

        # 2. Check if popup dismissed over 2 attempts (3.5s each)
        stuck = False
        for attempt in range(2):
            if not await self.human_delay(3.0, 3.8):
                return
            curr_screen = capture_screen_native()
            if curr_screen is not None:
                still_stage = detect_current_stage(curr_screen)
                if still_stage in ("CONNECTION_LOST", "INACTIVE"):
                    stuck = True
                    print(f"⚠️ [Recovery] หน้าต่าง {still_stage} ยังคงค้างอยู่ (กด Confirm รอบที่ {attempt + 1} ไม่ติด) — กำลังกดย้ำอีกครั้ง...")
                    tap_native(640, 460)
                else:
                    stuck = False
                    print("✅ [Recovery] ป๊อปอัปปิดแล้ว — ตัวเกมกำลังรีสตาร์ท...")
                    break

        # 3. If still stuck, force restart the app
        if stuck:
            print("🚨 [Recovery] ป๊อปอัปยังคงค้างอยู่ (กด Confirm ไม่ติด) — กำลังบังคับปิดแอปและเปิดเกมใหม่...")
            restart_game_app()
            await self.human_delay(4.0, 6.0)

        # 4. Wait for game to boot and recover (up to 90 seconds)
        print("⏳ [Recovery] กำลังรอเกมโหลดและตรวจสอบสถานะหน้าจอ (สูงสุด 90 วินาที)...")
        boot_start = time.time()
        while time.time() - boot_start < 90:
            if not self.is_active():
                return
            if not await self.human_delay(2.0, 2.5):
                return

            boot_screen = capture_screen_native()
            if boot_screen is None:
                continue

            # Check for DevPlay Login screen
            if detect_devplay_login(boot_screen):
                print("🟠 [Recovery] ตรวจพบหน้า DevPlay Login — กำลังแตะเข้าเกม (640, 640)...")
                tap_native(640, 640)
                await self.human_delay(3.5, 5.0)
                continue

            # Check if popup appeared again
            boot_stage = detect_current_stage(boot_screen)
            if boot_stage in ("CONNECTION_LOST", "INACTIVE"):
                print(f"⚠️ [Recovery] ป๊อปอัป {boot_stage} ปรากฏซ้ำ — กด Confirm (640, 460)...")
                tap_native(640, 460)
                continue

            # Check if reached playable stage
            if boot_stage in ("MAINMENU", "PURCHASE_ITEM", "GAME_START"):
                print(f"🎮 [Recovery] เกมรีโหลดและพร้อมทำงานต่อแล้ว (ตรวจพบหน้า {boot_stage}) — บอททำงานต่อทันที!")
                self.current_stage = boot_stage
                self.last_stage_change_time = time.time()
                break

            # Try to dismiss announcement dialogs / daily check-in
            popup_pos = find_popup_dismiss_action(boot_screen)
            if popup_pos:
                print(f"🛡️ [Recovery] Dismissing popup at {popup_pos}...")
                tap_native(popup_pos[0], popup_pos[1])
                await self.human_delay(1.0, 1.5)

    async def prepare_boosts_and_items(self):
        """Prepares items and rolls for desired random boost if configured with human-like delays."""
        if not self.is_active():
            return

        use_random_boost = bool(self.config.get("use_random_boost", False))
        desired_boost = self.config.get("desired_boost", "ALL")
        use_fast_start = bool(self.config.get("use_fast_start", False))
        use_cookie_relay = bool(self.config.get("use_cookie_relay", False))

        # 1. Random Boost Handling (Slot 3: Center 535, 600 - matched to PC bot)
        if use_random_boost:
            self.last_stage_change_time = time.time()
            print("🎲 [Booster] Tapping Random Boost slot (535, 600)...")
            tap_native(535, 600)
            if not await self.human_delay(0.65, 1.05):
                return

            if desired_boost and desired_boost != "ALL":
                print(f"🎲 [Booster] Checking for desired boost: {desired_boost}...")
                screen = capture_screen_native()
                gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if (screen is not None and len(screen.shape) == 3) else screen
                already_matched = match_template_in_region(gray, desired_boost, (701, 505, 1091, 578), threshold=0.72) if gray is not None else False

                if already_matched:
                    print(f"✅ [Booster] Desired boost '{desired_boost}' is already active!")
                else:
                    print(f"🔄 [Booster] Desired boost not active. Starting roll sequence (max 20)...")
                    for roll in range(1, 21):
                        if not self.is_active():
                            return
                        self.last_stage_change_time = time.time()
                        # Tap Buy button on right panel
                        tap_native(925, 295)
                        if not await self.human_delay(0.45, 0.75):
                            return
                        # Tap Confirm Buy button in confirmation dialog
                        tap_native(780, 460)
                        if not await self.human_delay(1.1, 1.6):
                            return

                        screen = capture_screen_native()
                        gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if (screen is not None and len(screen.shape) == 3) else screen
                        if gray is not None and match_template_in_region(gray, desired_boost, (701, 505, 1091, 578), threshold=0.72):
                            print(f"✨ [Booster] Successfully acquired '{desired_boost}' on roll #{roll}!")
                            break
                    else:
                        print(f"⚠️ [Booster] Reached roll limit (20) for '{desired_boost}', proceeding with current.")

        # Capture current screen to check item checkbox statuses
        current_screen = capture_screen_native()

        # 2. Smart Fast Start toggle (Slot 1: Center 235, 600 - matched to PC bot)
        if use_fast_start and current_screen is not None:
            fs_is_checked = is_item_checked(current_screen, "fast_start")
            if not fs_is_checked:
                if not self.is_active():
                    return
                self.last_stage_change_time = time.time()
                print("⚡ [Item] Fast Start is ON but unchecked -> Tapping slot 1 (235, 600)...")
                tap_native(235, 600)
                if not await self.human_delay(0.45, 0.75):
                    return

        # 3. Smart Cookie Relay toggle (Slot 2: Center 385, 600 - matched to PC bot)
        # Note: If use_cookie_relay is False, we strictly DO NOT tap it!
        if use_cookie_relay and current_screen is not None:
            cr_is_checked = is_item_checked(current_screen, "cookie_relay")
            if not cr_is_checked:
                if not self.is_active():
                    return
                self.last_stage_change_time = time.time()
                print("🍪 [Item] Cookie Relay is ON but unchecked -> Tapping slot 2 (385, 600)...")
                tap_native(385, 600)
                if not await self.human_delay(0.5, 0.8):
                    return
                # If out of stock, purchase popup may appear -> buy and confirm (925, 295) -> (780, 460)
                check_buy = capture_screen_native()
                if check_buy is not None and not is_item_checked(check_buy, "cookie_relay"):
                    tap_native(925, 295)
                    if await self.human_delay(0.45, 0.75):
                        tap_native(780, 460)
                        await self.human_delay(1.0, 1.5)

    async def run(self):
        # 1. Apply 1280x720 aspect ratio if enabled
        if self.config.get("auto_scale_resolution", True):
            self.scaler.apply_16_9_ratio()

        # 2. Preload detection templates
        print("??? Loading templates...")
        load_templates()
        print("? Templates loaded successfully.")

        # 3. Connect to Central Cloud Server
        base_ws = sanitize_ws_url(self.config.get("server_ws_url", ""))
        ws_url = f"{base_ws}/ws/device/{self.config['device_id']}?room_id={self.config['room_id']}"
        print(f"?? Connecting to Cloud Hub: {ws_url}...")

        while self.is_running:
            try:
                async with websockets.connect(ws_url) as ws:
                    self.ws = ws
                    self.connection_time = time.time()
                    print(f"✅ Connected to Central Server as {self.config['device_id']}!")
                    recv_task = asyncio.create_task(self.single_receive_loop())
                    hb_task = asyncio.create_task(self.heartbeat_loop())
                    stream_task = asyncio.create_task(self.streaming_loop())

                    while self.is_running and self.ws:
                        screen = capture_screen_native()
                        if screen is None:
                            await asyncio.sleep(0.3)
                            continue

                        # Periodic safety frame preview sync (guarantees server always has latest screen even if streaming loop paused)
                        if time.time() - self.last_frame_sent_time >= 1.5:
                            await self.send_frame_preview(screen)

                        # If user stopped bot via Web Dashboard, strictly halt actions and maintain IDLE
                        if not self.is_active():
                            self.current_stage = "IDLE (Stopped)"
                            self.is_in_game = False
                            await self.ws.send(json.dumps({
                                "type": "HEARTBEAT",
                                "status": "IDLE",
                                "current_stage": self.current_stage,
                                "is_in_game": False,
                                "rounds_played": self.rounds_played
                            }))
                            await asyncio.sleep(0.8)
                            continue

                        stage = detect_current_stage(screen, in_game=self.is_in_game)
                        now = time.time()
                        if stage:
                            self.current_stage = stage
                            if stage != self.last_detected_stage:
                                self.last_detected_stage = stage
                                self.last_stage_change_time = now
                        if stage != "GAME_COMPLETE":
                            self.game_complete_confirm_count = 0
                        if stage and stage not in ("CONNECTION_LOST", "INACTIVE"):
                            self.connection_lost_start_time = 0.0

                        # --- Watchdog: Automatic App Recovery When Stuck ---
                        if self.is_in_game:
                            if now - self.in_run_start_time > 280:
                                print("⏰ [Watchdog] In-game duration > 280s! App appears frozen, auto-restarting...")
                                self.is_in_game = False
                                restart_game_app()
                                self.last_stage_change_time = time.time()
                                await self.human_delay(7.0, 9.0)
                                continue
                        else:
                            if now - self.last_stage_change_time > 120:
                                print(f"⏰ [Watchdog] Stuck for > 120s in '{self.current_stage}'! Auto-restarting game...")
                                self.is_in_game = False
                                restart_game_app()
                                self.last_stage_change_time = time.time()
                                await self.human_delay(7.0, 9.0)
                                continue

                        if not self.is_active():
                            continue

                        # State 0: ANTI_BOT (Anti-Bot Captcha Verification)
                        if stage == "ANTI_BOT":
                            self.is_in_game = False
                            self.last_stage_change_time = time.time()
                            print("🚨 [Anti-Bot] Verification screen detected! Analyzing 6 cards to solve captcha...")
                            # Natural human hesitation like looking at the puzzle (1.2 - 2.0s)
                            if not await self.human_delay(1.2, 2.0):
                                continue
                            solved_cards = solve_anti_bot_captcha(screen)
                            if solved_cards:
                                print(f"✅ [Anti-Bot] Solved! Tapped odd cards: {[c + 1 for c in solved_cards]}")
                                if self.ws:
                                    try:
                                        await self.ws.send(json.dumps({
                                            "type": "CAPTCHA_SOLVED",
                                            "odd_cards": [c + 1 for c in solved_cards],
                                            "device_id": self.config.get("device_id")
                                        }))
                                    except Exception:
                                        pass
                            self.last_stage_change_time = time.time()
                            await self.human_delay(2.0, 3.0)
                            continue

                        # State: LEVEL_UP (Checked before generic popup dismiss to ensure Confirm is clicked)
                        if stage == "LEVEL_UP" or (not self.is_in_game and is_level_up_popup(screen)):
                            self.is_in_game = False
                            print("⭐ [Stage] LEVEL_UP popup detected! Finding Confirm button...")
                            lu_pos = find_level_up_confirm(screen)
                            lx, ly = lu_pos if lu_pos else (640, 630)
                            print(f"⭐ [Stage] LEVEL_UP -> Tapping Confirm at ({lx}, {ly})...")
                            tap_native(lx, ly)
                            await self.human_delay(0.6, 1.0)
                            # Tap second time to ensure confirmation through animation
                            tap_native(lx, ly)
                            self.last_stage_change_time = time.time()
                            await self.human_delay(1.5, 2.2)
                            continue

                        # State 1: MAINMENU
                        elif stage == "MAINMENU":
                            self.is_in_game = False
                            self.boosts_prepared = False
                            self.last_stage_change_time = time.time()
                            # Natural human reaction time before clicking start
                            if not await self.human_delay(0.8, 1.5):
                                continue
                            print("🚀 [Stage] MAINMENU -> Tapping Start Game...")
                            tap_native(955, 650)
                            self.last_stage_change_time = time.time()
                            await self.human_delay(1.8, 2.5)

                        # State 2: PURCHASE_ITEM (Lobby / Boost Screen)
                        elif stage == "PURCHASE_ITEM":
                            self.is_in_game = False
                            if not self.boosts_prepared:
                                await self.prepare_boosts_and_items()
                                self.boosts_prepared = True
                                self.last_stage_change_time = time.time()
                                if not self.is_active():
                                    continue

                            # Ask coordinator if safe to start (Anti-Collision)
                            loop = asyncio.get_event_loop()
                            self.can_start_future = loop.create_future()
                            await self.ws.send(json.dumps({"type": "CHECK_CAN_START"}))
                            
                            can_start = False
                            reason = ""
                            try:
                                resp = await asyncio.wait_for(self.can_start_future, timeout=2.0)
                                can_start = resp.get("can_start", False)
                                reason = resp.get("reason", "")
                            except Exception:
                                can_start = self.is_active()

                            if not self.is_active():
                                continue

                            if can_start:
                                # Natural human hesitation before hitting PLAY (0.6 - 1.2s)
                                if not await self.human_delay(0.6, 1.2):
                                    continue
                                print("🚀 [Anti-Collision] Approved to start! Tapping PLAY...")
                                # Tap exact center of Play button (915, 605) twice with short interval
                                tap_native(915, 605)
                                await asyncio.sleep(0.3)
                                tap_native(915, 605)
                                await self.human_delay(1.5, 2.2)

                                # Verify if screen transitioned away from lobby
                                post_screen = capture_screen_native()
                                post_stage = detect_current_stage(post_screen)
                                if post_stage == "PURCHASE_ITEM":
                                    print("⚠️ Still in Lobby, tapping PLAY again at (915, 605)...")
                                    tap_native(915, 605)
                                    await self.human_delay(1.0, 1.5)

                                self.is_in_game = True
                                self.boosts_prepared = False
                                self.last_stage_change_time = time.time()
                                self.in_run_start_time = time.time()
                                self.first_box_detected = False
                                self.rounds_played += 1
                                await self.ws.send(json.dumps({"type": "ROUND_START", "round": self.rounds_played}))
                                await self.human_delay(1.8, 2.6)
                            else:
                                self.last_stage_change_time = time.time()
                                print(f"⏳ [Anti-Collision] Waiting for partner: {reason}")
                                await self.human_delay(1.0, 1.5)

                        # State 3: GAME_START
                        elif stage == "GAME_START":
                            self.is_in_game = True
                            self.boosts_prepared = False
                            self.last_stage_change_time = time.time()
                            if bool(self.config.get("use_fast_start", False)):
                                if self.is_active():
                                    print("⚡ [Stage] GAME_START -> use_fast_start is ON -> Tapping Fast Start (655, 340)...")
                                    tap_native(655, 340)
                            else:
                                print("⚡ [Stage] GAME_START -> use_fast_start is OFF -> Skipping Fast Start tap.")
                            await self.human_delay(0.8, 1.3)

                        # State 4: GAME_RELAY
                        elif stage == "GAME_RELAY":
                            self.last_stage_change_time = time.time()
                            print("🍪 [Stage] Relay triggered! Rapid tapping Cookie Relay button (655, 340)...")
                            for _ in range(4):
                                tap_native(655, 340, jitter=False)
                                await asyncio.sleep(0.08)
                            await self.human_delay(0.35, 0.6)

                        # State 4.5: MYSTERY_BOX (Loot pedestal screen where box grades and tickets appear)
                        elif stage == "MYSTERY_BOX":
                            self.is_in_game = False
                            print("📦 [Stage] MYSTERY_BOX detected! Detecting box grades and tickets...")
                            await self.human_delay(0.8, 1.2)
                            mb_screen = capture_screen_native()
                            if mb_screen is not None:
                                detected_grades = detect_mystery_box_grades(mb_screen)
                                if not detected_grades:
                                    detected_grades = ["wood"]
                                print(f"📦 [Mystery Box] Grades detected: {detected_grades}")
                                self.current_round_boxes.extend(detected_grades)

                                # Also check for treasure tickets
                                tickets = detect_treasure_tickets(mb_screen)
                                if tickets.get("gold", 0) > 0:
                                    self.current_round_tickets["gold"] += tickets["gold"]
                                if tickets.get("rainbow", 0) > 0:
                                    self.current_round_tickets["rainbow"] += tickets["rainbow"]

                            # Tap confirm button (650, 645)
                            tap_native(650, 645)
                            self.last_stage_change_time = time.time()
                            await self.human_delay(1.5, 2.2)
                            continue

                        # State 5: GAME_COMPLETE
                        elif stage == "GAME_COMPLETE":
                            self.game_complete_confirm_count += 1
                            if self.game_complete_confirm_count < 2:
                                print(f"🔍 [Stage] Verifying GAME_COMPLETE ({self.game_complete_confirm_count}/2)...")
                                await asyncio.sleep(0.3)
                                continue

                            self.game_complete_confirm_count = 0
                            self.is_in_game = False
                            self.boosts_prepared = False
                            self.last_stage_change_time = time.time()
                            print("🏆 [Stage] GAME_COMPLETE confirmed! Finishing round...")
                            # 1. Skip score and coin animations (single tap with natural pause)
                            tap_native(640, 260)
                            if not await self.human_delay(0.7, 1.0):
                                continue

                            # 2. Extract real in-game coins and XP directly from Result screen
                            result_screen = capture_screen_native()
                            round_coins = extract_result_coins(result_screen) if result_screen is not None else 0
                            round_xp = extract_result_xp(result_screen) if result_screen is not None else 0

                            # Safety retry if numbers are still animating
                            if round_coins == 0 and result_screen is not None:
                                tap_native(640, 260)
                                await self.human_delay(0.6, 0.9)
                                result_screen = capture_screen_native()
                                if result_screen is not None:
                                    round_coins = extract_result_coins(result_screen)
                                    round_xp = extract_result_xp(result_screen)

                            # Also detect if any mystery box was awarded on Result screen
                            if not self.current_round_boxes and result_screen is not None:
                                res_boxes = detect_result_screen_mystery_box(result_screen)
                                if res_boxes:
                                    print(f"📦 [Stage] Detected mystery box on Result screen: {res_boxes}")
                                    self.current_round_boxes.extend(res_boxes)

                            print(f"💰 [Stage] Round complete! Coins: +{round_coins:,} 🪙, XP: +{round_xp:,} EXP")

                            # 3. Tap green OK button at (462, 619) to return to Lobby
                            tap_native(462, 619)
                            await self.ws.send(json.dumps({
                                "type": "ROUND_COMPLETE",
                                "coins": round_coins,
                                "xp": round_xp,
                                "boxes": list(self.current_round_boxes),
                                "tickets": dict(self.current_round_tickets),
                                "box_loot_sent": self.current_round_box_loot_sent
                            }))
                            self.current_round_boxes.clear()
                            self.current_round_tickets = {"rainbow": 0, "gold": 0}
                            self.current_round_box_loot_sent = False

                            # === ANTI-DETECTION / ANTI-CAPTCHA RESTING SYSTEM ===
                            self.rounds_since_fatigue += 1
                            self.rounds_since_long_break += 1

                            # Tier 1: Long Session Rest (every 25-32 rounds, rest for 90-150s to reset anti-cheat session velocity)
                            if self.rounds_since_long_break >= self.next_long_break_threshold and self.is_active():
                                break_dur = random.uniform(90.0, 150.0)
                                print(f"☕ [Session Break] Played {self.rounds_since_long_break} rounds! Resting naturally for {break_dur:.1f}s...")
                                self.current_stage = f"RESTING (Break {int(break_dur)}s)"
                                if self.ws:
                                    try:
                                        await self.ws.send(json.dumps({
                                            "type": "HEARTBEAT",
                                            "status": "RUNNING",
                                            "current_stage": self.current_stage,
                                            "is_in_game": False,
                                            "rounds_played": self.rounds_played
                                        }))
                                    except Exception:
                                        pass
                                await self.human_delay(break_dur, break_dur + 1.0)
                                self.rounds_since_long_break = 0
                                self.next_long_break_threshold = random.randint(25, 32)
                                self.rounds_since_fatigue = 0
                                self.last_stage_change_time = time.time()

                            # Tier 2: Fatigue Rest (every 9-13 rounds, rest for 40-70s)
                            elif self.rounds_since_fatigue >= self.next_fatigue_threshold and self.is_active():
                                fatigue_dur = random.uniform(40.0, 70.0)
                                print(f"😴 [Fatigue Rest] Played {self.rounds_since_fatigue} rounds! Resting naturally for {fatigue_dur:.1f}s...")
                                self.current_stage = f"RESTING ({int(fatigue_dur)}s)"
                                if self.ws:
                                    try:
                                        await self.ws.send(json.dumps({
                                            "type": "HEARTBEAT",
                                            "status": "RUNNING",
                                            "current_stage": self.current_stage,
                                            "is_in_game": False,
                                            "rounds_played": self.rounds_played
                                        }))
                                    except Exception:
                                        pass
                                await self.human_delay(fatigue_dur, fatigue_dur + 1.0)
                                self.rounds_since_fatigue = 0
                                self.next_fatigue_threshold = random.randint(9, 13)
                                self.last_stage_change_time = time.time()
                            else:
                                # Normal inter-round cooldown (5.0 - 8.5 seconds of realistic pause)
                                cooldown = random.uniform(5.0, 8.5)
                                print(f"🌿 [Human Rest] Inter-round cooldown: resting for {cooldown:.1f}s...")
                                self.current_stage = "RESTING (Cooldown)"
                                await self.human_delay(cooldown, cooldown + 0.5)
                                self.last_stage_change_time = time.time()

                        # State 6: MYSTERY_BOX
                        elif stage == "MYSTERY_BOX":
                            self.is_in_game = False
                            self.boosts_prepared = False
                            print("🎁 [Stage] Mystery Box screen detected! Detecting box grades...")
                            detected_boxes = detect_mystery_box_grades(screen)
                            if detected_boxes:
                                print(f"📦 [Mystery Box] Detected box grades: {detected_boxes}")
                                self.current_round_boxes.extend(detected_boxes)

                            # 1. Tap to open box
                            tap_native(650, 645)
                            if not await self.human_delay(1.5, 2.0):
                                continue

                            # 2. Capture loot screen to detect tickets
                            loot_screen = capture_screen_native()
                            detected_tickets = {"rainbow": 0, "gold": 0, "total": 0}
                            if loot_screen is not None:
                                detected_tickets = detect_treasure_tickets(loot_screen, box_count=max(len(detected_boxes), 1))
                                if detected_tickets.get("rainbow", 0) > 0 or detected_tickets.get("gold", 0) > 0:
                                    print(f"🎟️ [Mystery Box] Detected tickets! Rainbow: {detected_tickets['rainbow']}, Gold: {detected_tickets['gold']}")
                                    self.current_round_tickets["rainbow"] += int(detected_tickets.get("rainbow", 0))
                                    self.current_round_tickets["gold"] += int(detected_tickets.get("gold", 0))

                            # 3. Send BOX_LOOT to server immediately
                            if self.ws:
                                try:
                                    await self.ws.send(json.dumps({
                                        "type": "BOX_LOOT",
                                        "boxes": detected_boxes,
                                        "tickets": detected_tickets
                                    }))
                                    self.current_round_box_loot_sent = True
                                except Exception as e:
                                    print(f"⚠️ Failed to send BOX_LOOT: {e}")

                            # 4. Tap to close loot screen / return to lobby
                            tap_native(650, 645)
                            await self.human_delay(1.5, 2.2)

                        # State 7: DAILY_CHECKIN
                        elif stage == "DAILY_CHECKIN":
                            self.is_in_game = False
                            print("?? [Stage] DAILY_CHECKIN -> Claiming reward...")
                            tap_native(640, 660)
                            await self.human_delay(1.5, 2.2)

                        # State 8: DAILY_CHECKIN_BOOST_SET
                        elif stage == "DAILY_CHECKIN_BOOST_SET":
                            self.is_in_game = False
                            print("?? [Stage] DAILY_CHECKIN_BOOST_SET -> Claiming boost...")
                            tap_native(640, 568)
                            await self.human_delay(1.5, 2.2)

                        # State 9: DAILY_TREASURE / DAILY_NEW / ENTER_LEAGUE
                        elif stage == "DAILY_TREASURE":
                            self.is_in_game = False
                            print("?? [Stage] DAILY_TREASURE -> Confirming...")
                            tap_native(640, 595)
                            await self.human_delay(1.5, 2.2)

                        elif stage == "DAILY_NEW":
                            self.is_in_game = False
                            print("?? [Stage] DAILY_NEW -> Confirming...")
                            tap_native(640, 595)
                            await self.human_delay(1.5, 2.2)

                        elif stage == "ENTER_LEAGUE":
                            self.is_in_game = False
                            print("?? [Stage] ENTER_LEAGUE -> Confirming...")
                            tap_native(640, 580)
                            await self.human_delay(1.5, 2.2)

                        # State 10: CONNECTION_LOST / INACTIVE popup
                        elif stage in ("CONNECTION_LOST", "INACTIVE"):
                            await self.handle_connection_or_inactive(stage)

                        # State 11: DEVPLAY_LOGIN (Title screen with orange DevPlay Login button)
                        elif stage == "DEVPLAY_LOGIN":
                            self.is_in_game = False
                            self.boosts_prepared = False
                            print("🟠 [Stage] DevPlay Login title screen detected! Tapping login button (640, 640)...")
                            if not await self.human_delay(1.2, 2.0):
                                continue
                            tap_native(640, 640)
                            await self.human_delay(4.5, 6.5)

                        # State 11b: YOU_ARE_NOT_READY popup (dismiss with Cancel/Close-X only)
                        elif stage == "YOU_ARE_NOT_READY":
                            self.is_in_game = False
                            print("⚠️ [Stage] YOU_ARE_NOT_READY popup detected! Dismissing...")
                            popup_pos = find_popup_dismiss_action(screen)
                            if popup_pos:
                                tap_native(popup_pos[0], popup_pos[1])
                                print(f"  🛑 Dismissed at {popup_pos}")
                            else:
                                # Fallback: tap Cancel button area (bottom-left of dialog)
                                tap_native(480, 460)
                                print("  🛑 Tapped Cancel fallback (480, 460)")
                            await self.human_delay(1.0, 1.8)

                        # State 12: Popups & Results (CONGRATULATIONS / LEVEL_UP / PREVIOUS_RANK_RESULTS / etc.)
                        elif stage == "CONGRATULATIONS":
                            self.is_in_game = False
                            print("🎉 [Stage] CONGRATULATIONS -> Confirming (640, 565)...")
                            tap_native(640, 565)
                            await self.human_delay(1.5, 2.2)

                        elif stage == "LEVEL_UP":
                            self.is_in_game = False
                            print("⭐ [Stage] LEVEL_UP popup detected! Finding Confirm button...")
                            screen_for_lu = capture_screen_native()
                            lu_pos = find_level_up_confirm(screen_for_lu)
                            lx, ly = lu_pos if lu_pos else (640, 620)
                            print(f"⭐ [Stage] LEVEL_UP -> Tapping Confirm at ({lx}, {ly})...")
                            tap_native(lx, ly)
                            await self.human_delay(0.8, 1.2)
                            # Second tap in case of animation/multi-step confirm
                            tap_native(lx, ly)
                            await self.human_delay(1.0, 1.8)

                        elif stage == "PREVIOUS_RANK_RESULTS":
                            self.is_in_game = False
                            print("📊 [Stage] PREVIOUS_RANK_RESULTS -> Confirming (640, 615)...")
                            tap_native(640, 615)
                            await self.human_delay(1.5, 2.2)

                        elif stage == "TOO_MANY_TREASURES":
                            self.is_in_game = False
                            print("📦 [Stage] TOO_MANY_TREASURES -> Confirming (640, 545)...")
                            tap_native(640, 545)
                            await self.human_delay(1.5, 2.2)

                        elif stage == "OVERTAKE_BREAK_SCORE":
                            self.is_in_game = False
                            print("🚀 [Stage] OVERTAKE_BREAK_SCORE -> Confirming (465, 640)...")
                            tap_native(465, 640)
                            await self.human_delay(1.5, 2.2)

                        # State 12: IN_GAME Monitoring (First Box Detection)
                        elif self.is_in_game:
                            if not self.first_box_detected:
                                elapsed = time.time() - self.in_run_start_time
                                if elapsed >= 7.0 and detect_first_box(screen):
                                    self.first_box_detected = True
                                    print(f"📦 [First Box] Detected mystery box at second {elapsed:.1f}!")
                                    await self.ws.send(json.dumps({"type": "FIRST_BOX", "second": round(elapsed, 1)}))

                        # State 13: Not in-game & stage unknown -> Check DevPlay fallback or dismiss popups
                        else:
                            if detect_devplay_login(screen):
                                self.is_in_game = False
                                self.boosts_prepared = False
                                print("🟠 [DevPlay] Detected orange DevPlay Login screen! Tapping login (640, 640)...")
                                if await self.human_delay(1.2, 2.0):
                                    tap_native(640, 640)
                                    await self.human_delay(4.5, 6.5)
                                continue

                            popup_pos = find_popup_dismiss_action(screen)
                            if popup_pos:
                                print(f"🛡️ Dismissing popup dialog at {popup_pos}...")
                                tap_native(popup_pos[0], popup_pos[1])
                                await self.human_delay(0.9, 1.4)

                        # Heartbeat update
                        if self.is_active():
                            await self.ws.send(json.dumps({
                                "type": "HEARTBEAT",
                                "status": "RUNNING",
                                "current_stage": self.current_stage,
                                "is_in_game": self.is_in_game,
                                "rounds_played": self.rounds_played
                            }))

                        await asyncio.sleep(0.08 if self.is_in_game else 0.15)

                    recv_task.cancel()
                    hb_task.cancel()
                    stream_task.cancel()
                    self.ws = None

            except Exception as e:
                print(f"?? Connection lost: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5.0)

        # Cleanup screen resolution on stop
        self.scaler.restore_original_ratio()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CookieRun Cloud Bot Client")
    parser.add_argument("--adb", type=str, default=None, help="ADB target device ID (e.g. emulator-5554) for testing on LDPlayer")
    parser.add_argument("--id", type=str, default=None, help="Custom device ID (e.g. redfinger-01)")
    parser.add_argument("--room", type=str, default=None, help="Pair room ID (e.g. pair_room_1)")
    parser.add_argument("--server", type=str, default=None, help="Server WebSocket URL (e.g. ws://127.0.0.1:8000)")
    args = parser.parse_args()

    client = RedfingerBotClient()
    if args.adb:
        from detection_lite import set_adb_target
        set_adb_target(args.adb)
        # In PC ADB test mode, resolution is already 1280x720 in LDPlayer
        client.config["auto_scale_resolution"] = False
    if args.id:
        client.config["device_id"] = args.id
    if args.room:
        client.config["room_id"] = args.room
    if args.server:
        client.config["server_ws_url"] = args.server

    try:
        import threading
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, signal_handler)
    except Exception:
        pass

    asyncio.run(client.run())


_active_client = None


def stop_bot_from_android():
    """Stops the active running bot loop from Android UI."""
    global _active_client
    if _active_client:
        _active_client.is_bot_active = False
        _active_client.is_running = False
        print("🛑 Stop signal received from Android UI. Halting bot and closing connection...")
        if _active_client.ws:
            try:
                loop = getattr(_active_client, "loop", None)
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(_active_client.ws.close(), loop)
            except Exception:
                pass


def start_bot_from_android(server_url: str, device_id: str, room_id: str, callback=None):
    """Entry point called directly from Android Kotlin MainActivity."""
    global _active_client

    if callback is not None:
        class AndroidStdout:
            def __init__(self, original_stdout, cb):
                self.original_stdout = original_stdout
                self.cb = cb

            def write(self, s):
                if self.original_stdout:
                    try:
                        self.original_stdout.write(s)
                    except Exception:
                        pass
                if s and s.strip():
                    msg = s.strip()
                    try:
                        if hasattr(self.cb, "log"):
                            self.cb.log(msg)
                        elif hasattr(self.cb, "invoke"):
                            self.cb.invoke(msg)
                        elif callable(self.cb):
                            self.cb(msg)
                    except Exception:
                        pass

            def flush(self):
                if self.original_stdout:
                    try:
                        self.original_stdout.flush()
                    except Exception:
                        pass

        sys.stdout = AndroidStdout(sys.__stdout__, callback)
        sys.stderr = AndroidStdout(sys.__stderr__, callback)

    # Re-apply signal neutralization inside this specific worker thread
    for _mod_name in ("_signal", "signal"):
        try:
            _m = sys.modules.get(_mod_name) or __import__(_mod_name)
            for _fn in ("signal", "getsignal", "set_wakeup_fd", "siginterrupt", "pthread_sigmask"):
                if hasattr(_m, _fn):
                    setattr(_m, _fn, lambda *args, **kwargs: None)
        except Exception:
            pass

    try:
        import asyncio
        asyncio.BaseEventLoop.add_signal_handler = lambda *args, **kwargs: None
        asyncio.BaseEventLoop.remove_signal_handler = lambda *args, **kwargs: None
        if hasattr(asyncio, "get_event_loop_policy"):
            _policy = asyncio.get_event_loop_policy()
            if hasattr(_policy, "set_child_watcher"):
                _policy.set_child_watcher(None)
    except Exception:
        pass

    client = RedfingerBotClient()
    _active_client = client
    client.is_bot_active = False
    client.current_stage = "IDLE (รอสั่งเริ่มจากเว็บ)"

    if server_url:
        client.config["server_ws_url"] = server_url
    if device_id:
        client.config["device_id"] = device_id
    if room_id:
        client.config["room_id"] = room_id

    print("🤖 Python Agent initialized in STANDBY mode (Waiting for start from Web Dashboard)...")
    try:
        loop = asyncio.new_event_loop()
        client.loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(client.run())
    except Exception as e:
        import traceback
        print(f"❌ Fatal error in Python loop:\n{traceback.format_exc()}")
        raise
    finally:
        try:
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

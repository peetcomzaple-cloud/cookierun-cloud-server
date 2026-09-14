"""
StaggerCoordinator - ระบบควบคุมและซิงค์โหมดคู่หู (Partner Mode / Anti-Collision)
สำหรับจัดการโทรศัพท์ Redfinger Cloud Phones หลายเครื่องที่เชื่อมต่อผ่านอินเทอร์เน็ต
"""

import time
import asyncio
from datetime import datetime
from typing import Dict, Optional, Tuple, Any, List

class CloudDeviceState:
    def __init__(self, device_id: str, room_id: str = "default_pair"):
        self.device_id = device_id
        self.room_id = room_id
        self.name = device_id
        self.status = "IDLE"                  # Default to IDLE standby until user clicks Start on Web Dashboard!
        self.is_user_stopped = True           # Standby by default
        self.is_in_game = False
        self.current_stage = "IDLE (รอสั่งเริ่มจากเว็บ)"
        self.rounds_played = 0
        self.in_run_start_time = 0.0
        self.first_box_second = 0.0
        self.first_box_wall_time = 0.0
        self.has_paused_for_box = False
        self.is_paused_waiting_for_partner = False
        self.in_result_screen = False
        self.session_coins = 0
        self.session_xp = 0
        self.last_round_coins = 0
        self.last_round_xp = 0
        self.box_counts = {"rainbow": 0, "gold": 0, "silver": 0, "bronze": 0, "wood": 0}
        self.ticket_counts = {"rainbow": 0, "gold": 0}
        self.last_heartbeat = time.time()
        self.ws = None
        self.latest_frame_bytes: Optional[bytes] = None
        self.round_history: List[Dict[str, Any]] = []
        self.settings: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "room_id": self.room_id,
            "name": self.name,
            "status": "IDLE" if getattr(self, "is_user_stopped", False) else self.status,
            "is_in_game": False if getattr(self, "is_user_stopped", False) else self.is_in_game,
            "current_stage": "IDLE (Stopped)" if getattr(self, "is_user_stopped", False) else self.current_stage,
            "rounds_played": self.rounds_played,
            "first_box_second": self.first_box_second,
            "session_coins": self.session_coins,
            "session_xp": self.session_xp,
            "last_round_coins": self.last_round_coins,
            "last_round_xp": self.last_round_xp,
            "box_counts": self.box_counts,
            "ticket_counts": self.ticket_counts,
            "has_frame": self.latest_frame_bytes is not None,
            "last_seen": round(time.time() - self.last_heartbeat, 1)
        }


class StaggerCoordinator:
    def __init__(self):
        self.devices: Dict[str, CloudDeviceState] = {}
        self.rooms: Dict[str, List[str]] = {}  # room_id -> list of device_ids
        self.room_settings: Dict[str, Dict[str, Any]] = {}  # room_id -> dict of settings (box_gap_seconds, box_pause_duration, etc.)
        self.lock = asyncio.Lock()

    def register_device(self, device_id: str, room_id: Optional[str] = None, ws: Any = None) -> CloudDeviceState:
        # If room_id is default_pair or not specified, put into independent single room by default
        actual_room = room_id if (room_id and room_id not in ("default_pair", "default", "none")) else f"single_{device_id}"
        if device_id not in self.devices:
            self.devices[device_id] = CloudDeviceState(device_id, actual_room)
        
        dev = self.devices[device_id]
        dev.room_id = actual_room
        dev.ws = ws
        dev.last_heartbeat = time.time()
        dev.status = "IDLE"                   # Default to IDLE standby upon connect!
        dev.is_user_stopped = True            # Require explicit start from Web Dashboard
        dev.current_stage = "IDLE (รอสั่งเริ่มจากเว็บ)"

        if actual_room not in self.rooms:
            self.rooms[actual_room] = []
        if device_id not in self.rooms[actual_room]:
            self.rooms[actual_room].append(device_id)

        return dev

    def _unpair(self, device_id: str):
        """Removes device from any current paired room, reverting remaining devices to single."""
        dev = self.devices.get(device_id)
        if not dev or not dev.room_id:
            return
        old_room = dev.room_id
        if old_room in self.rooms:
            if device_id in self.rooms[old_room]:
                self.rooms[old_room].remove(device_id)
            # If a partner remains alone in old pair room, revert them to single mode
            for remaining_id in list(self.rooms[old_room]):
                rem_dev = self.devices.get(remaining_id)
                if rem_dev:
                    rem_dev.room_id = f"single_{remaining_id}"
                    rem_dev.settings["stagger_mode"] = False
                    rem_dev.settings["stagger_partner_id"] = None
                    if rem_dev.room_id not in self.rooms:
                        self.rooms[rem_dev.room_id] = []
                    self.rooms[rem_dev.room_id].append(remaining_id)
            if not self.rooms[old_room]:
                del self.rooms[old_room]

    def set_pair(self, dev1_id: str, dev2_id: Optional[str], enabled: bool = True):
        """
        Dynamically pairs dev1 and dev2 into a shared pair room.
        If enabled is False or dev2 is empty/none, both are set to independent single mode.
        """
        dev1 = self.devices.get(dev1_id)
        if not dev1:
            return

        self._unpair(dev1_id)

        if not enabled or not dev2_id or dev2_id in ("none", "", "null", "auto", "default") or dev2_id not in self.devices:
            dev1.room_id = f"single_{dev1_id}"
            dev1.settings["stagger_mode"] = False
            dev1.settings["stagger_partner_id"] = None
            if dev1.room_id not in self.rooms:
                self.rooms[dev1.room_id] = []
            if dev1_id not in self.rooms[dev1.room_id]:
                self.rooms[dev1.room_id].append(dev1_id)
            return

        dev2 = self.devices.get(dev2_id)
        self._unpair(dev2_id)

        pair_room = f"pair_{min(dev1_id, dev2_id)}_{max(dev1_id, dev2_id)}"
        dev1.room_id = pair_room
        dev2.room_id = pair_room
        dev1.settings["stagger_mode"] = True
        dev1.settings["stagger_partner_id"] = dev2_id
        dev2.settings["stagger_mode"] = True
        dev2.settings["stagger_partner_id"] = dev1_id

        self.rooms[pair_room] = [dev1_id, dev2_id]
        print(f"🔗 [StaggerCoordinator] Paired {dev1_id} <-> {dev2_id} in room '{pair_room}'")

    def remove_device(self, device_id: str):
        """Completely removes a device from registry and room assignments."""
        if device_id in self.devices:
            dev = self.devices[device_id]
            del self.devices[device_id]
            self._unpair(device_id)

    def unregister_device(self, device_id: str):
        """Called when WebSocket closes. Immediately purges disconnected device."""
        self.remove_device(device_id)

    def cleanup_stale_devices(self, max_stale_seconds: float = 120.0) -> List[str]:
        """
        Auto-prunes any devices that disconnected or haven't sent a heartbeat for > max_stale_seconds.
        Ensures dead/phantom devices disappear from the dashboard within seconds.
        """
        now = time.time()
        stale_ids = []
        for dev_id, dev in list(self.devices.items()):
            is_stale = (now - dev.last_heartbeat > max_stale_seconds) or (dev.status == "DISCONNECTED") or (dev.ws is None)
            if is_stale:
                stale_ids.append(dev_id)
                self.remove_device(dev_id)
        return stale_ids

    def clear_all(self) -> int:
        """Force-clears all devices and rooms from memory."""
        count = len(self.devices)
        self.devices.clear()
        self.rooms.clear()
        return count

    def get_partner(self, device_id: str) -> Optional[CloudDeviceState]:
        dev = self.devices.get(device_id)
        if not dev or not dev.room_id or dev.room_id.startswith("single_"):
            return None
        if not dev.settings.get("stagger_mode", False):
            return None
        room_devs = self.rooms.get(dev.room_id, [])
        for pid in room_devs:
            if pid != device_id and pid in self.devices:
                p = self.devices[pid]
                if p.status != "DISCONNECTED" and (time.time() - p.last_heartbeat < 15):
                    return p
        return None

    def check_can_start_round(self, device_id: str) -> Tuple[bool, str]:
        """
        ตรวจสอบว่าอุปกรณ์สามารถเริ่มรอบใหม่ได้หรือไม่
        หาก Stagger Mode เปิดอยู่และคู่หูยังวิ่งอยู่ในช่วง gap_seconds แรก ให้รอก่อน
        """
        dev = self.devices.get(device_id)
        partner = self.get_partner(device_id)
        if not partner:
            return True, "วิ่งเดี่ยว (ไม่มีคู่หูออนไลน์)"

        # ดึงค่า gap_seconds จาก settings ของอุปกรณ์นี้ (หรือ partner)
        gap_seconds = float(
            (dev.settings if dev else {}).get("box_gap_seconds")
            or partner.settings.get("box_gap_seconds", 0.0)
        )

        if gap_seconds > 0 and partner.is_in_game and partner.in_run_start_time > 0:
            elapsed = time.time() - partner.in_run_start_time
            remaining = gap_seconds - elapsed
            if remaining > 0:
                return False, f"⏳ รอ Gap {remaining:.1f}s ก่อนเริ่มรอบใหม่ (คู่หูวิ่งอยู่)"

        return True, "พร้อมเริ่มวิ่งได้ทันที (ระบบซิงค์รอหน้าสรุปผล)"

    async def handle_first_box_event(self, device_id: str, box_second: float) -> Optional[str]:
        """Stats tracking only. No pause is triggered by mystery box timing anymore."""
        dev = self.devices.get(device_id)
        if dev:
            dev.first_box_second = box_second
        return None

    async def notify_result_entered(self, device_id: str) -> Optional[str]:
        """
        Called when a device enters GAME_COMPLETE (Result screen).
        If its partner is currently in-game, pause the partner immediately so they don't finish simultaneously!
        """
        dev = self.devices.get(device_id)
        if not dev:
            return None

        dev.in_result_screen = True
        partner = self.get_partner(device_id)
        if not partner or not partner.is_in_game:
            return f"📊 [Result Screen] {dev.name} เข้าสู่หน้าสรุปผล"

        # Partner is still running in game! Pause partner to avoid collision!
        if not partner.is_paused_waiting_for_partner:
            partner.is_paused_waiting_for_partner = True
            # ใช้ค่า box_pause_duration จาก settings (default 40s)
            pause_max_wait = float(
                dev.settings.get("box_pause_duration")
                or partner.settings.get("box_pause_duration", 40.0)
            )
            if pause_max_wait <= 0:
                pause_max_wait = 40.0
            msg = f"⏱️ [Result Sync] {dev.name} ถึงหน้าสรุปผลแล้ว! สั่ง {partner.name} กด Pause พักจอรอ (max {pause_max_wait:.0f}s)..."
            print(msg)
            if partner.ws:
                try:
                    await partner.ws.send_json({
                        "type": "COMMAND",
                        "command": "STAGGER_PAUSE",
                        "mode": "RESULT_SYNC",
                        "max_wait": pause_max_wait,
                        "reason": f"คู่หู ({dev.name}) เข้าสู่หน้าสรุปผล — รอคู่หูกด OK"
                    })
                except Exception as e:
                    print(f"Error sending STAGGER_PAUSE to {partner.name}: {e}")
            return msg
        return None

    async def notify_result_finished(self, device_id: str) -> Optional[str]:
        """
        Called when a runner finishes the Result screen (GAME_COMPLETE, taps OK) and returns to Lobby.
        Immediately resumes any partner that was paused waiting for this device.
        """
        dev = self.devices.get(device_id)
        if not dev:
            return None

        dev.in_result_screen = False
        partner = self.get_partner(device_id)
        if not partner:
            return None

        if partner.is_paused_waiting_for_partner:
            partner.is_paused_waiting_for_partner = False
            msg = f"🟢 [Result Sync] {dev.name} กด OK สรุปผลเสร็จแล้ว -> สั่ง {partner.name} ปลด Pause วิ่งต่อทันที!"
            print(msg)
            if partner.ws:
                try:
                    await partner.ws.send_json({
                        "type": "COMMAND",
                        "command": "STAGGER_RESUME",
                        "reason": f"คู่หู ({dev.name}) สรุปผลเสร็จแล้ว"
                    })
                except Exception as e:
                    print(f"Error sending STAGGER_RESUME to {partner.name}: {e}")
            return msg
        return None


coordinator = StaggerCoordinator()


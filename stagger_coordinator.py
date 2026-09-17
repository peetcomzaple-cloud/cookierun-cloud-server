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
        self.status = "RUNNING"
        self.is_user_stopped = False
        self.is_in_game = False
        self.current_stage = "RUNNING"
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
        self.latest_loot_image_bytes: Optional[bytes] = None
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
            "has_loot_image": self.latest_loot_image_bytes is not None,
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
        is_new = device_id not in self.devices
        if is_new:
            self.devices[device_id] = CloudDeviceState(device_id, actual_room)
        
        dev = self.devices[device_id]
        dev.room_id = actual_room
        dev.ws = ws
        dev.last_heartbeat = time.time()
        # Preserve active running state across reconnects so bot is never killed by network drops!
        if is_new:
            dev.status = "RUNNING"
            dev.is_user_stopped = False
            dev.current_stage = "RUNNING"
        else:
            if not dev.is_user_stopped:
                dev.status = "RUNNING"

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
        room_devs = self.rooms.get(dev.room_id, [])
        for pid in room_devs:
            if pid != device_id and pid in self.devices:
                p = self.devices[pid]
                if p.status != "DISCONNECTED" and (time.time() - p.last_heartbeat < 30):
                    return p
        return None

    def check_can_start_round(self, device_id: str) -> Tuple[bool, str]:
        """
        ตรวจสอบว่าอุปกรณ์สามารถเริ่มรอบใหม่ได้หรือไม่
        ระบบ Lobby Gate Anti-Collision:
        1. หากคู่หูกำลังอยู่ในหน้าสรุปผล (Result Screen / Mystery Box) ให้รอก่อนเพื่อไม่ให้ชนกัน
        2. หากคู่หูเพิ่งเริ่มวิ่ง ให้เว้นระยะห่าง (Stagger Gap) อย่างน้อย 20 วินาที
        """
        dev = self.devices.get(device_id)
        partner = self.get_partner(device_id)
        if not partner:
            return True, "วิ่งเดี่ยว (ไม่มีคู่หูออนไลน์)"

        # 1. ป้องกันจอชนกัน: ถ้าคู่หูกำลังสรุปผลอยู่ ให้รอก่อนจนกว่าคู่หูจะผ่านหน้าสรุปผลกลับเข้าล็อบบี้
        if getattr(partner, "in_result_screen", False) or partner.current_stage in ("GAME_COMPLETE", "GAME_COMPLETE (สรุปผล)", "MYSTERY_BOX"):
            return False, f"⏳ รอคู่หู ({partner.name}) สรุปผลกลับสู่ Lobby ก่อน เพื่อป้องกันจอชนกัน"

        # 2. ป้องกันจอชนกัน: เว้นระยะการออกตัว (Stagger Gap) ค่าเริ่มต้น 20 วินาที
        dev_gap = (dev.settings if dev else {}).get("box_gap_seconds")
        partner_gap = partner.settings.get("box_gap_seconds")
        raw_gap = dev_gap if dev_gap is not None else (partner_gap if partner_gap is not None else 20.0)
        gap_seconds = float(raw_gap)

        if gap_seconds > 0 and partner.is_in_game and partner.in_run_start_time > 0:
            elapsed = time.time() - partner.in_run_start_time
            remaining = gap_seconds - elapsed
            if remaining > 0:
                return False, f"⏳ เว้นระยะห่าง (Stagger Gap) {remaining:.1f}s ก่อนเริ่มวิ่ง"

        return True, f"พร้อมเริ่มวิ่งได้ทันที (จับคู่ห้อง: {dev.room_id})"

    async def handle_first_box_event(self, device_id: str, box_second: float) -> Optional[str]:
        """Stats tracking only. No pause is triggered by mystery box timing anymore."""
        dev = self.devices.get(device_id)
        if dev:
            dev.first_box_second = box_second
        return None

    async def notify_result_entered(self, device_id: str) -> Optional[str]:
        """
        Called when a device enters GAME_COMPLETE (Result screen).
        Broadcasts status to partner device so if partner's HP depletes, partner will pause!
        """
        dev = self.devices.get(device_id)
        if not dev:
            return None

        dev.in_result_screen = True
        partner = self.get_partner(device_id)
        if not partner:
            return f"📊 [Result Screen] {dev.name} เข้าสู่หน้าสรุปผล"

        msg = f"📊 [Result Screen] {dev.name} เข้าสู่หน้าสรุปผล -> ส่งสัญญาณบอกคู่หู ({partner.name})"
        print(msg)
        if partner.ws:
            try:
                await partner.ws.send_json({
                    "type": "PARTNER_RESULT_STATUS",
                    "in_result": True,
                    "partner_id": dev.device_id,
                    "reason": f"คู่หู ({dev.name}) เข้าสู่หน้าสรุปผลแล้ว"
                })
            except Exception as e:
                print(f"Error sending PARTNER_RESULT_STATUS to {partner.name}: {e}")
        return msg

    async def notify_result_finished(self, device_id: str) -> Optional[str]:
        """
        Called when a runner finishes the Result screen (GAME_COMPLETE, taps OK) and returns to Lobby.
        Immediately notifies partner that result screen is cleared, and resumes if partner was paused!
        """
        dev = self.devices.get(device_id)
        if not dev:
            return None

        dev.in_result_screen = False
        partner = self.get_partner(device_id)
        if not partner:
            return None

        msg = f"🟢 [Result Screen] {dev.name} ผ่านหน้าสรุปผลกลับสู่ Lobby -> ส่งสัญญาณบอกคู่หู ({partner.name})"
        print(msg)
        if partner.ws:
            try:
                await partner.ws.send_json({
                    "type": "PARTNER_RESULT_STATUS",
                    "in_result": False,
                    "partner_id": dev.device_id,
                    "reason": f"คู่หู ({dev.name}) กลับสู่หน้า Lobby แล้ว"
                })
                # If partner was paused waiting, also send STAGGER_RESUME command directly
                if partner.is_paused_waiting_for_partner:
                    partner.is_paused_waiting_for_partner = False
                    await partner.ws.send_json({
                        "type": "COMMAND",
                        "command": "STAGGER_RESUME",
                        "reason": f"คู่หู ({dev.name}) สรุปผลเสร็จกลับเข้า Lobby แล้ว"
                    })
            except Exception as e:
                print(f"Error sending resume to {partner.name}: {e}")
        return msg

    def set_device_paused_status(self, device_id: str, is_paused: bool):
        dev = self.devices.get(device_id)
        if dev:
            dev.is_paused_waiting_for_partner = is_paused


coordinator = StaggerCoordinator()


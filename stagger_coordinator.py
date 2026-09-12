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
        self.status = "RUNNING"               # Default to RUNNING so devices are active when connected
        self.is_user_stopped = False        # Not stopped by default
        self.is_in_game = False
        self.current_stage = "IDLE (Ready)"
        self.rounds_played = 0
        self.in_run_start_time = 0.0
        self.first_box_second = 0.0
        self.first_box_wall_time = 0.0
        self.has_paused_for_box = False
        self.session_coins = 0
        self.session_xp = 0
        self.last_round_coins = 0
        self.last_round_xp = 0
        self.box_counts = {"rainbow": 0, "gold": 0, "silver": 0, "bronze": 0}
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

    def register_device(self, device_id: str, room_id: str = "default_pair", ws: Any = None) -> CloudDeviceState:
        if device_id not in self.devices:
            self.devices[device_id] = CloudDeviceState(device_id, room_id)
        
        dev = self.devices[device_id]
        dev.room_id = room_id
        dev.ws = ws
        dev.last_heartbeat = time.time()
        dev.status = "RUNNING"
        dev.is_user_stopped = False

        if room_id not in self.rooms:
            self.rooms[room_id] = []
        if device_id not in self.rooms[room_id]:
            self.rooms[room_id].append(device_id)

        return dev

    def remove_device(self, device_id: str):
        """Completely removes a device from registry and room assignments."""
        if device_id in self.devices:
            dev = self.devices[device_id]
            del self.devices[device_id]
            if dev.room_id in self.rooms:
                if device_id in self.rooms[dev.room_id]:
                    self.rooms[dev.room_id].remove(device_id)
                if not self.rooms[dev.room_id]:
                    del self.rooms[dev.room_id]

    def unregister_device(self, device_id: str):
        """Called when WebSocket closes. Immediately purges disconnected device."""
        self.remove_device(device_id)

    def cleanup_stale_devices(self, max_stale_seconds: float = 60.0) -> List[str]:
        """
        Auto-prunes any devices that disconnected or haven't sent a heartbeat for > max_stale_seconds.
        Ensures dead/phantom devices disappear from the dashboard automatically.
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
        if not dev or not dev.room_id:
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
        Start check: Immediate start allowed for all screens (no startup delay between partners).
        Stagger separation is strictly handled via in-game first mystery box detection.
        """
        partner = self.get_partner(device_id)
        if not partner:
            return True, "วิ่งเดี่ยว (ไม่มีคู่หูออนไลน์)"
        return True, "พร้อมเริ่มวิ่งได้ทันที (จับเวลาเว้นระยะจากกล่องแรก)"

    async def handle_first_box_event(self, device_id: str, box_second: float) -> Optional[str]:
        """
        Called when a device collects the first mystery box.
        Evaluates whether partner device needs to execute an intentional pause.
        Matches original PC bot algorithm: compares wall-clock timestamps of both runners.
        """
        dev = self.devices.get(device_id)
        if not dev:
            return None

        dev.first_box_second = box_second
        dev.first_box_wall_time = time.time()

        partner = self.get_partner(device_id)
        if not partner or not partner.is_in_game:
            return None

        # Wait until both runners record their first box in this round
        if partner.first_box_wall_time <= 0:
            return f"📦 [First Box] {dev.name} เก็บกล่องแรกที่ {box_second:.1f}s — รอจอคู่หู ({partner.name}) เจอกล่องแรกเพื่อวัดระยะห่าง"

        gap = abs(dev.first_box_wall_time - partner.first_box_wall_time)

        # Dynamic user-configurable threshold & pause duration from settings (matches PC bot)
        room_cfg = getattr(self, "room_settings", {}).get(dev.room_id, {})
        threshold = float(dev.settings.get("box_gap_seconds") or partner.settings.get("box_gap_seconds") or room_cfg.get("box_gap_seconds", 6.0))
        pause_duration = float(dev.settings.get("box_pause_duration") or partner.settings.get("box_pause_duration") or room_cfg.get("box_pause_duration", 5.0))

        if gap >= threshold:
            return f"✅ [Anti-Collision] ระยะห่างกล่องแรกระหว่าง {dev.name} กับ {partner.name} = {gap:.1f}s (ปลอดภัย >= {threshold:.1f}s) — ไม่ต้องหยุดชะลอ"

        # Gap is too close (< threshold)! Pause the slower runner (the one that collected the box second)
        target = dev if dev.first_box_wall_time > partner.first_box_wall_time else partner
        if not target.has_paused_for_box:
            target.has_paused_for_box = True
            msg = f"⚠️ [Anti-Collision] ระยะห่างกล่องแรกเพียง {gap:.1f}s (< {threshold:.1f}s) ใกล้กันเกินไป! สั่ง {target.name} กด Pause ชะลอ {pause_duration:.1f}s!"
            if target.ws:
                try:
                    await target.ws.send_json({
                        "type": "COMMAND",
                        "command": "STAGGER_PAUSE",
                        "duration": pause_duration,
                        "reason": f"ระยะห่างกล่องแรก {gap:.1f}s ใกล้เกินไป (เกณฑ์: {threshold:.1f}s)"
                    })
                except Exception:
                    pass
            return msg

        return None


coordinator = StaggerCoordinator()

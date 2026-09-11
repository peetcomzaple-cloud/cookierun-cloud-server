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
        self.status = "CONNECTED"          # CONNECTED, IDLE, IN_GAME, PAUSED, DISCONNECTED
        self.is_in_game = False
        self.current_stage = "IDLE"
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
            "status": self.status,
            "is_in_game": self.is_in_game,
            "current_stage": self.current_stage,
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
        self.lock = asyncio.Lock()

    def register_device(self, device_id: str, room_id: str = "default_pair", ws: Any = None) -> CloudDeviceState:
        if device_id not in self.devices:
            self.devices[device_id] = CloudDeviceState(device_id, room_id)
        
        dev = self.devices[device_id]
        dev.room_id = room_id
        dev.ws = ws
        dev.last_heartbeat = time.time()
        dev.status = "CONNECTED"

        if room_id not in self.rooms:
            self.rooms[room_id] = []
        if device_id not in self.rooms[room_id]:
            self.rooms[room_id].append(device_id)

        return dev

    def unregister_device(self, device_id: str):
        if device_id in self.devices:
            dev = self.devices[device_id]
            dev.status = "DISCONNECTED"
            dev.ws = None

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
        Anti-Collision start check:
        Ensures 2 devices in the same room don't start at the exact same second.
        """
        partner = self.get_partner(device_id)
        if not partner:
            return True, "วิ่งเดี่ยว (ไม่มีคู่หูออนไลน์)"

        if partner.is_in_game:
            partner_elapsed = time.time() - partner.in_run_start_time if partner.in_run_start_time > 0 else 0
            # If partner just started within 12 seconds, wait briefly to establish time separation
            if partner_elapsed < 12.0:
                wait_sec = int(12.0 - partner_elapsed)
                return False, f"คู่หู ({partner.name}) เพิ่งเริ่มวิ่งไป {partner_elapsed:.1f}s — รอเว้นระยะ {wait_sec}s เพื่อไม่ให้ชนกัน"

        return True, "พร้อมเริ่มวิ่งได้ทันที (ระยะเวลาปลอดภัย)"

    async def handle_first_box_event(self, device_id: str, box_second: float) -> Optional[str]:
        """
        Called when a device collects the first mystery box.
        Evaluates whether partner device needs to execute an intentional pause.
        """
        dev = self.devices.get(device_id)
        if not dev:
            return None

        dev.first_box_second = box_second
        dev.first_box_wall_time = time.time()

        partner = self.get_partner(device_id)
        if not partner or not partner.is_in_game:
            return None

        # Check if partner is also running and hasn't paused yet
        if not partner.has_paused_for_box:
            partner_elapsed = time.time() - partner.in_run_start_time if partner.in_run_start_time > 0 else 0
            pause_duration = 5.0
            partner.has_paused_for_box = True
            msg = f"📦 [Stagger Sync] {dev.name} ดรอปกล่องแรกที่ {box_second:.1f}s -> ส่งคำสั่งให้ {partner.name} กด Pause ชะลอ {pause_duration}s!"
            
            # Send pause command to partner client
            if partner.ws:
                try:
                    await partner.ws.send_json({
                        "type": "COMMAND",
                        "command": "STAGGER_PAUSE",
                        "duration": pause_duration,
                        "reason": f"คู่หู ({dev.name}) เจอกล่องแรกที่วินาทีที่ {box_second:.1f}"
                    })
                except Exception:
                    pass
            return msg

        return None


coordinator = StaggerCoordinator()

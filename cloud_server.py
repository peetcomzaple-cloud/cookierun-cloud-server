"""
CookieRun Bot - Central Cloud Server & Web Dashboard
Server กลางสำหรับรับการเชื่อมต่อจาก Redfinger Cloud Phone / มือถือ ผ่าน WebSocket
พร้อมระบบ Dashboard สตรีมจอและประสานงานโหมดคู่หู (Anti-Collision / Stagger Sync)
"""

import os
import sys
import time
import base64
import asyncio
import socket
import platform
from typing import Dict, List, Optional, Any
from datetime import datetime

# Prevent UnicodeEncodeError on Windows Thai (CP874) console
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

from stagger_coordinator import coordinator, CloudDeviceState

app = FastAPI(title="CookieRun Cloud Bot Hub", version="1.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
SERVER_START_TIME = time.time()


def format_status_dict(dev: Optional[CloudDeviceState] = None) -> Dict[str, Any]:
    """Builds a rich status dictionary fully compatible with index.html Web Dashboard."""
    if dev is None and coordinator.devices:
        dev = next(iter(coordinator.devices.values()), None)

    if dev is None:
        # Return fallback status when no device connected yet
        uptime_sec = int(time.time() - SERVER_START_TIME)
        h, m, s = uptime_sec // 3600, (uptime_sec % 3600) // 60, uptime_sec % 60
        return {
            "device_id": "none",
            "is_running": False,
            "status": "online",
            "online": True,
            "is_online": True,
            "current_stage": "รอเชื่อมต่อจาก Redfinger...",
            "speed_mode": "Normal",
            "stagger_mode": True,
            "stagger_partner_id": None,
            "box_gap_seconds": 6.0,
            "box_pause_duration": 5.0,
            "first_box_second": 0.0,
            "has_paused_for_box": False,
            "is_in_game": False,
            "uptime": f"{h:02d}:{m:02d}:{s:02d}",
            "rounds_played": 0,
            "mystery_boxes": 0,
            "box_counts": {"rainbow": 0, "gold": 0, "silver": 0, "bronze": 0},
            "ticket_counts": {"rainbow": 0, "gold": 0},
            "coin_stats": {
                "session_earned": 0,
                "session_xp_earned": 0,
                "coins_per_hour": 0,
                "last_round": 0,
                "last_round_xp": 0,
            },
            "round_history": [],
            "running_instances_count": 0,
            "total_instances_count": 0,
            "is_any_running": False,
            "pc_name": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    uptime_sec = int(time.time() - SERVER_START_TIME)
    h, m, s = uptime_sec // 3600, (uptime_sec % 3600) // 60, uptime_sec % 60
    running_count = sum(1 for d in coordinator.devices.values() if d.status == "RUNNING" and not getattr(d, "is_user_stopped", False))
    partner = coordinator.get_partner(dev.device_id)

    all_devs = list(coordinator.devices.values())
    s1 = dev
    s2 = partner if partner else (all_devs[1] if len(all_devs) > 1 and all_devs[0].device_id == dev.device_id else (all_devs[0] if len(all_devs) > 1 and all_devs[1].device_id == dev.device_id else None))

    is_dev_running = (dev.status == "RUNNING") and not getattr(dev, "is_user_stopped", False)
    coins_per_hr = int(dev.session_coins / max(0.001, uptime_sec / 3600)) if (is_dev_running and uptime_sec > 5) else 0

    s1_running = (s1.status == "RUNNING" and not getattr(s1, "is_user_stopped", False)) if s1 else False
    s2_running = (s2.status == "RUNNING" and not getattr(s2, "is_user_stopped", False)) if s2 else False

    combined_stats = {
        "enabled": len(all_devs) >= 2 or partner is not None,
        "partner_id": s2.device_id if s2 else None,
        "partner_running": s2_running,
        "partner_stage": "IDLE (Stopped)" if (s2 and getattr(s2, "is_user_stopped", False)) else (s2.current_stage if s2 else "IDLE"),
        "screen1_id": s1.device_id if s1 else None,
        "screen1_running": s1_running,
        "screen1_stage": "IDLE (Stopped)" if (s1 and getattr(s1, "is_user_stopped", False)) else (s1.current_stage if s1 else "IDLE"),
        "screen2_id": s2.device_id if s2 else None,
        "screen2_running": s2_running,
        "screen2_stage": "IDLE (Stopped)" if (s2 and getattr(s2, "is_user_stopped", False)) else (s2.current_stage if s2 else "IDLE"),
    }

    return {
        "device_id": dev.device_id,
        "is_running": is_dev_running,
        "status": "online",
        "online": True,
        "is_online": True,
        "current_stage": "IDLE (Stopped)" if getattr(dev, "is_user_stopped", False) else dev.current_stage,
        "speed_mode": "Normal",
        "stagger_mode": True,
        "stagger_partner_id": partner.device_id if partner else None,
        "combined_stats": combined_stats,
        "box_gap_seconds": 6.0,
        "box_pause_duration": 5.0,
        "first_box_second": dev.first_box_second,
        "has_paused_for_box": dev.has_paused_for_box,
        "is_in_game": False if getattr(dev, "is_user_stopped", False) else dev.is_in_game,
        "uptime": f"{h:02d}:{m:02d}:{s:02d}",
        "rounds_played": dev.rounds_played,
        "mystery_boxes": sum(dev.box_counts.values()),
        "box_counts": dev.box_counts,
        "ticket_counts": dev.ticket_counts,
        "coin_stats": {
            "session_earned": dev.session_coins,
            "session_xp_earned": dev.session_xp,
            "coins_per_hour": coins_per_hr,
            "last_round": dev.last_round_coins,
            "last_round_xp": dev.last_round_xp,
        },
        "round_history": list(dev.round_history),
        "running_instances_count": running_count,
        "total_instances_count": len(coordinator.devices),
        "is_any_running": running_count > 0,
        "pc_name": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class ActionRequest(BaseModel):
    action: Optional[str] = "start"
    room_id: Optional[str] = "default_pair"


BOOST_OPTIONS = [
    {"id": "double_coins", "name": "เหรียญ x2 (Double Coins)", "template": "BOOST_DOUBLE_COINS_1.png"},
    {"id": "coin_magic", "name": "เปลี่ยนสิ่งกีดขวางเป็นเหรียญทอง (Coin Magic)", "template": "BOOST_GOLD_COIN_MAGIC_1.png"},
    {"id": "magnetic_aura", "name": "พลังแม่เหล็ก (Magnetic Aura)", "template": "BOOST_MAGNETIC_AURA_1.png"},
    {"id": "score_bonus", "name": "คะแนนโบนัส +15% (Score Bonus)", "template": "BOOST_15P_SCORE_BONUS_1.png"},
    {"id": "base_speed", "name": "ความเร็วพื้นฐาน +17% (Base Speed)", "template": "BOOST_17P_BASE_SPEED_1.png"},
    {"id": "hp_drain", "name": "พลังชีวิตลดช้าลง 15% (HP Drain)", "template": "BOOST_M15P_HP_DRAIN_1.png"},
    {"id": "revive_80hp", "name": "ฟื้นคืนชีพ 1 ครั้งด้วย 80HP (Revive)", "template": "BOOST_REVIVE_ONCE_WITH_80HP_1.png"},
    {"id": "crush_chance", "name": "โอกาสทำลายสิ่งกีดขวาง 70% (Crush Chance)", "template": "BOOST_70P_CRUSH_CHANCE_1.png"},
    {"id": "hp_potions", "name": "ฟื้นฟู HP จากขวดยา +20% (HP from Potions)", "template": "BOOST_20P_HP_FROM_POTIONS_1.png"},
    {"id": "collision_damage", "name": "ความเสียหายจากการชนลดลง 30% (Collision Damage)", "template": "BOOST_M30P_COLLISION_DAMAGE_1.png"},
    {"id": "pit_lifts", "name": "ช่วยตกหลุม 2 ครั้ง (Pit Lifts)", "template": "BOOST_2PIT_LIFTS_1.png"},
]


@app.get("/api/boosts")
async def get_boost_options():
    return BOOST_OPTIONS


@app.post("/api/instances/{device_id}/settings/save")
@app.post("/api/settings/save")
async def save_instance_settings(device_id: Optional[str] = None, settings: Dict[str, Any] = None):
    dev = coordinator.devices.get(device_id) if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.values()), None)
    if dev and settings:
        dev.settings.update(settings)
        if dev.ws:
            await dev.ws.send_json({"type": "SETTINGS_UPDATE", "settings": dev.settings})
        return {"status": "ok", "settings": dev.settings}
    return {"status": "error", "message": "Device not found or empty settings"}


@app.get("/api/pc-status")
@app.get("/api/heartbeat")
@app.get("/api/ping")
async def ping():
    return {"status": "ok", "time": time.time()}


@app.get("/api/status")
async def get_status():
    """Default status endpoint called by Web Dashboard."""
    # Pick the first active device, or fallback
    first_dev = next(iter(coordinator.devices.values()), None)
    return format_status_dict(first_dev)


@app.get("/api/instances")
async def get_instances():
    """Returns all connected devices/instances for multi-instance selector."""
    instances = []
    for dev_id, dev in coordinator.devices.items():
        partner = coordinator.get_partner(dev_id)
        is_stopped = getattr(dev, "is_user_stopped", False)
        instances.append({
            "id": dev_id,
            "device_id": dev_id,
            "name": dev.name,
            "rounds": dev.rounds_played,
            "is_running": (dev.status == "RUNNING") and not is_stopped,
            "current_stage": "IDLE (Stopped)" if is_stopped else dev.current_stage,
            "rounds_played": dev.rounds_played,
            "is_in_game": False if is_stopped else dev.is_in_game,
            "stagger_mode": True,
            "stagger_partner_id": partner.device_id if partner else None,
            "status": "IDLE" if is_stopped else dev.status
        })
    return {"instances": instances, "active_device_id": next(iter(coordinator.devices.keys()), "")}


@app.get("/api/instances/{device_id}/status")
async def get_instance_status(device_id: str):
    dev = coordinator.devices.get(device_id)
    if dev is None and (device_id in ("default", "none", "emulator-5554") or len(coordinator.devices) == 1):
        dev = next(iter(coordinator.devices.values()), None)
    return format_status_dict(dev)


@app.get("/api/devices")
async def get_devices():
    return [dev.to_dict() for dev in coordinator.devices.values()]


@app.post("/api/instances/{device_id}/start")
@app.post("/api/start")
async def start_device(device_id: Optional[str] = None):
    target_devs = []
    if device_id and device_id not in ("default", "all", "none") and device_id in coordinator.devices:
        target_dev = coordinator.devices[device_id]
        target_devs.append(target_dev)
        # If partner exists in room, start partner too so both screens run together!
        partner = coordinator.get_partner(target_dev.device_id)
        if partner and partner not in target_devs:
            target_devs.append(partner)
    else:
        target_devs = list(coordinator.devices.values())

    count = 0
    for dev in target_devs:
        dev.is_user_stopped = False
        dev.status = "RUNNING"
        if dev.ws:
            try:
                await dev.ws.send_json({"type": "COMMAND", "command": "START"})
            except Exception:
                pass
        count += 1
    return {"status": "ok", "message": f"Started {count} device(s)"}


@app.post("/api/instances/{device_id}/stop")
@app.post("/api/stop")
async def stop_device(device_id: Optional[str] = None):
    target_devs = []
    if device_id and device_id not in ("default", "all", "none") and device_id in coordinator.devices:
        target_dev = coordinator.devices[device_id]
        target_devs.append(target_dev)
        partner = coordinator.get_partner(target_dev.device_id)
        if partner and partner not in target_devs:
            target_devs.append(partner)
    else:
        # If default or unspecified, stop all active devices
        target_devs = list(coordinator.devices.values())

    count = 0
    for dev in target_devs:
        dev.is_user_stopped = True
        dev.status = "IDLE"
        dev.is_in_game = False
        if dev.ws:
            try:
                await dev.ws.send_json({"type": "COMMAND", "command": "STOP"})
            except Exception:
                pass
        count += 1
    return {"status": "ok", "message": f"Stopped {count} device(s)"}


@app.post("/api/instances/start-all")
async def start_all_devices():
    count = 0
    for dev in coordinator.devices.values():
        dev.is_user_stopped = False
        dev.status = "RUNNING"
        if dev.ws:
            try:
                await dev.ws.send_json({"type": "COMMAND", "command": "START"})
            except Exception:
                pass
        count += 1
    return {"status": "ok", "started_count": count}


@app.post("/api/instances/stop-all")
async def stop_all_devices():
    count = 0
    for dev in coordinator.devices.values():
        dev.is_user_stopped = True
        dev.status = "IDLE"
        dev.is_in_game = False
        if dev.ws:
            try:
                await dev.ws.send_json({"type": "COMMAND", "command": "STOP"})
            except Exception:
                pass
        count += 1
    return {"status": "ok", "stopped_count": count}


class TapRequest(BaseModel):
    x: int
    y: int


@app.post("/api/instances/{device_id}/tap")
@app.post("/api/tap")
async def tap_instance_endpoint(device_id: Optional[str] = None, req: Optional[TapRequest] = None):
    dev = coordinator.devices.get(device_id) if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.values()), None)
    if dev and dev.ws and req:
        await dev.ws.send_json({"type": "COMMAND", "command": "TAP", "x": req.x, "y": req.y})
        return {"success": True, "message": f"Tapped ({req.x}, {req.y}) on {dev.device_id}"}
    return {"success": False, "message": "Device not connected"}


@app.post("/api/instances/{device_id}/reset-app")
@app.post("/api/reset-app")
async def reset_app_endpoint(device_id: Optional[str] = None):
    dev = coordinator.devices.get(device_id) if device_id else next(iter(coordinator.devices.values()), None)
    if dev and dev.ws:
        await dev.ws.send_json({"type": "COMMAND", "command": "RESET_APP"})
        return {"status": "ok", "message": f"Sent RESET_APP to {dev.device_id}"}
    return {"status": "error", "message": "Device not connected"}


@app.get("/api/frame")
@app.get("/api/instances/{device_id}/frame")
@app.get("/api/device/{device_id}/frame")
async def get_device_frame(device_id: Optional[str] = None):
    dev = coordinator.devices.get(device_id) if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.values()), None)
    if not dev or not dev.latest_frame_bytes:
        raise HTTPException(status_code=404, detail="No frame available")
    return Response(content=dev.latest_frame_bytes, media_type="image/jpeg")


async def frame_stream_generator(device_id: Optional[str] = None):
    """Yields MJPEG stream from latest frames received from Redfinger."""
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    while True:
        dev = coordinator.devices.get(device_id) if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.values()), None)
        if dev and dev.latest_frame_bytes:
            yield boundary + dev.latest_frame_bytes + b"\r\n"
        await asyncio.sleep(0.5)


@app.get("/api/stream")
@app.get("/api/instances/{device_id}/stream")
async def mjpeg_stream(device_id: Optional[str] = None):
    return StreamingResponse(
        frame_stream_generator(device_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.websocket("/ws/device/{device_id}")
async def device_websocket_endpoint(websocket: WebSocket, device_id: str, room_id: str = "default_pair"):
    await websocket.accept()
    dev = coordinator.register_device(device_id, room_id, websocket)
    print(f"📱 [CloudHub] Device connected: {device_id} in room: {room_id}")
    if dev.settings:
        await websocket.send_json({"type": "CONFIG", "settings": dev.settings})
    # Ensure newly connected device stays in IDLE until user clicks START on Web Dashboard
    if getattr(dev, "is_user_stopped", True):
        try:
            await websocket.send_json({"type": "COMMAND", "command": "STOP"})
        except Exception:
            pass

    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            dev.last_heartbeat = time.time()

            if mtype == "HEARTBEAT":
                if getattr(dev, "is_user_stopped", False):
                    dev.status = "IDLE"
                else:
                    dev.status = msg.get("status", dev.status)
                dev.current_stage = "IDLE (Stopped)" if getattr(dev, "is_user_stopped", False) else msg.get("current_stage", dev.current_stage)
                dev.is_in_game = False if getattr(dev, "is_user_stopped", False) else msg.get("is_in_game", dev.is_in_game)
                dev.rounds_played = msg.get("rounds_played", dev.rounds_played)

            elif mtype == "FRAME":
                b64_data = msg.get("data")
                if b64_data:
                    try:
                        dev.latest_frame_bytes = base64.b64decode(b64_data)
                    except Exception:
                        pass

            elif mtype == "CHECK_CAN_START":
                if getattr(dev, "is_user_stopped", False):
                    await websocket.send_json({
                        "type": "CAN_START_RESPONSE",
                        "can_start": False,
                        "reason": "บอทถูกสั่งหยุดทำงานจาก Web Dashboard"
                    })
                else:
                    can_start, reason = coordinator.check_can_start_round(device_id)
                    await websocket.send_json({
                        "type": "CAN_START_RESPONSE",
                        "can_start": can_start,
                        "reason": reason
                    })

            elif mtype == "ROUND_START":
                if getattr(dev, "is_user_stopped", False):
                    dev.is_in_game = False
                    dev.status = "IDLE"
                    try:
                        await websocket.send_json({"type": "COMMAND", "command": "STOP"})
                    except Exception:
                        pass
                else:
                    dev.is_in_game = True
                    dev.in_run_start_time = time.time()
                    dev.first_box_second = 0.0
                    dev.first_box_wall_time = 0.0
                    dev.has_paused_for_box = False
                    dev.rounds_played = msg.get("round", dev.rounds_played + 1)
                    print(f"🏁 [{device_id}] Round {dev.rounds_played} started!")

            elif mtype == "FIRST_BOX":
                box_sec = float(msg.get("second", 0.0))
                sync_msg = await coordinator.handle_first_box_event(device_id, box_sec)
                if sync_msg:
                    print(sync_msg)

            elif mtype == "ROUND_COMPLETE":
                dev.is_in_game = False
                coins = int(msg.get("coins", 0))
                xp = int(msg.get("xp", 0))
                boxes = msg.get("boxes", [])
                
                dev.last_round_coins = coins
                dev.last_round_xp = xp
                dev.session_coins += coins
                dev.session_xp += xp

                for b in boxes:
                    if b in dev.box_counts:
                        dev.box_counts[b] += 1

                print(f"✅ [{device_id}] Round complete! Coins: +{coins}, XP: +{xp}, Boxes: {boxes}")

    except WebSocketDisconnect:
        print(f"⚠️ [CloudHub] Device disconnected: {device_id}")
        coordinator.unregister_device(device_id)
    except Exception as e:
        print(f"❌ [CloudHub] Error handling device {device_id}: {e}")
        coordinator.unregister_device(device_id)


@app.get("/")
async def serve_dashboard():
    for p in [os.path.join(WEB_DIR, "index.html"), os.path.join(BASE_DIR, "index.html")]:
        if os.path.exists(p):
            return FileResponse(p)
    return HTMLResponse("<h2>CookieRun Cloud Server is Running. Place web files in /web directory or root.</h2>")


if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
elif os.path.exists(BASE_DIR):
    app.mount("/static", StaticFiles(directory=BASE_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print("=" * 60)
    print("🍪 CookieRun Cloud Bot - Central Hub & Stagger Server")
    print(f"🚀 Server running on: http://0.0.0.0:{port}")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

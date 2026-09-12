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


def calculate_fifty_summary(dev: Optional[CloudDeviceState]) -> Dict[str, Any]:
    """Calculates summary metrics for the last 50 rounds for the dashboard card."""
    default_summary = {
        "total_coins": 0,
        "total_xp": 0,
        "total_boxes": 0,
        "box_counts": {"wood": 0, "silver": 0, "gold": 0, "rainbow": 0},
        "ticket_counts": {"rainbow": 0, "gold": 0, "total": 0},
        "tickets_50_rounds": {"rainbow": 0, "gold": 0, "total": 0},
        "box_drop_rate": 0.0,
        "rounds_with_boxes": 0,
        "total_rounds": 0,
    }
    if not dev or not getattr(dev, "round_history", None):
        return default_summary

    history = dev.round_history[:50]
    total_rounds = len(history)
    total_coins = sum(int(r.get("coins", 0)) for r in history)
    total_xp = sum(int(r.get("xp", 0)) for r in history)

    box_counts = {"wood": 0, "silver": 0, "gold": 0, "rainbow": 0}
    rounds_with_boxes = 0
    total_boxes = 0

    t50_rainbow = 0
    t50_gold = 0

    for r in history:
        r_boxes = r.get("boxes", [])
        if r_boxes:
            rounds_with_boxes += 1
            for b in r_boxes:
                b_str = str(b).lower().strip()
                if "wood" in b_str or "bronze" in b_str:
                    box_counts["wood"] += 1
                elif "silver" in b_str:
                    box_counts["silver"] += 1
                elif "gold" in b_str:
                    box_counts["gold"] += 1
                elif "rainbow" in b_str:
                    box_counts["rainbow"] += 1
                total_boxes += 1

        r_tickets = r.get("tickets", {})
        if isinstance(r_tickets, dict):
            t50_rainbow += int(r_tickets.get("rainbow", 0))
            t50_gold += int(r_tickets.get("gold", 0))

    drop_rate = round((rounds_with_boxes / max(1, total_rounds)) * 100, 1)

    tc_rainbow = dev.ticket_counts.get("rainbow", 0) if hasattr(dev, "ticket_counts") else 0
    tc_gold = dev.ticket_counts.get("gold", 0) if hasattr(dev, "ticket_counts") else 0

    return {
        "total_coins": total_coins,
        "total_xp": total_xp,
        "total_boxes": total_boxes,
        "box_counts": box_counts,
        "ticket_counts": {
            "rainbow": tc_rainbow,
            "gold": tc_gold,
            "total": tc_rainbow + tc_gold
        },
        "tickets_50_rounds": {
            "rainbow": t50_rainbow,
            "gold": t50_gold,
            "total": t50_rainbow + t50_gold
        },
        "box_drop_rate": drop_rate,
        "rounds_with_boxes": rounds_with_boxes,
        "total_rounds": total_rounds,
    }


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
            "box_counts": {"rainbow": 0, "gold": 0, "silver": 0, "bronze": 0, "wood": 0},
            "ticket_counts": {"rainbow": 0, "gold": 0},
            "coin_stats": {
                "session_earned": 0,
                "session_xp_earned": 0,
                "coins_per_hour": 0,
                "last_round": 0,
                "last_round_xp": 0,
            },
            "round_history": [],
            "fifty_rounds_summary": calculate_fifty_summary(None),
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
    stagger_enabled = bool(dev.settings.get("stagger_mode", False)) and (partner is not None)

    is_dev_running = (dev.status == "RUNNING") and not getattr(dev, "is_user_stopped", False)
    coins_per_hr = int(dev.session_coins / max(0.001, uptime_sec / 3600)) if (is_dev_running and uptime_sec > 5) else 0

    if stagger_enabled and partner:
        if "5554" in str(dev.device_id) or "01" in str(dev.device_id):
            s1, s2 = dev, partner
        elif "5554" in str(partner.device_id) or "01" in str(partner.device_id):
            s1, s2 = partner, dev
        else:
            s1 = dev if str(dev.device_id) <= str(partner.device_id) else partner
            s2 = partner if str(dev.device_id) <= str(partner.device_id) else dev

        s1_running = (s1.status == "RUNNING" and not getattr(s1, "is_user_stopped", False))
        s2_running = (s2.status == "RUNNING" and not getattr(s2, "is_user_stopped", False))

        s1_rounds = s1.rounds_played
        s2_rounds = s2.rounds_played
        comb_rounds = s1_rounds + s2_rounds

        s1_boxes = sum(s1.box_counts.values())
        s2_boxes = sum(s2.box_counts.values())
        comb_boxes = s1_boxes + s2_boxes

        s1_coins = s1.session_coins
        s2_coins = s2.session_coins
        comb_coins = s1_coins + s2_coins

        s1_xp = s1.session_xp
        s2_xp = s2.session_xp
        comb_xp = s1_xp + s2_xp

        s1_cph = int(s1_coins / max(0.001, uptime_sec / 3600)) if (s1_running and uptime_sec > 5) else 0
        s2_cph = int(s2_coins / max(0.001, uptime_sec / 3600)) if (s2_running and uptime_sec > 5) else 0
        comb_cph = s1_cph + s2_cph

        box_wood = s1.box_counts.get("wood", 0) + s2.box_counts.get("wood", 0)
        box_silver = s1.box_counts.get("silver", 0) + s2.box_counts.get("silver", 0)
        box_gold = s1.box_counts.get("gold", 0) + s2.box_counts.get("gold", 0)
        box_rainbow = s1.box_counts.get("rainbow", 0) + s2.box_counts.get("rainbow", 0)

        t_rainbow = s1.ticket_counts.get("rainbow", 0) + s2.ticket_counts.get("rainbow", 0)
        t_gold = s1.ticket_counts.get("gold", 0) + s2.ticket_counts.get("gold", 0)
        t_total = t_rainbow + t_gold

        combined_history = []
        for r in list(s1.round_history):
            rc = r.copy()
            rc["device_id"] = s1.device_id
            rc["device_label"] = "จอ 1"
            combined_history.append(rc)
        for r in list(s2.round_history):
            rc = r.copy()
            rc["device_id"] = s2.device_id
            rc["device_label"] = "จอ 2"
            combined_history.append(rc)

        combined_history.sort(key=lambda x: (x.get("time", ""), x.get("round", 0)), reverse=True)
        combined_history = combined_history[:50]

        tot_r = len(combined_history)
        tot_c = sum(r.get("coins", 0) for r in combined_history)
        tot_x = sum(r.get("xp", 0) for r in combined_history)
        c_wood = sum(1 for r in combined_history for b in r.get("boxes", []) if "wood" in str(b).lower() or "bronze" in str(b).lower())
        c_silver = sum(1 for r in combined_history for b in r.get("boxes", []) if "silver" in str(b).lower())
        c_gold = sum(1 for r in combined_history for b in r.get("boxes", []) if "gold" in str(b).lower())
        c_rainbow = sum(1 for r in combined_history for b in r.get("boxes", []) if "rainbow" in str(b).lower())
        c_r_boxes = sum(1 for r in combined_history if r.get("boxes"))
        c_t_rainbow = sum(r.get("tickets", {}).get("rainbow", 0) if isinstance(r.get("tickets"), dict) else 0 for r in combined_history)
        c_t_gold = sum(r.get("tickets", {}).get("gold", 0) if isinstance(r.get("tickets"), dict) else 0 for r in combined_history)
        c_tot_boxes = c_wood + c_silver + c_gold + c_rainbow
        drop_rate = round((c_r_boxes / max(1, tot_r)) * 100, 1) if tot_r > 0 else 0.0

        comb_fifty_summary = {
            "total_rounds": tot_r,
            "total_coins": tot_c,
            "total_xp": tot_x,
            "total_boxes": c_tot_boxes,
            "box_counts": {"wood": c_wood, "silver": c_silver, "gold": c_gold, "rainbow": c_rainbow, "total": c_tot_boxes},
            "rounds_with_boxes": c_r_boxes,
            "box_drop_rate": drop_rate,
            "tickets_50_rounds": {"rainbow": c_t_rainbow, "gold": c_t_gold, "total": c_t_rainbow + c_t_gold},
            "ticket_counts": {"rainbow": t_rainbow, "gold": t_gold, "total": t_total},
        }

        combined_stats = {
            "enabled": True,
            "partner_id": s2.device_id,
            "partner_running": s2_running,
            "partner_stage": "IDLE (Stopped)" if getattr(s2, "is_user_stopped", False) else s2.current_stage,
            "screen1_id": s1.device_id,
            "screen2_id": s2.device_id,
            "screen1_label": f"จอ 1 ({s1.device_id})",
            "screen2_label": f"จอ 2 ({s2.device_id})",
            "screen1_running": s1_running,
            "screen2_running": s2_running,
            "screen1_stage": "IDLE (Stopped)" if getattr(s1, "is_user_stopped", False) else s1.current_stage,
            "screen2_stage": "IDLE (Stopped)" if getattr(s2, "is_user_stopped", False) else s2.current_stage,
            "screen1_rounds": s1_rounds,
            "screen2_rounds": s2_rounds,
            "screen1_boxes": s1_boxes,
            "screen2_boxes": s2_boxes,
            "screen1_coins": s1_coins,
            "screen2_coins": s2_coins,
            "screen1_xp": s1_xp,
            "screen2_xp": s2_xp,
            "screen1_tickets": s1.ticket_counts,
            "screen2_tickets": s2.ticket_counts,
            "total_rounds": comb_rounds,
            "total_boxes": comb_boxes,
            "total_coins": comb_coins,
            "total_xp": comb_xp,
            "box_counts": {
                "wood": box_wood,
                "silver": box_silver,
                "gold": box_gold,
                "rainbow": box_rainbow,
                "total": box_wood + box_silver + box_gold + box_rainbow,
            },
            "ticket_counts": {
                "rainbow": t_rainbow,
                "gold": t_gold,
                "total": t_total,
            },
            "coins_per_hour": comb_cph,
            "breakdown": {
                "main": {
                    "id": s1.device_id,
                    "rounds": s1_rounds,
                    "boxes": s1_boxes,
                    "coins": s1_coins,
                    "xp": s1_xp,
                    "tickets": s1.ticket_counts,
                },
                "partner": {
                    "id": s2.device_id,
                    "rounds": s2_rounds,
                    "boxes": s2_boxes,
                    "coins": s2_coins,
                    "xp": s2_xp,
                    "tickets": s2.ticket_counts,
                }
            }
        }
        res_round_history = combined_history
        res_fifty_summary = comb_fifty_summary
    else:
        combined_stats = {
            "enabled": False,
            "partner_id": None,
            "partner_running": False,
            "partner_stage": "IDLE",
            "screen1_id": dev.device_id,
            "screen1_running": is_dev_running,
            "screen1_stage": "IDLE (Stopped)" if getattr(dev, "is_user_stopped", False) else dev.current_stage,
            "screen2_id": None,
            "screen2_running": False,
            "screen2_stage": "IDLE",
            "total_rounds": dev.rounds_played,
            "total_boxes": sum(dev.box_counts.values()),
            "total_coins": dev.session_coins,
            "total_xp": dev.session_xp,
            "box_counts": dev.box_counts,
            "ticket_counts": dev.ticket_counts,
            "coins_per_hour": coins_per_hr,
        }
        res_round_history = list(dev.round_history)
        res_fifty_summary = calculate_fifty_summary(dev)

    return {
        "device_id": dev.device_id,
        "is_running": is_dev_running,
        "status": "online",
        "online": True,
        "is_online": True,
        "current_stage": "IDLE (Stopped)" if getattr(dev, "is_user_stopped", False) else dev.current_stage,
        "speed_mode": "Normal",
        "stagger_mode": stagger_enabled,
        "stagger_partner_id": partner.device_id if partner else None,
        "combined_stats": combined_stats,
        "box_gap_seconds": float(dev.settings.get("box_gap_seconds") or getattr(coordinator, "room_settings", {}).get(dev.room_id, {}).get("box_gap_seconds", 6.0)),
        "box_pause_duration": float(dev.settings.get("box_pause_duration") or getattr(coordinator, "room_settings", {}).get(dev.room_id, {}).get("box_pause_duration", 5.0)),
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
        "round_history": res_round_history,
        "fifty_rounds_summary": res_fifty_summary,
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
        if dev.room_id:
            if not hasattr(coordinator, "room_settings"):
                coordinator.room_settings = {}
            if dev.room_id not in coordinator.room_settings:
                coordinator.room_settings[dev.room_id] = {}
            coordinator.room_settings[dev.room_id].update(settings)
            partner = coordinator.get_partner(dev.device_id)
            if partner:
                partner.settings.update(settings)
                if partner.ws:
                    try:
                        await partner.ws.send_json({"type": "SETTINGS_UPDATE", "settings": partner.settings})
                    except Exception:
                        pass
        if dev.ws:
            try:
                await dev.ws.send_json({"type": "SETTINGS_UPDATE", "settings": dev.settings})
            except Exception:
                pass
        return {"status": "ok", "settings": dev.settings}
    return {"status": "error", "message": "Device not found or empty settings"}


class PairRequest(BaseModel):
    partner_id: Optional[str] = None
    enabled: bool = True


@app.post("/api/instances/{device_id}/pair")
@app.post("/api/pair")
async def set_pair_endpoint(device_id: Optional[str] = None, req: Optional[PairRequest] = None):
    target_id = device_id if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.keys()), None)
    dev = coordinator.devices.get(target_id)
    if not dev:
        return {"status": "error", "message": "Device not found"}
    partner_id = req.partner_id if req else None
    enabled = req.enabled if req else True
    coordinator.set_pair(dev.device_id, partner_id, enabled)
    partner = coordinator.get_partner(dev.device_id)
    if dev.ws:
        try:
            await dev.ws.send_json({"type": "SETTINGS_UPDATE", "settings": dev.settings})
        except Exception:
            pass
    if partner and partner.ws:
        try:
            await partner.ws.send_json({"type": "SETTINGS_UPDATE", "settings": partner.settings})
        except Exception:
            pass
    return {
        "status": "ok",
        "device_id": dev.device_id,
        "partner_id": partner.device_id if partner else None,
        "stagger_mode": dev.settings.get("stagger_mode", False)
    }


@app.get("/api/pc-status")
@app.get("/api/heartbeat")
@app.get("/api/ping")
async def ping():
    return {"status": "ok", "time": time.time()}


@app.get("/api/status")
async def get_status():
    """Default status endpoint called by Web Dashboard."""
    coordinator.cleanup_stale_devices(max_stale_seconds=8.0)
    # Pick the first active device, or fallback
    first_dev = next(iter(coordinator.devices.values()), None)
    return format_status_dict(first_dev)


@app.get("/api/instances")
async def get_instances():
    """Returns all connected devices/instances for multi-instance selector (auto-purges stale)."""
    coordinator.cleanup_stale_devices(max_stale_seconds=8.0)
    instances = []
    for dev_id, dev in coordinator.devices.items():
        partner = coordinator.get_partner(dev_id)
        is_stopped = getattr(dev, "is_user_stopped", False)
        is_stagger = bool(dev.settings.get("stagger_mode", False)) and (partner is not None)
        instances.append({
            "id": dev_id,
            "device_id": dev_id,
            "name": dev.name,
            "rounds": dev.rounds_played,
            "is_running": (dev.status == "RUNNING") and not is_stopped,
            "current_stage": "IDLE (Stopped)" if is_stopped else dev.current_stage,
            "rounds_played": dev.rounds_played,
            "is_in_game": False if is_stopped else dev.is_in_game,
            "stagger_mode": is_stagger,
            "stagger_partner_id": partner.device_id if partner else None,
            "status": "IDLE" if is_stopped else dev.status
        })
    return {"instances": instances, "active_device_id": next(iter(coordinator.devices.keys()), "")}


@app.get("/api/instances/{device_id}/status")
async def get_instance_status(device_id: str):
    coordinator.cleanup_stale_devices(max_stale_seconds=8.0)
    dev = coordinator.devices.get(device_id)
    if dev is None and (device_id in ("default", "none", "emulator-5554") or len(coordinator.devices) == 1):
        dev = next(iter(coordinator.devices.values()), None)
    return format_status_dict(dev)


@app.get("/api/devices")
async def get_devices():
    coordinator.cleanup_stale_devices(max_stale_seconds=60.0)
    return [dev.to_dict() for dev in coordinator.devices.values()]


@app.post("/api/instances/clear-stale")
@app.post("/api/clear-stale")
async def clear_stale_endpoint():
    """Immediately purges any disconnected or stale devices."""
    removed = coordinator.cleanup_stale_devices(max_stale_seconds=5.0)
    return {"status": "ok", "removed": removed, "remaining": len(coordinator.devices)}


@app.post("/api/instances/clear-all")
@app.post("/api/clear-all")
async def clear_all_endpoint():
    """Forces all devices to be cleared from memory immediately for a clean slate."""
    count = coordinator.clear_all()
    return {"status": "ok", "cleared_count": count, "message": "All devices cleared."}


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


class TicketAdjustRequest(BaseModel):
    device_id: Optional[str] = None
    rainbow: int = 0
    gold: int = 0
    apply_all: bool = False


@app.post("/api/tickets/adjust")
@app.post("/api/instances/{device_id}/tickets")
async def adjust_tickets_endpoint(req: TicketAdjustRequest, device_id: Optional[str] = None):
    did = req.device_id or device_id or (next(iter(coordinator.devices.keys())) if coordinator.devices else "default")
    dev = coordinator.get_device(did)
    partner = coordinator.get_partner(did) if dev else None

    if req.apply_all or (req.rainbow == 0 and req.gold == 0):
        for d in coordinator.devices.values():
            d.ticket_counts["rainbow"] = max(0, req.rainbow)
            d.ticket_counts["gold"] = max(0, req.gold)
    else:
        if dev:
            dev.ticket_counts["rainbow"] = max(0, req.rainbow)
            dev.ticket_counts["gold"] = max(0, req.gold)

    return {"success": True, "message": "Tickets updated successfully"}


@app.post("/api/instances/{device_id}/reset-stats")
@app.post("/api/reset-stats")
async def reset_stats_endpoint(device_id: Optional[str] = None):
    did = device_id or (next(iter(coordinator.devices.keys())) if coordinator.devices else "default")
    dev = coordinator.get_device(did)
    partner = coordinator.get_partner(did) if dev else None

    def _reset_dev(d):
        d.rounds_played = 0
        d.session_coins = 0
        d.last_round_coins = 0
        d.session_xp = 0
        d.last_round_xp = 0
        d.box_counts = {"wood": 0, "silver": 0, "gold": 0, "rainbow": 0}
        d.ticket_counts = {"rainbow": 0, "gold": 0}
        d.round_history.clear()

    if dev:
        _reset_dev(dev)
        if partner:
            _reset_dev(partner)
    else:
        for d in coordinator.devices.values():
            _reset_dev(d)

    return {"success": True, "message": f"Stats reset successfully for {did}"}


PLACEHOLDER_PATH = os.path.join(BASE_DIR, "placeholder.jpg")
if os.path.exists(PLACEHOLDER_PATH):
    with open(PLACEHOLDER_PATH, "rb") as f:
        PLACEHOLDER_JPEG = f.read()
else:
    PLACEHOLDER_JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'


@app.get("/api/frame")
@app.get("/api/screenshot")
@app.get("/api/instances/{device_id}/frame")
@app.get("/api/instances/{device_id}/screenshot")
@app.get("/api/device/{device_id}/frame")
async def get_device_frame(device_id: Optional[str] = None):
    dev = coordinator.devices.get(device_id) if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.values()), None)
    content = dev.latest_frame_bytes if (dev and dev.latest_frame_bytes) else PLACEHOLDER_JPEG
    return Response(content=content, media_type="image/jpeg")


async def frame_stream_generator(device_id: Optional[str] = None):
    """Yields ultra-smooth, high-frequency MJPEG stream from latest frames received from Redfinger."""
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    last_sent_bytes = None
    last_ping = time.time()

    # Always yield at least 1 initial frame immediately so browser connects instantly without stalling
    dev = coordinator.devices.get(device_id) if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.values()), None)
    init_bytes = dev.latest_frame_bytes if (dev and dev.latest_frame_bytes) else PLACEHOLDER_JPEG
    yield boundary + init_bytes + b"\r\n"
    last_sent_bytes = init_bytes

    while True:
        dev = coordinator.devices.get(device_id) if (device_id and device_id not in ("default", "none")) else next(iter(coordinator.devices.values()), None)
        target = dev.latest_frame_bytes if (dev and dev.latest_frame_bytes) else PLACEHOLDER_JPEG

        if target is not last_sent_bytes:
            last_sent_bytes = target
            last_ping = time.time()
            yield boundary + target + b"\r\n"
        elif time.time() - last_ping > 1.5:
            last_ping = time.time()
            yield boundary + target + b"\r\n"

        await asyncio.sleep(0.04)


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
    # Inherit room settings if any
    if room_id in getattr(coordinator, "room_settings", {}):
        dev.settings.update(coordinator.room_settings[room_id])
    if dev.settings:
        await websocket.send_json({"type": "CONFIG", "settings": dev.settings})

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

            elif mtype == "BOX_LOOT":
                boxes = msg.get("boxes", [])
                tickets = msg.get("tickets", {})
                for b in boxes:
                    b_str = str(b).lower().strip()
                    if b_str in dev.box_counts:
                        dev.box_counts[b_str] += 1
                    elif "wood" in b_str:
                        dev.box_counts["wood"] = dev.box_counts.get("wood", 0) + 1
                        dev.box_counts["bronze"] = dev.box_counts.get("bronze", 0) + 1
                if isinstance(tickets, dict):
                    dev.ticket_counts["rainbow"] += int(tickets.get("rainbow", 0))
                    dev.ticket_counts["gold"] += int(tickets.get("gold", 0))
                print(f"🎁 [{device_id}] Mystery Box loot: Boxes={boxes}, Tickets={tickets}")

            elif mtype == "CAPTCHA_SOLVED":
                odd_cards = msg.get("odd_cards", [])
                print(f"🛡️ [{device_id}] Anti-Bot Captcha SOLVED successfully! Tapped cards: {odd_cards}")
                dev.current_stage = f"CAPTCHA แก้ไขสำเร็จ! (การ์ด {odd_cards})"

            elif mtype == "ROUND_COMPLETE":
                dev.is_in_game = False
                coins = int(msg.get("coins", 0))
                xp = int(msg.get("xp", 0))
                boxes = msg.get("boxes", [])
                tickets = msg.get("tickets", {})
                
                dev.last_round_coins = coins
                dev.last_round_xp = xp
                dev.session_coins += coins
                dev.session_xp += xp

                if boxes and not msg.get("box_loot_sent", False):
                    for b in boxes:
                        b_str = str(b).lower().strip()
                        if b_str in dev.box_counts:
                            dev.box_counts[b_str] += 1
                        elif "wood" in b_str or "bronze" in b_str:
                            dev.box_counts["wood"] = dev.box_counts.get("wood", 0) + 1
                        elif "silver" in b_str:
                            dev.box_counts["silver"] = dev.box_counts.get("silver", 0) + 1
                        elif "gold" in b_str:
                            dev.box_counts["gold"] = dev.box_counts.get("gold", 0) + 1
                        elif "rainbow" in b_str:
                            dev.box_counts["rainbow"] = dev.box_counts.get("rainbow", 0) + 1

                if isinstance(tickets, dict) and not msg.get("box_loot_sent", False):
                    dev.ticket_counts["rainbow"] += int(tickets.get("rainbow", 0))
                    dev.ticket_counts["gold"] += int(tickets.get("gold", 0))

                round_duration = round(time.time() - dev.in_run_start_time, 1) if dev.in_run_start_time > 0 else 0.0

                tickets_dict = {
                    "rainbow": int(tickets.get("rainbow", 0)) if isinstance(tickets, dict) else 0,
                    "gold": int(tickets.get("gold", 0)) if isinstance(tickets, dict) else 0,
                    "total": (int(tickets.get("rainbow", 0)) + int(tickets.get("gold", 0))) if isinstance(tickets, dict) else 0
                }

                round_entry = {
                    "round": dev.rounds_played,
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "duration": f"{round_duration}s",
                    "coins": coins,
                    "xp": xp,
                    "boxes": list(boxes),
                    "tickets": tickets_dict,
                    "has_box_screenshot": False,
                    "device_id": device_id,
                    "device_label": dev.name
                }
                dev.round_history.insert(0, round_entry)
                if len(dev.round_history) > 50:
                    dev.round_history = dev.round_history[:50]

                print(f"✅ [{device_id}] Round #{dev.rounds_played} complete! Coins: +{coins:,}, XP: +{xp:,}, Boxes: {boxes}, Tickets: {tickets_dict}")

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

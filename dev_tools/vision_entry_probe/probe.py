"""視野への進入を座標列から先読みし、既存snapshotの実視認で採点する独立試験。

入力は読み取り専用HTTP診断、または同じ形式のJSONL。予測は過去2点だけから作る。
出力は試験記録と集計で、本体の発話・移動・changes・記憶は変更しない。
時刻は収集プロセスの単調時計をmsにした値。ageを引いた観測時刻は推定値として扱う。
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import time
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class Config:
    """ms、block、degree単位。高さ1.8は初回の立位・平地試験の仮定。"""

    horizon_ms: float = 2500
    step_ms: float = 50
    response_ms: float = 1600
    max_gap_ms: float = 2000
    max_age_ms: float = 1500
    player_height: float = 1.8
    max_speed: float = 12
    max_turn_speed: float = 180

    def __post_init__(self):
        if not all(math.isfinite(x) and x > 0 for x in vars(self).values()):
            raise ValueError("試験条件は有限の正数が必要")
        if self.horizon_ms > 5000 or self.step_ms > self.horizon_ms:
            raise ValueError("予測は最大5秒、刻みは予測範囲以下")


def point(value):
    return tuple(float(value[k]) for k in ("x", "y", "z"))


def wrapped(angle):
    return (angle + 180) % 360 - 180


@dataclass(frozen=True)
class Frame:
    """contextと同じtickの姿勢・実視認。receivedとobservedの差は入力age。"""

    key: tuple
    tick: int
    revision: int
    received: float
    observed: float
    observed_lower: float
    observed_upper: float
    eye: tuple
    player: tuple
    yaw: float
    pitch: float
    visibility: str
    reason: str
    range: float
    horizontal_fov: float
    vertical_fov: float

    @classmethod
    def read(cls, row, config):
        sample = row["sample"]
        if sample.get("version") != 1 or sample.get("status") != "observed":
            return None
        context = sample["context"]
        key = (row["session_id"], row["session_instance"],
               *(context[k] for k in ("owner_id", "body_id", "epoch", "dimension")))
        if any(not isinstance(v, str) or not v for v in key):
            raise ValueError("系列識別子が欠けている")
        age = float(sample["age_ms"])
        received = float(row["received_mono_ms"])
        rtt = float(row.get("request_duration_ms", 0))
        vision = sample["vision"]
        body = sample["self_pose"]
        result = cls(key, int(sample["server_tick"]), int(sample["snapshot_revision"]),
                     received, received - age - rtt/2, received - age - rtt,
                     received - age, point(body["eye_position"]),
                     point(sample["player_pose"]["position"]), float(body["view_yaw"]),
                     float(body["view_pitch"]), vision["player_visibility"], vision["reason"],
                     float(vision["range_blocks"]), float(vision["horizontal_fov_degrees"]),
                     float(vision["vertical_fov_degrees"]))
        numbers = (age, rtt, received, result.tick, result.revision, *result.eye,
                   *result.player, result.yaw, result.pitch, result.range,
                   result.horizontal_fov, result.vertical_fov)
        if not all(math.isfinite(x) for x in numbers) or min(age, rtt) < 0:
            raise ValueError("不正な姿勢・時刻")
        if (result.tick < 0 or result.revision < 0 or abs(result.pitch) > 90
                or not 0 < result.range <= 64 or not 0 < result.horizontal_fov <= 360
                or not 0 < result.vertical_fov <= 180):
            raise ValueError("姿勢・視野の範囲が不正")
        if age + rtt > config.max_age_ms or result.visibility == "unavailable":
            return None
        valid = {("visible", "in_view"), ("not_visible", "outside_fov"),
                 ("not_visible", "out_of_range"), ("not_visible", "occluded")}
        if (result.visibility, result.reason) not in valid:
            raise ValueError("視認結果と理由が不整合")
        return result


def inside(eye, player, yaw, pitch, frame, height):
    """既存BodyVisionGeometryと同じ角度規約。遮蔽は予測せず、正解側の実rayを使う。"""
    for fraction in (.25, .55, .85):
        dx, dy, dz = (player[0] - eye[0],
                      player[1] + height * fraction - eye[1], player[2] - eye[2])
        if dx * dx + dy * dy + dz * dz > frame.range ** 2 + 1e-9:
            continue
        horizontal = math.hypot(dx, dz)
        target_yaw = yaw if horizontal < 1e-9 else math.degrees(math.atan2(-dx, dz))
        target_pitch = pitch if dx * dx + dy * dy + dz * dz < 1e-9 else -math.degrees(math.atan2(dy, horizontal))
        if (abs(wrapped(target_yaw - yaw)) <= frame.horizontal_fov / 2 + 1e-9
                and abs(target_pitch - pitch) <= frame.vertical_fov / 2 + 1e-9):
            return True
    return False


def forecast(previous, current, config):
    """過去2点の等速・等角速度だけで初回進入を予測。未来の正解やchangesは参照しない。"""
    dt = (current.observed - previous.observed) / 1000
    if dt <= 0 or dt * 1000 > config.max_gap_ms:
        return None
    pv = tuple((b - a) / dt for a, b in zip(previous.player, current.player))
    bv = tuple((b - a) / dt for a, b in zip(previous.eye, current.eye))
    yaw_rate = wrapped(current.yaw - previous.yaw) / dt
    pitch_rate = (current.pitch - previous.pitch) / dt
    if (max(math.sqrt(sum(x*x for x in v)) for v in (pv, bv)) > config.max_speed
            or max(abs(yaw_rate), abs(pitch_rate)) > config.max_turn_speed):
        return None
    for offset in range(0, int(config.horizon_ms / config.step_ms) + 1):
        ahead_ms = offset * config.step_ms
        elapsed = (current.received - current.observed + ahead_ms) / 1000
        eye = tuple(p + v * elapsed for p, v in zip(current.eye, bv))
        player = tuple(p + v * elapsed for p, v in zip(current.player, pv))
        yaw = current.yaw + yaw_rate * elapsed
        pitch = min(90, max(-90, current.pitch + pitch_rate * elapsed))
        if inside(eye, player, yaw, pitch, current, config.player_height):
            # 推定上すでに入っているときは、未来の進入を先読みできたと数えない。
            return current.received + ahead_ms if offset else None
    return None


def analyze(rows, config=Config()):
    """一つの不可視区間につき最大一予測。欠損・世代変更・記録末尾は不的中と区別する。

    採取できたvisible遷移の直前・直後を区間として残す。サンプル間の短い出入りは未評価。
    遅延欄は同一の固定生成時間を仮定した机上比較で、実発話の計測値ではない。
    """
    previous = None
    pending = None
    attempted = False
    predictions, entries, intervals = [], [], []
    evidence = set()
    counts = {"valid_frames": 0, "duplicate_frames": 0, "breaks": 0}
    last_received = -math.inf

    def close(status, reason):
        nonlocal pending
        if pending is not None:
            pending.update(status=status, outcome_reason=reason)
            pending = None

    for row in rows:
        if row.get("kind") == "metadata":
            continue
        evidence.add(row.get("evidence", "unknown"))
        received = float(row["received_mono_ms"])
        if not math.isfinite(received) or received <= last_received:
            raise ValueError("受信時刻が単調増加していない")
        last_received = received
        current = None if row.get("kind") == "gap" else Frame.read(row, config)
        if current is None:
            close("censored", "unavailable")
            previous, attempted = None, False
            counts["breaks"] += 1
            continue
        if previous and current.key == previous.key:
            if current.tick == previous.tick and current.revision == previous.revision:
                counts["duplicate_frames"] += 1
                continue
            invalid = (current.tick <= previous.tick or current.revision <= previous.revision
                       or not 0 < current.observed - previous.observed <= config.max_gap_ms)
        else:
            invalid = previous is not None
        if invalid:
            close("censored", "context_or_time_discontinuity")
            previous, attempted = None, False
            counts["breaks"] += 1
        counts["valid_frames"] += 1
        if previous is not None:
            intervals.append(current.observed - previous.observed)
            entered = previous.visibility == "not_visible" and current.visibility == "visible"
            if entered:
                entry = {"lower_ms": previous.observed_lower, "upper_ms": current.observed_upper,
                         "detected_ms": current.received,
                         "eligible": previous.reason in ("outside_fov", "out_of_range"),
                         "prediction_id": pending["id"] if pending else None}
                entries.append(entry)
                if pending:
                    start, end = pending["issued_ms"], pending["deadline_ms"]
                    low, high = entry["lower_ms"], entry["upper_ms"]
                    if low >= start and high <= end:
                        status = "hit"
                    elif high > start and low < end:
                        status = "uncertain"
                    else:
                        status = "miss"
                    pending["entry"] = entry
                    pending["eta_interval_error_ms"] = max(low - pending["eta_ms"], pending["eta_ms"] - high, 0)
                    lead = current.received - start
                    pending["detected_lead_ms"] = lead
                    pending["estimated_remaining_generation_ms"] = max(0, config.response_ms - lead)
                    pending["estimated_saved_ms"] = min(config.response_ms, max(0, lead))
                    close(status, "entry_interval")
                attempted = False
            if pending and current.observed_lower >= pending["deadline_ms"]:
                close("miss", "no_sampled_entry_within_horizon")
            if current.visibility == "not_visible" and current.reason == "occluded":
                close("miss", "occluded_after_prediction")
            if (previous.visibility == "not_visible" and current.visibility == "not_visible"
                    and current.reason in ("outside_fov", "out_of_range") and not attempted):
                eta = forecast(previous, current, config)
                if eta is not None:
                    pending = {"id": len(predictions) + 1, "issued_ms": current.received,
                               "eta_ms": eta, "deadline_ms": current.received + config.horizon_ms,
                               "server_tick": current.tick, "status": "pending"}
                    predictions.append(pending)
                    attempted = True
            if current.visibility == "visible":
                attempted = False
        previous = current
    close("censored", "recording_ended")
    if len(evidence) > 1:
        raise ValueError("実測と合成など異なる証拠を同じ集計へ混ぜない")
    outcomes = {key: sum(p["status"] == key for p in predictions)
                for key in ("hit", "miss", "uncertain", "censored")}
    measured = outcomes["hit"] + outcomes["miss"] + outcomes["uncertain"]
    eligible = sum(e["eligible"] for e in entries)
    hit_entries = sum(p["status"] == "hit" and p.get("entry", {}).get("eligible", False) for p in predictions)
    uncertain_entries = sum(p["status"] == "uncertain" and p.get("entry", {}).get("eligible", False) for p in predictions)
    successful = [p for p in predictions if p["status"] == "hit"]
    median = lambda values: statistics.median(values) if values else None
    return {
        "schema_version": 1, "evidence": next(iter(evidence), "empty"),
        "sampling_scope": "sampled_visibility_transitions_not_continuous_visibility",
        "conditions": vars(config), "counts": counts, "outcomes": outcomes,
        "eligible_entries": eligible, "all_entries": len(entries),
        "precision_interval": [outcomes["hit"]/measured, (outcomes["hit"]+outcomes["uncertain"])/measured] if measured else None,
        "recall_interval": [hit_entries/eligible, (hit_entries+uncertain_entries)/eligible] if eligible else None,
        "median_sample_interval_ms": median(intervals),
        "median_entry_bracket_ms": median([e["upper_ms"]-e["lower_ms"] for e in entries]),
        "latency_kind": "counterfactual_fixed_generation_only_not_playback",
        "baseline_generation_ms": config.response_ms,
        "median_estimated_saved_ms_on_confirmed_hits": median([p["estimated_saved_ms"] for p in successful]),
        "median_estimated_remaining_ms_on_confirmed_hits": median([p["estimated_remaining_generation_ms"] for p in successful]),
        "predictions": predictions, "entries": entries,
    }


def dump_line(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    handle.flush()


def record(args):
    """既存HTTPをGETし、選択sessionの診断欄だけを保存。失敗はgapとして残す。"""
    address = urllib.parse.urlparse(args.url)
    if address.scheme != "http" or address.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("収集先はローカルHTTPのみ")
    if args.duration <= 0 or args.interval < .05:
        raise ValueError("durationは正数、intervalは0.05秒以上")
    end = time.monotonic() + args.duration
    identity = None
    last = None
    saved = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        dump_line(handle, {"kind": "metadata", "schema_version": 1,
                           "started_utc": datetime.now(timezone.utc).isoformat(),
                           "evidence": "live_snapshot", "url": args.url,
                           "requested_session": args.session, "interval_seconds": args.interval})
        while time.monotonic() < end:
            started = time.monotonic()
            row = {"evidence": "live_snapshot"}
            try:
                with urllib.request.urlopen(args.url, timeout=2) as response:
                    payload = json.load(response)
                sessions = [s for s in payload["sessions"]
                            if (args.session is None or s["session_id"] == args.session)
                            and s.get("observation_mode") == "minecraft"]
                if len(sessions) != 1:
                    raise ValueError(f"Minecraft sessionが一意でない: {len(sessions)}")
                session = sessions[0]
                selected = (session["session_id"], session["session_instance"])
                if identity is not None and selected != identity:
                    raise ValueError("選択sessionが変わったため収集を止めて再選択が必要")
                identity = selected
                sample = session.get("body_probe")
                if sample is None:
                    raise ValueError("body_probeがない: 試験用診断を含む本体で起動が必要")
                row.update(kind="sample", session_id=selected[0],
                           session_instance=selected[1], sample=sample)
                marker = json.dumps([selected, sample.get("context"), sample.get("server_tick"),
                                     sample.get("snapshot_revision"), sample.get("status"), sample.get("reason")], sort_keys=True)
                if marker == last and sample.get("status") == "observed":
                    time.sleep(max(0, args.interval - (time.monotonic()-started)))
                    continue
                last = marker
                saved += sample.get("status") == "observed"
            except (OSError, ValueError, KeyError, TypeError) as error:
                row.update(kind="gap", reason=str(error)[:200])
                last = None
            row.update(received_mono_ms=time.monotonic()*1000,
                       received_utc=datetime.now(timezone.utc).isoformat(),
                       request_duration_ms=(time.monotonic()-started)*1000)
            dump_line(handle, row)
            time.sleep(max(0, args.interval-(time.monotonic()-started)))
    print(json.dumps({"saved_samples": saved, "output": str(args.output)}, ensure_ascii=False))
    return 0 if saved else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("record")
    capture.add_argument("--url", default="http://127.0.0.1:5055/api/v1/rust-dialogue/snapshot")
    capture.add_argument("--session")
    capture.add_argument("--duration", type=float, default=180)
    capture.add_argument("--interval", type=float, default=.1)
    capture.add_argument("--output", type=Path, required=True)
    score = commands.add_parser("analyze")
    score.add_argument("input", type=Path)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--response-ms", type=float, default=1600)
    score.add_argument("--horizon-ms", type=float, default=2500)
    args = parser.parse_args()
    if args.command == "record":
        return record(args)
    with args.input.open() as handle:
        result = analyze((json.loads(line) for line in handle if line.strip()),
                         Config(response_ms=args.response_ms, horizon_ms=args.horizon_ms))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({k: v for k, v in result.items() if k not in ("predictions", "entries")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

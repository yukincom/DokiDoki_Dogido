"""8×6の各マス内の局所差と明るさを測り、閾値を超えたマスだけ囲む試験用処理。原画像から計測し、保存済みモデル画像への枠だけを変更する。物体・出来事の種類は判定しない。"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

CONFIG = {
    "columns": 8, "rows": 6, "samples_per_cell_side": 8,
    "local_top_sample_count": 4, "local_top_mean_threshold": 60.0,
    "local_connected_sample_threshold": 45.0, "local_min_connected_samples": 3,
    "brightness_median_threshold": 18.0, "brightness_support_threshold": 12.0,
    "brightness_min_support_samples": 40,
    "outline_rgb": [255, 128, 0], "outline_width": 1,
}


def samples(path, config):
    """マスごとに8×8の小区画へ平均化する。マス全体の平均で小物を消さず、原画像の同じ座標同士を比較する。位置合わせや撮影画像の書換えはしない。"""
    cols, rows, side = (config[k] for k in ["columns", "rows", "samples_per_cell_side"])
    with Image.open(path) as image:
        pixels = np.asarray(image.convert("RGB").resize((cols * side, rows * side), Image.Resampling.BOX), dtype=np.float32)
    return pixels.reshape(rows, side, cols, side, 3).transpose(0, 2, 1, 3, 4)


def largest_component(mask):
    """孤立した強い1点を拾わないため、4近傍でつながる小区画数を調べる。画像の物体領域とはみなさない。"""
    visited = np.zeros_like(mask, dtype=bool); largest = 0
    for y, x in np.argwhere(mask):
        if visited[y, x]:
            continue
        queue = deque([(int(y), int(x))]); visited[y, x] = True; count = 0
        while queue:
            cy, cx = queue.popleft(); count += 1
            for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True; queue.append((ny, nx))
        largest = max(largest, count)
    return largest


def measure(before, after, config=CONFIG):
    """各マスの一様なRGB差を引いた残差の上位4点平均と連結数で局所変化を判定。明るさは引く前の符号付き輝度差の中央値と支持点数で別判定する。閾値は0〜255の強度単位。"""
    with Image.open(before) as a, Image.open(after) as b:
        if a.width * b.height != b.width * a.height:
            raise ValueError("画像の縦横比が異なる")
    delta = samples(after, config) - samples(before, config)
    offset = np.median(delta, axis=(2, 3))
    residual = np.abs(delta - offset[:, :, None, None, :]).mean(axis=-1)
    luma_delta = delta @ np.asarray([0.2126, 0.7152, 0.0722])
    cells = []
    for y in range(config["rows"]):
        for x in range(config["columns"]):
            values = residual[y, x]
            top = float(np.sort(values.ravel())[-config["local_top_sample_count"]:].mean())
            connected = largest_component(values >= config["local_connected_sample_threshold"])
            local = top >= config["local_top_mean_threshold"] and connected >= config["local_min_connected_samples"]
            median = float(np.median(luma_delta[y, x]))
            direction = 1 if median > 0 else -1 if median < 0 else 0
            support = int(np.count_nonzero(luma_delta[y, x] * direction >= config["brightness_support_threshold"]))
            brightness = abs(median) >= config["brightness_median_threshold"] and support >= config["brightness_min_support_samples"]
            cells.append({"column": x + 1, "row": y + 1, "local_top_mean": round(top, 4),
                "local_connected_samples": connected, "luma_median_delta": round(median, 4),
                "brightness_support_samples": support, "local_change": bool(local), "brightness_change": bool(brightness),
                "brightness_direction": ("lighter" if direction > 0 else "darker") if brightness else "below_threshold",
                "changed": bool(local or brightness)})
    return {"cells": cells, "changed_cell_count": sum(cell["changed"] for cell in cells),
            "local_cell_count": sum(cell["local_change"] for cell in cells),
            "brightness_cell_count": sum(cell["brightness_change"] for cell in cells)}


def outline(model_image, target, detection, config=CONFIG):
    """実撮影のモデル画像へ、検出マスの1px枠だけを加える。未検出ならバイト同一で複写。枠以外の画素と解像度を保ち、数値・全マスの線は描かない。"""
    if not detection["changed_cell_count"]:
        Path(target).write_bytes(Path(model_image).read_bytes())
        return
    with Image.open(model_image) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    for cell in detection["cells"]:
        if not cell["changed"]:
            continue
        x, y = cell["column"] - 1, cell["row"] - 1
        box = (x * image.width // config["columns"], y * image.height // config["rows"],
               (x + 1) * image.width // config["columns"] - 1, (y + 1) * image.height // config["rows"] - 1)
        draw.rectangle(box, outline=tuple(config["outline_rgb"]), width=config["outline_width"])
    image.save(target)

"""固定済みの8×6判定を、位置の文と結合した外周枠へ変換する。検出・閾値・明るさの分類を変えず、物体名や出来事を補作しない。上下左右で接するマスだけを結合する。"""
from collections import deque

from PIL import Image, ImageDraw


def regions(detection):
    """検出マスの4近傍連結成分を返す。対角だけで接するマスは別領域。外接矩形で未検出マスを塗り込まず、元のマス集合をそのまま保持する。"""
    remaining = {(c["column"] - 1, c["row"] - 1) for c in detection["cells"] if c["changed"]}
    result = []
    while remaining:
        start = min(remaining, key=lambda p: (p[1], p[0])); remaining.remove(start)
        queue = deque([start]); component = []
        while queue:
            x, y = queue.popleft(); component.append((x, y))
            for point in [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]:
                if point in remaining:
                    remaining.remove(point); queue.append(point)
        result.append(sorted(component, key=lambda p: (p[1], p[0])))
    return result


def _span_text(numbers):
    """連続列だけを範囲にまとめ、離れた列は別記する。途中の未検出マスを変化した場所として説明しない。"""
    runs = []
    for number in sorted(numbers):
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    return "・".join(str(r[0]) if len(r) == 1 else f"{r[0]}〜{r[-1]}" for r in runs)


def location_text(component):
    """まず画面内の大まかな範囲、続いて上起点の行・左起点の列で正確な場所を示す。広い領域を中心一点の場所と取り違えないよう、またがる範囲も明記する。"""
    xs, ys = zip(*component)
    def band(values, cuts):
        return {0 if v < cuts[0] else 1 if v < cuts[1] else 2 for v in values}
    horizontal = band(xs, (3, 5)); vertical = band(ys, (2, 4))
    h = {frozenset({0}): "左側", frozenset({1}): "中央", frozenset({2}): "右側",
         frozenset({0, 1}): "左側から中央", frozenset({1, 2}): "中央から右側"}.get(frozenset(horizontal), "左右全体")
    v = {frozenset({0}): "上部", frozenset({1}): "中央部", frozenset({2}): "下部",
         frozenset({0, 1}): "上部から中央部", frozenset({1, 2}): "中央部から下部"}.get(frozenset(vertical), "上部から下部")
    by_row = {}
    for x, y in component:
        by_row.setdefault(y + 1, []).append(x + 1)
    precise = "、".join(f"上から{row}行目は左から{_span_text(cols)}列目" for row, cols in sorted(by_row.items()))
    return f"画面の{h}、{v}にある範囲。{precise}。"


def brightness_text(detection):
    """以前に確定した明るさ判定を文へ写す。符号が混在すれば両方を伝え、画像全体や世界の照明が一方向に変化したと決めつけない。"""
    directions = {c["brightness_direction"] for c in detection["cells"] if c["brightness_change"]}
    if not directions:
        return "画像の明るさの変化は検出していない。"
    if directions == {"lighter"}:
        return "画像の明るさに変化がある。明るくなった領域を検出した。"
    if directions == {"darker"}:
        return "画像の明るさに変化がある。暗くなった領域を検出した。"
    return "画像の明るさに変化がある。明るくなった領域と暗くなった領域をそれぞれ検出した。"


def outline_merged(model_image, target, detection, config):
    """検出マスの共有辺を消し、結合した領域の外周だけを描く。未検出マスに接する辺は残す。色・太さ・解像度は前回と同じで、枠の外の画素は変えない。"""
    with Image.open(model_image) as source:
        image = source.convert("RGB")
    changed = {(c["column"] - 1, c["row"] - 1) for c in detection["cells"] if c["changed"]}
    draw = ImageDraw.Draw(image)
    for x, y in sorted(changed):
        x0, x1 = x * image.width // config["columns"], (x + 1) * image.width // config["columns"] - 1
        y0, y1 = y * image.height // config["rows"], (y + 1) * image.height // config["rows"] - 1
        edges = [((x - 1, y), (x0, y0, x0, y1)), ((x + 1, y), (x1, y0, x1, y1)),
                 ((x, y - 1), (x0, y0, x1, y0)), ((x, y + 1), (x0, y1, x1, y1))]
        for neighbor, edge in edges:
            if neighbor not in changed:
                draw.line(edge, fill=tuple(config["outline_rgb"]), width=config["outline_width"])
    image.save(target)

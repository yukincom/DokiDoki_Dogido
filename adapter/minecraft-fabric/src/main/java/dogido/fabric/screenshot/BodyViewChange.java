package dogido.fabric.screenshot;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import java.awt.image.BufferedImage;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** 直前の撮影と水平距離が1ブロック未満なら同じ場所。場所は撮影座標、景色の変化は枠なし原画像で判定する。
 * 目線の上下による背景のずれは、景色を測る64×48の標本だけで補正する。モデルには撮影した原画像を渡す。
 */
final class BodyViewChange {
    enum Status { NOT_COMPARED, UNCHANGED, CHANGED }
    enum Brightness { NONE, LIGHTER, DARKER }
    record Reference(String captureId, Instant capturedAt, String sha256) {
        JsonObject json() {
            JsonObject value = new JsonObject();
            value.addProperty("capture_id", captureId); value.addProperty("captured_at", capturedAt.toString());
            value.addProperty("sha256", sha256); return value;
        }
    }
    record Sample(String context, double x, double y, double z, int width, int height, int[] pixels, Reference reference) {}
    // 数値は計測の診断用に留め、通信・モデル・HUDへ渡さない。
    record Cell(int row, int column, boolean localChange, Brightness brightness,
            float localTopMean, int localConnectedSamples, double lumaMedianDelta, int brightnessSupportSamples) {
        boolean changed() { return localChange || brightness != Brightness.NONE; }
        JsonObject json() {
            JsonObject value = new JsonObject();
            value.addProperty("row", row); value.addProperty("column", column);
            value.addProperty("local_change", localChange);
            value.addProperty("brightness", brightness.name().toLowerCase(java.util.Locale.ROOT));
            return value;
        }
    }
    record Result(Status status, Reference reference, List<Cell> cells, double verticalShiftRows) {
        JsonObject json() {
            JsonObject value = new JsonObject();
            value.addProperty("status", status.name().toLowerCase(java.util.Locale.ROOT));
            value.add("reference", reference == null ? JsonNull.INSTANCE : reference.json());
            return value;
        }
        JsonObject diagnosticJson() {
            JsonObject value = json();
            value.addProperty("vertical_shift_rows", verticalShiftRows);
            JsonArray changed = new JsonArray();
            for (Cell cell : cells) if (cell.changed()) changed.add(cell.json());
            value.add("cells", changed); return value;
        }
    }
    private Sample previous;
    Result observe(String context, double x, double y, double z, BufferedImage image, Reference reference) {
        Sample old = previous;
        Sample current = new Sample(context, x, y, z, image.getWidth(), image.getHeight(), BodyViewBox.samples(image), reference);
        previous = current;
        if (old == null || !old.context.equals(context) || !(Math.hypot(x - old.x, z - old.z) < 1.0)
                || (long)old.width * current.height != (long)current.width * old.height) {
            return new Result(Status.NOT_COMPARED, null, List.of(), 0);
        }
        double shift = backgroundShift(old.pixels, current.pixels, y - old.y);
        int[] aligned = shift == 0 ? old.pixels : shifted(old.pixels, shift);
        List<Cell> cells = measure(aligned, current.pixels);
        Status status = cells.stream().anyMatch(Cell::changed) ? Status.CHANGED : Status.UNCHANGED;
        return new Result(status, old.reference, cells, shift);
    }

    // 上昇時は背景が下へ、下降時は上へずれる。視差は奥行きで異なるので、Y差だけでずれ量を決めない。
    // 48行中最大8行、かつ実際のY差に応じた範囲だけを探索し、左右に広く共通するずれがなければ補正しない。
    private static double backgroundShift(int[] before, int[] after, double dy) {
        double limit = Math.min(8.0, Math.abs(dy) * 48);
        if (!(limit >= .25)) return 0;
        int margin = (int)Math.ceil(limit) + 1;
        double[] baseline = edgeErrors(before, after, 0, margin);
        double bestError = Arrays.stream(baseline).sum() * .85;
        double bestShift = 0;
        for (double magnitude = .25; magnitude <= limit; magnitude += .25) {
            double shift = Math.copySign(magnitude, dy);
            double[] errors = edgeErrors(before, after, shift, margin);
            int supportingBands = 0;
            for (int i = 0; i < errors.length; i++) {
                if (errors[i] < baseline[i] * .85) supportingBands++;
            }
            double error = Arrays.stream(errors).sum();
            if (supportingBands >= 2 && error < bestError) {
                bestError = error;
                bestShift = shift;
            }
        }
        return bestShift;
    }

    // 同じ共通領域で縦方向の輪郭を照合する。画面全体の明るさの差は位置合わせに使わず、後段でそのまま測る。
    private static double[] edgeErrors(int[] before, int[] after, double shift, int margin) {
        double[] bands = new double[4];
        for (int y = margin; y < 48 - margin; y += 2) {
            for (int x = 0; x < 64; x += 2) {
                for (int channel = 0; channel < 3; channel++) {
                    int i = (y * 64 + x) * 3 + channel;
                    double oldEdge = sample(before, x, y + 1 - shift, channel) - sample(before, x, y - shift, channel);
                    int newEdge = after[i + 64 * 3] - after[i];
                    bands[x / 16] += Math.min(48.0, Math.abs(oldEdge - newEdge));
                }
            }
        }
        return bands;
    }

    private static int[] shifted(int[] before, double shift) {
        int[] aligned = new int[before.length];
        for (int y = 0; y < 48; y++) for (int x = 0; x < 64; x++) for (int channel = 0; channel < 3; channel++) {
            aligned[(y * 64 + x) * 3 + channel] = (int)Math.round(sample(before, x, y - shift, channel));
        }
        return aligned;
    }

    // 新しく写った端は一番近い旧画像の縁と比べ、そこに現れた物を無条件には除外しない。
    private static double sample(int[] pixels, int x, double y, int channel) {
        double row = Math.clamp(y, 0.0, 47.0);
        int top = (int)row;
        int i = (top * 64 + x) * 3 + channel;
        int next = top == 47 ? i : i + 64 * 3;
        return pixels[i] + (row - top) * (pixels[next] - pixels[i]);
    }

    static List<Cell> measure(int[] before, int[] after) {
        if (before.length != 64 * 48 * 3 || after.length != before.length) throw new IllegalArgumentException("grid_sample_size");
        List<Cell> cells = new ArrayList<>(48);
        for (int row = 0; row < 6; row++) for (int column = 0; column < 8; column++) {
            float[][] delta = new float[3][64];
            for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) for (int c = 0; c < 3; c++) {
                int i = (((row * 8 + y) * 64) + column * 8 + x) * 3 + c;
                delta[c][y * 8 + x] = after[i] - before[i];
            }
            float[] offset = new float[3];
            for (int c = 0; c < 3; c++) {
                float[] sorted = delta[c].clone(); Arrays.sort(sorted); offset[c] = (sorted[31] + sorted[32]) / 2f;
            }
            float[] residual = new float[64]; double[] luma = new double[64]; boolean[] mask = new boolean[64];
            for (int i = 0; i < 64; i++) {
                residual[i] = (Math.abs(delta[0][i] - offset[0]) + Math.abs(delta[1][i] - offset[1]) + Math.abs(delta[2][i] - offset[2])) / 3f;
                mask[i] = residual[i] >= 45f;
                luma[i] = delta[0][i] * .2126 + delta[1][i] * .7152 + delta[2][i] * .0722;
            }
            Arrays.sort(residual);
            float top = (residual[60] + residual[61] + residual[62] + residual[63]) / 4f;
            int connected = largestComponent(mask);
            double[] sorted = luma.clone(); Arrays.sort(sorted);
            double median = (sorted[31] + sorted[32]) / 2;
            int direction = median > 0 ? 1 : median < 0 ? -1 : 0, support = 0;
            for (double value : luma) if (value * direction >= 12) support++;
            Brightness brightness = Math.abs(median) >= 18 && support >= 40
                ? direction > 0 ? Brightness.LIGHTER : Brightness.DARKER : Brightness.NONE;
            cells.add(new Cell(row + 1, column + 1, top >= 60f && connected >= 3, brightness, top, connected, median, support));
        }
        return List.copyOf(cells);
    }
    private static int largestComponent(boolean[] mask) {
        boolean[] seen = new boolean[64]; int largest = 0;
        for (int start = 0; start < 64; start++) {
            if (!mask[start] || seen[start]) continue;
            ArrayDeque<Integer> queue = new ArrayDeque<>(); queue.add(start); seen[start] = true; int count = 0;
            while (!queue.isEmpty()) {
                int i = queue.removeFirst(), x = i % 8, y = i / 8; count++;
                for (int next : new int[]{x > 0 ? i - 1 : -1, x < 7 ? i + 1 : -1, y > 0 ? i - 8 : -1, y < 7 ? i + 8 : -1}) {
                    if (next >= 0 && mask[next] && !seen[next]) { seen[next] = true; queue.addLast(next); }
                }
            }
            largest = Math.max(largest, count);
        }
        return largest;
    }
}

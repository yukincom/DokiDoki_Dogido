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

/** 同じ身体世代・地点・画面比率の4枚前と、枠なし原画像の8×6マスを比較する。
 * 未比較も無変化も画像を保持する。意味・対象名・新規発話の可否は計測しない。
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
    record Sample(String context, long translation, int width, int height, int[] pixels, Reference reference) {}
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
    record Result(Status status, Reference reference, List<Cell> cells) {
        JsonObject json() {
            JsonObject value = new JsonObject();
            value.addProperty("status", status.name().toLowerCase(java.util.Locale.ROOT));
            value.add("reference", reference == null ? JsonNull.INSTANCE : reference.json());
            JsonArray changed = new JsonArray();
            for (Cell cell : cells) if (cell.changed()) changed.add(cell.json());
            value.add("cells", changed); return value;
        }
    }
    private final ArrayDeque<Sample> history = new ArrayDeque<>();
    Result observe(String context, long translation, BufferedImage image, Reference reference) {
        Sample current = new Sample(context, translation, image.getWidth(), image.getHeight(), BodyViewBox.samples(image), reference);
        Sample old = history.size() == 4 ? history.getFirst() : null;
        boolean compared = old != null && history.stream().allMatch(s -> s.context.equals(context) && s.translation == translation)
            && (long)old.width * current.height == (long)current.width * old.height;
        List<Cell> cells = compared ? measure(old.pixels, current.pixels) : List.of();
        Status status = !compared ? Status.NOT_COMPARED : cells.stream().anyMatch(Cell::changed) ? Status.CHANGED : Status.UNCHANGED;
        history.addLast(current); while (history.size() > 4) history.removeFirst();
        return new Result(status, compared ? old.reference : null, cells);
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

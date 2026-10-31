package dogido.fabric.screenshot;

import com.google.gson.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import javax.imageio.ImageIO;

/** 見込み: 固定13組の画像を同じ縮小・判定へ通せばPython版の期待値を維持できる。
 * 合格: 原画像と縮小RGBのhash、全624マス・件数が固定goldenと一致。範囲: 本体JavaのBOX・比較のみ。GPU撮影・通信・モデル・発話は未確認。
 */
public final class BodyViewGoldenFixture {
    private static final String GOLDEN_SHA256 = "62296c18dd8310bb8695907b96210b6dfecab08d708cc73272ab2b75e7cad00d";
    // goldenは小数4桁へ丸め済み。強度の照合だけに丸め誤差を許容し、判定・整数・RGBは厳密比較する。
    private static final double ROUNDED_DIAGNOSTIC_TOLERANCE = 0.0001;

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static String sha(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private static int[] samples(Path root, JsonObject pair, String side) throws Exception {
        Path path = root.resolve(pair.get(side).getAsString());
        require(sha(Files.readAllBytes(path)).equals(pair.get(side + "_sha256").getAsString()), pair.get("id") + " " + side + " source hash");
        var image = ImageIO.read(path.toFile());
        require(image != null, "unreadable image: " + path);
        int[] rowMajor = BodyViewBox.samples(image);
        require(rowMajor.length == 64 * 48 * 3, "sample size");
        byte[] cellMajor = new byte[rowMajor.length];
        int index = 0;
        for (int row = 0; row < 6; row++) for (int column = 0; column < 8; column++)
            for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) for (int channel = 0; channel < 3; channel++) {
                int value = rowMajor[(((row * 8 + y) * 64) + column * 8 + x) * 3 + channel];
                require(value >= 0 && value <= 255, "RGB byte range");
                cellMajor[index++] = (byte)value;
            }
        var fixedRgb = pair.getAsJsonArray(side + "_samples_rgb");
        require(fixedRgb.size() == cellMajor.length, "golden RGB size");
        for (int i = 0; i < cellMajor.length; i++) {
            require(Byte.toUnsignedInt(cellMajor[i]) == fixedRgb.get(i).getAsInt(), pair.get("id") + " " + side + " RGB[" + i + "]");
        }
        require(sha(cellMajor).equals(pair.get(side + "_samples_sha256").getAsString()), pair.get("id") + " " + side + " resized RGB hash");
        return rowMajor;
    }

    private static void diagnostic(double actual, double expected, String label) {
        require(Double.isFinite(actual) && Math.abs(actual - expected) <= ROUNDED_DIAGNOSTIC_TOLERANCE,
            label + ": expected " + expected + ", actual " + actual);
    }

    // 見込み: 各固定実画像対は縮小RGBから局所差・明るさを再現できる。
    // 合格: 各対48マスの座標・判定・連結数・支持数・集計は完全一致、丸め済み強度のみ誤差0.0001以内。範囲: 本体BOX→measure。期待値を再生成せず、画像の意味は採点しない。
    private static void checkPair(Path root, JsonObject pair) throws Exception {
        String id = pair.get("id").getAsString();
        List<BodyViewChange.Cell> cells = BodyViewChange.measure(samples(root, pair, "before"), samples(root, pair, "after"));
        var expected = pair.getAsJsonObject("expected");
        var fixed = expected.getAsJsonArray("cells");
        require(cells.size() == 48 && fixed.size() == 48, id + " cell count");
        int changed = 0, local = 0, brightness = 0;
        Set<String> coordinates = new HashSet<>();
        for (int i = 0; i < 48; i++) {
            var actual = cells.get(i);
            var want = fixed.get(i).getAsJsonObject();
            String label = id + " row=" + actual.row() + " column=" + actual.column();
            require(actual.row() == i / 8 + 1 && actual.column() == i % 8 + 1, label + " complete grid order");
            require(coordinates.add(actual.row() + ":" + actual.column()), label + " duplicated cell");
            require(actual.row() == want.get("row").getAsInt() && actual.column() == want.get("column").getAsInt(), label + " golden coordinates");
            require(actual.localChange() == want.get("local_change").getAsBoolean(), label + " local change");
            require(actual.changed() == want.get("changed").getAsBoolean(), label + " changed");
            boolean bright = actual.brightness() != BodyViewChange.Brightness.NONE;
            require(bright == want.get("brightness_change").getAsBoolean(), label + " brightness change");
            String direction = switch (actual.brightness()) { case NONE -> "below_threshold"; case LIGHTER -> "lighter"; case DARKER -> "darker"; };
            require(direction.equals(want.get("brightness_direction").getAsString()), label + " brightness direction");
            require(actual.localConnectedSamples() == want.get("local_connected_samples").getAsInt(), label + " connected samples");
            require(actual.brightnessSupportSamples() == want.get("brightness_support_samples").getAsInt(), label + " brightness support");
            diagnostic(actual.localTopMean(), want.get("local_top_mean").getAsDouble(), label + " local top mean");
            diagnostic(actual.lumaMedianDelta(), want.get("luma_median_delta").getAsDouble(), label + " luma median");
            if (actual.changed()) changed++;
            if (actual.localChange()) local++;
            if (bright) brightness++;
        }
        require(changed == expected.get("changed_cell_count").getAsInt(), id + " changed total");
        require(local == expected.get("local_cell_count").getAsInt(), id + " local total");
        require(brightness == expected.get("brightness_cell_count").getAsInt(), id + " brightness total");
        var result = new JsonObject();
        result.addProperty("case_id", id); result.addProperty("status", "passed"); result.addProperty("cells", cells.size());
        result.addProperty("changed_cells", changed); result.addProperty("local_cells", local); result.addProperty("brightness_cells", brightness);
        System.out.println(result);
    }

    public static void main(String[] args) throws Exception {
        require(args.length == 1, "one golden-cells.json path required");
        Path golden = Path.of(args[0]).toAbsolutePath();
        byte[] data = Files.readAllBytes(golden);
        require(sha(data).equals(GOLDEN_SHA256), "fixed golden hash changed; do not regenerate expectations");
        var document = JsonParser.parseString(new String(data, java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();
        require(document.get("sample_layout").equals(JsonParser.parseString("[\"cell_row\",\"cell_column\",\"sample_y\",\"sample_x\",\"rgb\"]")), "golden sample layout");
        require(document.get("sample_shape").equals(JsonParser.parseString("[6,8,8,8,3]")), "golden sample shape");
        var cases = document.getAsJsonArray("cases");
        require(cases.size() == 13, "fixed 13 pairs required");
        Set<String> ids = new HashSet<>();
        for (var element : cases) {
            var pair = element.getAsJsonObject();
            require(ids.add(pair.get("id").getAsString()), "duplicate pair");
            checkPair(golden.getParent(), pair);
        }
        System.out.println("PASS: 13 pairs, 624 cells, 26 resized RGB hashes; golden unchanged");
    }
}

package dogido.fabric.screenshot;

import java.awt.image.BufferedImage;
import java.time.Instant;
import java.util.Set;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 見込み: 比較不能と無変化を分け、4枚前の画像を参照すれば変化の根拠を保てる。
 * 合格: 三状態・参照・局所差と明るさを期待値に照合。範囲: 合成画素を本体Javaへ通す。参照メタデータは固定し、描画・通信・発話は確認しない。
 */
class BodyViewChangeTest {
    private static final Instant START = Instant.parse("2026-10-29T00:00:00Z");

    private BufferedImage image(int rgb) {
        var image = new BufferedImage(64, 48, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < 48; y++) for (int x = 0; x < 64; x++) image.setRGB(x, y, rgb);
        return image;
    }

    private BodyViewChange.Reference reference(int n) {
        return new BodyViewChange.Reference("capture-" + n, START.plusSeconds(n), "%064x".formatted(n));
    }

    private BodyViewChange prepared(int rgb) {
        var changes = new BodyViewChange();
        for (int i = 0; i < 4; i++) changes.observe("body", 1, image(rgb), reference(i));
        return changes;
    }

    // 見込み: 初回4枚は根拠不足、5枚目以降は直前ではなく4枚前と比べる。
    // 合格: 最初はnot_compared/null/空、5枚目は初回参照でunchanged、6枚目は2枚目参照でchanged。範囲: 本体比較とJSON、GPU・モデルは対象外。
    @Test void comparesExactlyFourBeforeWithoutCallingUnknownChanged() {
        var changes = new BodyViewChange();
        int[] colors = {0x101010, 0x505050, 0x909090, 0xd0d0d0};
        for (int i = 0; i < colors.length; i++) {
            var result = changes.observe("body", 1, image(colors[i]), reference(i));
            assertEquals(BodyViewChange.Status.NOT_COMPARED, result.status());
            assertNull(result.reference()); assertTrue(result.cells().isEmpty());
            assertEquals("not_compared", result.json().get("status").getAsString());
            assertTrue(result.json().get("reference").isJsonNull());
        }
        var same = changes.observe("body", 1, image(colors[0]), reference(4));
        assertEquals(BodyViewChange.Status.UNCHANGED, same.status());
        assertEquals(reference(0), same.reference());
        assertEquals(reference(0).json(), same.json().getAsJsonObject("reference"));
        assertEquals(48, same.cells().size());
        assertTrue(same.cells().stream().noneMatch(BodyViewChange.Cell::changed));
        assertTrue(same.json().getAsJsonArray("cells").isEmpty());
        var changed = changes.observe("body", 1, image(0xffffff), reference(5));
        assertEquals(BodyViewChange.Status.CHANGED, changed.status());
        assertEquals(reference(1), changed.reference());
        assertEquals(reference(1).json(), changed.json().getAsJsonObject("reference"));
    }

    // 見込み: 一様な小さい色差は局所変化にも明るさ変化にも数えない。
    // 合格: 灰色+3でunchanged、全48マスがlocal=false/brightness=none、参照は残る。範囲: 本体Javaの合成画素比較。実画像の意味は判定しない。
    @Test void toleratesSmallUniformAnimation() {
        var result = prepared(0x808080).observe("body", 1, image(0x838383), reference(4));
        assertEquals(BodyViewChange.Status.UNCHANGED, result.status());
        assertEquals(reference(0), result.reference()); assertEquals(48, result.cells().size());
        assertTrue(result.cells().stream().allMatch(c -> !c.localChange() && c.brightness() == BodyViewChange.Brightness.NONE));
    }

    // 見込み: マス内の連結した強い小領域は、マス全体が明るくならなくても局所差になる。
    // 合格: 左上の2×2点だけで1行1列だけlocal=true、明るさnone。JSONは判定項目だけ。範囲: 本体比較・JSON。物体認識は対象外。
    @Test void retainsLocalChangeSeparatelyFromBrightnessWithoutSendingIntensities() {
        var pixels = image(0x808080);
        for (int y = 0; y < 2; y++) for (int x = 0; x < 2; x++) pixels.setRGB(x, y, 0xffffff);
        var result = prepared(0x808080).observe("body", 1, pixels, reference(4));
        assertEquals(BodyViewChange.Status.CHANGED, result.status());
        assertEquals(1, result.cells().stream().filter(BodyViewChange.Cell::changed).count());
        var cell = result.cells().get(0);
        assertEquals(1, cell.row()); assertEquals(1, cell.column());
        assertTrue(cell.localChange()); assertEquals(BodyViewChange.Brightness.NONE, cell.brightness());
        var wire = result.json();
        assertEquals(Set.of("status", "reference", "cells"), wire.keySet());
        assertEquals(1, wire.getAsJsonArray("cells").size());
        var sent = wire.getAsJsonArray("cells").get(0).getAsJsonObject();
        assertEquals(Set.of("row", "column", "local_change", "brightness"), sent.keySet());
        assertEquals("none", sent.get("brightness").getAsString());
        assertTrue(sent.get("local_change").getAsBoolean());
    }

    // 見込み: 一様な明暗は局所差から分離し、明るさ閾値18以上だけを報告する。
    // 合格: ±17はunchanged、±18は全48マスlighter/darker、localは全てfalse。範囲: 本体比較の閾値境界。実照明の原因や発話は確認しない。
    @Test void brightnessThresholdAndDirectionStaySeparateFromLocalChange() {
        for (int delta : new int[]{-18, -17, 17, 18}) {
            int grey = 128 + delta;
            var result = prepared(0x808080).observe("body", 1, image(grey * 0x010101), reference(4));
            var brightness = Math.abs(delta) < 18 ? BodyViewChange.Brightness.NONE
                : delta > 0 ? BodyViewChange.Brightness.LIGHTER : BodyViewChange.Brightness.DARKER;
            assertEquals(Math.abs(delta) < 18 ? BodyViewChange.Status.UNCHANGED : BodyViewChange.Status.CHANGED,
                result.status(), "delta=" + delta);
            assertEquals(48, result.cells().size());
            assertTrue(result.cells().stream().allMatch(c -> !c.localChange() && c.brightness() == brightness), "delta=" + delta);
        }
    }

    // 見込み: 身体世代・地点・画面比率が違う画像は、景色が変わったとの根拠にしない。
    // 合格: 各境界でnot_compared、reference=null、cells空。範囲: 本体比較へ境界値を模擬入力。画像保持と実移動はRust・実機側で別確認。
    @Test void contextMovementAndAspectChangesAreNotCompared() {
        for (int mode = 0; mode < 3; mode++) {
            var result = prepared(0).observe(mode == 0 ? "new-body" : "body", mode == 1 ? 2 : 1,
                mode == 2 ? new BufferedImage(64, 64, BufferedImage.TYPE_INT_RGB) : image(0), reference(4));
            assertEquals(BodyViewChange.Status.NOT_COMPARED, result.status(), "boundary=" + mode);
            assertNull(result.reference()); assertTrue(result.cells().isEmpty());
        }
    }

    // 見込み: 移動前の画像が履歴に残る間は比較せず、新地点の4枚が揃えば再開する。
    // 合格: 移動後4枚はnot_compared、5枚目は新地点の最初の参照でunchanged。範囲: 本体履歴の世代境界。ゲームの移動検出は模擬。
    @Test void comparisonResumesOnlyAfterFourFramesAtTheNewLocation() {
        var changes = prepared(0x808080);
        for (int i = 4; i < 8; i++) {
            var result = changes.observe("body", 2, image(0x808080), reference(i));
            assertEquals(BodyViewChange.Status.NOT_COMPARED, result.status()); assertNull(result.reference());
        }
        var result = changes.observe("body", 2, image(0x808080), reference(8));
        assertEquals(BodyViewChange.Status.UNCHANGED, result.status()); assertEquals(reference(4), result.reference());
    }
}

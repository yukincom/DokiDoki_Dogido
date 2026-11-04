package dogido.fabric.screenshot;

import java.awt.image.BufferedImage;
import java.time.Instant;
import java.util.Set;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 見込み: 比較不能と無変化を分け、直前撮影の画像を参照すれば変化の根拠を保てる。
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
        changes.observe("body", 0, 64, 0, image(rgb), reference(0));
        return changes;
    }

    // 見込み: 初回だけは根拠がなく、2枚目から実際の直前撮影を参照する。
    // 合格: 最初は未比較、2枚目は初回参照で無変化、3枚目は2枚目参照で変化。合成画像と本体JSONを確認する。
    @Test void comparesThePreviousCaptureStartingWithTheSecond() {
        var changes = new BodyViewChange();
        var first = changes.observe("body", 0, 64, 0, image(0), reference(0));
        assertEquals(BodyViewChange.Status.NOT_COMPARED, first.status());
        assertNull(first.reference()); assertTrue(first.cells().isEmpty());
        assertEquals("not_compared", first.json().get("status").getAsString());
        assertTrue(first.json().get("reference").isJsonNull());
        var same = changes.observe("body", 0, 64, 0, image(0), reference(1));
        assertEquals(BodyViewChange.Status.UNCHANGED, same.status());
        assertEquals(reference(0), same.reference());
        assertEquals(reference(0).json(), same.json().getAsJsonObject("reference"));
        assertEquals(48, same.cells().size());
        assertTrue(same.cells().stream().noneMatch(BodyViewChange.Cell::changed));
        assertFalse(same.json().has("cells"));
        var changed = changes.observe("body", 0, 64, 0, image(0xffffff), reference(2));
        assertEquals(BodyViewChange.Status.CHANGED, changed.status());
        assertEquals(reference(1), changed.reference());
        assertEquals(reference(1).json(), changed.json().getAsJsonObject("reference"));
    }

    // 見込み: 一様な小さい色差は局所変化にも明るさ変化にも数えない。
    // 合格: 灰色+3でunchanged、全48マスがlocal=false/brightness=none、参照は残る。範囲: 本体Javaの合成画素比較。実画像の意味は判定しない。
    @Test void toleratesSmallUniformAnimation() {
        var result = prepared(0x808080).observe("body", 0, 64, 0, image(0x838383), reference(4));
        assertEquals(BodyViewChange.Status.UNCHANGED, result.status());
        assertEquals(reference(0), result.reference()); assertEquals(48, result.cells().size());
        assertTrue(result.cells().stream().allMatch(c -> !c.localChange() && c.brightness() == BodyViewChange.Brightness.NONE));
    }

    // 見込み: マス内の連結した強い小領域は、マス全体が明るくならなくても局所差になる。
    // 合格: 左上の2×2点だけで1行1列だけlocal=true、明るさnone。通信は状態と参照だけ、セルは保存診断に保持。範囲: 本体比較・JSON。物体認識は対象外。
    @Test void retainsLocalChangeSeparatelyFromBrightnessWithoutSendingIntensities() {
        var pixels = image(0x808080);
        for (int y = 0; y < 2; y++) for (int x = 0; x < 2; x++) pixels.setRGB(x, y, 0xffffff);
        var result = prepared(0x808080).observe("body", 0, 64, 0, pixels, reference(4));
        assertEquals(BodyViewChange.Status.CHANGED, result.status());
        assertEquals(1, result.cells().stream().filter(BodyViewChange.Cell::changed).count());
        var cell = result.cells().get(0);
        assertEquals(1, cell.row()); assertEquals(1, cell.column());
        assertTrue(cell.localChange()); assertEquals(BodyViewChange.Brightness.NONE, cell.brightness());
        var wire = result.json();
        assertEquals(Set.of("status", "reference"), wire.keySet());
        var diagnostics = result.diagnosticJson();
        assertEquals(1, diagnostics.getAsJsonArray("cells").size());
        var sent = diagnostics.getAsJsonArray("cells").get(0).getAsJsonObject();
        assertEquals(Set.of("row", "column", "local_change", "brightness"), sent.keySet());
        assertEquals("none", sent.get("brightness").getAsString());
        assertTrue(sent.get("local_change").getAsBoolean());
    }

    // 見込み: 一様な明暗は局所差から分離し、明るさ閾値18以上だけを報告する。
    // 合格: ±17はunchanged、±18は全48マスlighter/darker、localは全てfalse。範囲: 本体比較の閾値境界。実照明の原因や発話は確認しない。
    @Test void brightnessThresholdAndDirectionStaySeparateFromLocalChange() {
        for (int delta : new int[]{-18, -17, 17, 18}) {
            int grey = 128 + delta;
            var result = prepared(0x808080).observe("body", 0, 64, 0, image(grey * 0x010101), reference(4));
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
            var result = prepared(0).observe(mode == 0 ? "new-body" : "body", mode == 1 ? 1 : 0, 64, 0,
                mode == 2 ? new BufferedImage(64, 64, BufferedImage.TYPE_INT_RGB) : image(0), reference(4));
            assertEquals(BodyViewChange.Status.NOT_COMPARED, result.status(), "boundary=" + mode);
            assertNull(result.reference()); assertTrue(result.cells().isEmpty());
        }
    }

    // 見込み: 同じ場所の基準は直前からのXZ直線距離。各撮影の間で累積せず、上下だけなら比較を続ける。
    // 合格: .2/.9/1未満は比較し、ちょうど1以上や斜めの1以上は未比較、新地点でも次の撮影から再開。合成座標・画素。
    @Test void locationIsRelativeToThePreviousCaptureAndIgnoresHeight() {
        var changes = prepared(0);
        double[][] coords = {{.2,65,0}, {1.1,66,0}, {1.1,68,0}, {1.1,68,1}, {1.1,68,1.9}, {1.9,68,2.7}};
        boolean[] same = {true, true, true, false, true, false};
        for (int i = 0; i < coords.length; i++) {
            var pos = coords[i];
            var result = changes.observe("body", pos[0], pos[1], pos[2], image(0), reference(i+1));
            assertEquals(same[i] ? BodyViewChange.Status.UNCHANGED : BodyViewChange.Status.NOT_COMPARED, result.status());
            assertEquals(same[i] ? reference(i) : null, result.reference());
        }
        assertEquals(BodyViewChange.Status.UNCHANGED, prepared(0)
            .observe("body", Math.nextDown(1.0), 64, 0, image(0), reference(1)).status());
    }

    private BufferedImage background(int rowShift, int brightness) {
        var pixels = image(0);
        for (int y = 0; y < 48; y++) for (int x = 0; x < 64; x++) {
            int row = Math.clamp(y - rowShift, 0, 47);
            int gray = 36 + ((row*31 + x*7 + row*x) % 144) + brightness;
            pixels.setRGB(x, y, gray * 0x010101);
        }
        return pixels;
    }

    // 見込み: 上昇・下降と一致して同じ背景がずれた場合だけ、景色の差分から取り除く。
    // 合格: 上下両方向で元画像はunchanged、Y差がない場合は同じ画像ペアでもchanged。64×48の合成画像で検査。
    @Test void verticalViewpointShiftIsCompensatedOnlyWhenTheCameraActuallyMoved() {
        for (int sign : new int[]{1,-1}) {
            var before = background(0, 0);
            var after = background(sign*3, 0);
            for (boolean floating : new boolean[]{true,false}) {
                var changes = new BodyViewChange();
                changes.observe("body",0,64,0,before,reference(0));
                var result = changes.observe("body",0,64+(floating ? sign*.36 : 0),0,after,reference(1));
                assertEquals(floating ? BodyViewChange.Status.UNCHANGED : BodyViewChange.Status.CHANGED, result.status());
                assertEquals(floating ? sign*3 : 0, result.verticalShiftRows(), 0.0);
            }
        }
    }

    // 見込み: 共通背景を揃えても、新しい物体や照明変化は今回の景色に残る。
    // 合格: 中央・新しく見えた縁の白い物体はlocal=true、画面全体+24はlighter。合成画素と本体判定。
    @Test void alignmentKeepsNewObjectsAndChangesInLight() {
        for (int y : new int[]{0, 16, -1}) {
            var changes = new BodyViewChange();
            changes.observe("body",0,64,0,background(0,0),reference(0));
            var current = background(3, y < 0 ? 24 : 0);
            if (y >= 0) for (int row=y; row<y+3; row++) for (int x=24; x<27; x++) current.setRGB(x,row,0xffffff);
            var result = changes.observe("body",0,64.36,0,current,reference(1));
            assertEquals(3.0, result.verticalShiftRows(), 0.0);
            assertEquals(BodyViewChange.Status.CHANGED,result.status());
            if (y >= 0) assertTrue(result.cells().get(y/8*8+3).localChange());
            else assertTrue(result.cells().stream().allMatch(c -> c.brightness() == BodyViewChange.Brightness.LIGHTER));
        }
    }
}

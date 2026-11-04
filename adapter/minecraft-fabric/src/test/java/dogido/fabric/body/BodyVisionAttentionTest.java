package dogido.fabric.body;

import dogido.fabric.observation.NearbyComposition;
import dogido.fabric.observation.world.WorldObserver;
import dogido.lab.api.perception.BodyVisibility.Trace;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 視認と注意を分けることで周囲の消失を防ぎ、候補だけを絞れる見込み。
 * 実際の視野幾何・候補整形・外周追跡を固定した姿勢とray結果で検査する。ゲームとモデルの発言は別確認。 */
class BodyVisionAttentionTest {
    private static BodyOriginVision surroundings() { return new BodyOriginVision(10,Vec3d.ZERO,0,0); }
    private static boolean sees(BodyOriginVision vision,Vec3d point,double range) {
        return vision.visible(List.of(point),range,p -> Trace.CLEAR);
    }

    // 見込み: 注意の左右1〜2blockを外れても通常視野の対象は周囲に残る。
    // 合格: 追従/待機の両方で横4前6は視認し候補外、横0前4は両方に入る。実視野幾何を固定rayで検査。
    @Test void surroundingsStayWideWhileFollowAndHoldingOnlyNarrowCandidates() {
        var broad=surroundings();
        for (double openness : List.of(0.0,1.0)) {
            var focused=broad.forAttention(new BodyAttention.View(Vec3d.ZERO,0,openness,null));
            assertTrue(sees(broad,new Vec3d(4,0,6),NearbyComposition.MOB_ENTER));
            assertFalse(sees(focused,new Vec3d(4,0,6),NearbyComposition.MOB_ENTER));
            assertTrue(sees(focused,new Vec3d(0,0,4),NearbyComposition.MOB_ENTER));
            assertTrue(sees(broad,new Vec3d(4,0,6),NearbyComposition.MOB_ENTER));
        }
    }

    // 見込み: 一個体への集中も、別個体の周辺視認を消さない。
    // 合格: 同じ実測点で選択UUIDだけ候補に残り、他UUIDは周囲に残る。遮蔽結果を固定し実entity判定を通す。
    @Test void focusedEntityDoesNotEraseOtherVisibleEntities() {
        var broad=surroundings(); var selected=UUID.randomUUID(); var other=UUID.randomUUID();
        var target=new BodyInspectProtocol.Target("e:"+selected,"entity","minecraft:villager","村人",
            new BodyInspectProtocol.Point(0,0,4),selected);
        var focused=broad.forAttention(new BodyAttention.View(Vec3d.ZERO,0,1,target));
        var box=new Box(-.3,0,3.7,.3,1.8,4.3);
        broad.trace(new Vec3d(0,.45,4),null,() -> Trace.CLEAR);
        assertTrue(broad.entity(null,other,box,NearbyComposition.MOB_ENTER));
        assertFalse(focused.entity(null,other,box,NearbyComposition.MOB_ENTER));
        assertTrue(focused.entity(null,selected,box,NearbyComposition.MOB_ENTER));
        assertTrue(broad.entity(null,other,box,NearbyComposition.MOB_ENTER));
    }

    // 見込み: 広い範囲でも壁・未ロード・背後は視認せず、注意箱もその条件を省略しない。
    // 合格: CLEARだけ視認、BLOCKED/UNAVAILABLEは両方で不可。後方1blockの箱内もFOV外なら不可。
    @Test void bothViewsRequireFovAndActualLineOfSight() {
        var broad=surroundings(); var focused=broad.forAttention(new BodyAttention.View(Vec3d.ZERO,0,1,null));
        for (var view : List.of(broad,focused)) {
            for (var result : Trace.values())
                assertEquals(result==Trace.CLEAR,view.visible(List.of(new Vec3d(0,0,4)),16,p -> result));
            assertFalse(sees(view,new Vec3d(0,0,-1),16));
            assertFalse(sees(view,new Vec3d(0,5,4),16));
        }
    }

    // 見込み: 注意箱を外した周囲でも、既存の内側16/外側21による種類保持を使える。
    // 合格: 新規20は不可、16で視認後は21まで実視認し、21超過と遮蔽は不可。追跡器と視野を結合した模擬検査。
    @Test void mobRetentionMarginUsesTheWideViewOutsideAttention() {
        var broad=surroundings();
        var focused=broad.forAttention(new BodyAttention.View(Vec3d.ZERO,0,1,null));
        var tracker=new NearbyComposition.RangeTracker(NearbyComposition.MOB_ENTER,NearbyComposition.MOB_EXIT);
        assertFalse(tracker.allows("villager",20));
        assertTrue(tracker.allows("villager",16));
        assertTrue(sees(broad,new Vec3d(0,0,16),NearbyComposition.MOB_EXIT));
        tracker.finish(List.of("villager"));
        for (double distance : List.of(16.1,20.0,21.0)) {
            var point=new Vec3d(0,0,distance);
            assertTrue(tracker.allows("villager",distance));
            assertTrue(sees(broad,point,NearbyComposition.MOB_EXIT));
            assertFalse(sees(focused,point,NearbyComposition.MOB_EXIT));
        }
        assertFalse(tracker.allows("villager",21.001));
        assertFalse(sees(broad,new Vec3d(0,0,21.001),NearbyComposition.MOB_EXIT));
        assertFalse(broad.visible(List.of(new Vec3d(0,0,20)),NearbyComposition.MOB_EXIT,p -> Trace.BLOCKED));
        tracker.finish(List.of());
        assertFalse(tracker.allows("villager",20));
    }

    // 見込み: ブロックも広い取得箱と外周5blockまで見通せる。
    // 合格: 横8前8で発見し横13前13で既知だけ保持、横14は外。注意箱で外周を切らない模擬幾何検査。
    @Test void blockRetentionMarginIsOutsideTheWholeAcquisitionBox() {
        var broad=surroundings();
        var tracker=new NearbyComposition.RangeTracker(0,NearbyComposition.BLOCK_MARGIN);
        assertTrue(tracker.allows("stone",NearbyComposition.blockPadding(8,0,8)));
        assertTrue(sees(broad,new Vec3d(8,0,8),NearbyComposition.BLOCK_RAY_RANGE));
        assertFalse(tracker.allows("stone",NearbyComposition.blockPadding(13,0,13)));
        tracker.finish(List.of("stone"));
        assertTrue(tracker.allows("stone",NearbyComposition.blockPadding(13,0,13)));
        assertTrue(sees(broad,new Vec3d(13,0,13),NearbyComposition.BLOCK_RAY_RANGE));
        assertFalse(tracker.allows("stone",NearbyComposition.blockPadding(14,0,13)));
        assertFalse(sees(broad.forAttention(new BodyAttention.View(Vec3d.ZERO,0,1,null)),
            new Vec3d(13,0,13),NearbyComposition.BLOCK_RAY_RANGE));
    }

    // 見込み: 同種の近いブロックが注意外でも、注意内の別ブロックを選べる。
    // 合格: 注意外の原木を除き、注意内の近い原木一件だけを送る。視認済み一覧を固定し実候補整形を検査。
    @Test void blockKindsAreReducedOnlyAfterAttentionFiltering() {
        var outside=new WorldObserver.VisibleBlock(new BlockPos(3,0,2),"minecraft:oak_log","オークの原木");
        var inside=new WorldObserver.VisibleBlock(new BlockPos(0,0,4),"minecraft:oak_log","オークの原木");
        var farther=new WorldObserver.VisibleBlock(new BlockPos(0,0,5),"minecraft:oak_log","オークの原木");
        var focused=surroundings().forAttention(new BodyAttention.View(Vec3d.ZERO,0,1,null));
        var candidates=BodySensesCollector.candidates(List.of(),List.of(outside,inside,farther),Vec3d.ZERO,8,
            mob -> false,block -> sees(focused,Vec3d.ofCenter(block.position()),NearbyComposition.BLOCK_RAY_RANGE));
        assertEquals(1,candidates.size());
        assertEquals("b:0,0,4:minecraft:oak_log",candidates.get(0).getAsJsonObject().get("id").getAsString());
    }

    // 見込み: 候補確認は同じframeのrayを再利用し、別frameへは持ち越さない。
    // 合格: 周囲と注意で測定一回、別frameで再測定。UNAVAILABLE再利用も未取得数へ反映する。固定rayで予算経路を検査。
    @Test void attentionReusesSameFrameRaysWithoutHidingUnavailableSections() {
        var broad=surroundings(); var focused=broad.forAttention(new BodyAttention.View(Vec3d.ZERO,0,1,null));
        var point=new Vec3d(0,0,4); var calls=new AtomicInteger();
        assertEquals(Trace.CLEAR,broad.trace(point,null,() -> { calls.incrementAndGet(); return Trace.CLEAR; }));
        assertEquals(Trace.CLEAR,focused.trace(point,null,() -> { fail("same ray measured twice"); return Trace.BLOCKED; }));
        assertEquals(1,calls.get());
        assertEquals(Trace.BLOCKED,surroundings().trace(point,null,() -> { calls.incrementAndGet(); return Trace.BLOCKED; }));
        assertEquals(2,calls.get());
        var missing=new Vec3d(0,0,5);
        assertEquals(Trace.UNAVAILABLE,broad.trace(missing,null,() -> Trace.UNAVAILABLE));
        assertEquals(1,broad.missedRays());
        assertEquals(Trace.UNAVAILABLE,focused.trace(missing,null,() -> Trace.CLEAR));
        assertEquals(2,broad.missedRays());
        for (int i=2;i<BodyOriginVision.MAX_RAYS;i++) assertTrue(focused.takeRay());
        assertFalse(broad.takeRay());
    }
}

package dogido.fabric.observation.sound;

import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.observation.mobs.BossTracker;
import dogido.fabric.observation.mobs.ThreatObserver;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import net.minecraft.sound.SoundCategory;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import static org.junit.jupiter.api.Assertions.*;

/** 模擬の音と対象変更を受け、通常音／警戒音と発音時刻を送信直前まで検査する。Minecraftの実AI・音声は範囲外。 */
class SoundObserverTest {
    private static final Vec3d SOURCE = new Vec3d(0,64,2);
    private static final ObservationOrigin ORIGIN = new ObservationOrigin(UUID.randomUUID(), UUID.randomUUID(),
        new Vec3d(0,64,0), new Vec3d(0,65,0), 0, 0, null, null, null);

    @Test void sameSourceStepsBreakingAndPlacementRemainSeparateAndRepeatedStepsRefreshOnlySteps() {
        var tick = new AtomicLong(100);
        var observer = new SoundObserver(16, new MobClassifier(), LoggerFactory.getLogger("sound-test"), tick::get);
        String sourceId = "played:block:stone:0:32:1";
        for (String kind : new String[]{"step", "break", "place"}) {
            observer.recordAmbientSoundObservation(ORIGIN,"block.stone."+kind,"block:stone",SOURCE,sourceId,null);
        }
        tick.set(110);
        observer.recordAmbientSoundObservation(ORIGIN,"block.stone.step","block:stone",SOURCE,sourceId,null);
        var sounds = observer.scanAmbientSounds(ORIGIN,Map.of());
        assertEquals(3,sounds.size());
        assertEquals(3,sounds.stream().map(s -> s.soundEvent()).distinct().count());
        assertTrue(sounds.stream().allMatch(s -> sourceId.equals(s.sourceId())));
        tick.set(405);
        observer.expireSoundObservations();
        sounds = observer.scanAmbientSounds(ORIGIN,Map.of());
        assertEquals(1,sounds.size());
        assertEquals("block.stone.step",sounds.getFirst().soundEvent());
    }

    @Test void bodyServerClientPairDoesNotEraseOtherSoundKindsOrRenewEmissionAge() {
        var observer = new SoundObserver(16, new MobClassifier(), LoggerFactory.getLogger("sound-test"), () -> 100);
        observer.observeBodySound(ORIGIN,null,"block.stone.step",SoundCategory.BLOCKS,SOURCE,null,2000,true);
        observer.observeBodySound(ORIGIN,null,"block.stone.break",SoundCategory.BLOCKS,SOURCE,null,1900,true);
        observer.observeBodySound(ORIGIN,null,"block.stone.step",SoundCategory.BLOCKS,SOURCE,null,1800,false);
        var sounds = observer.scanAmbientSounds(ORIGIN,Map.of());
        assertEquals(2,sounds.size());
        assertTrue(sounds.stream().allMatch(s -> s.heardAgoMs() >= 1900));
    }
    @Test void verifiedPlayerSoundSurvivesWithoutCatalogLabelAndUnknownSourceDoesNotBorrowPlayer() {
        var observer=new SoundObserver(16,new MobClassifier(),LoggerFactory.getLogger("sound-test"),()->100);
        observer.observeBodySound(ORIGIN,null,"entity.player.attack.strong",SoundCategory.PLAYERS,SOURCE,ORIGIN.threatTargetId(),0,true);
        var sounds=observer.scanAmbientSounds(ORIGIN,Map.of());assertEquals(1,sounds.size());
        assertEquals(ORIGIN.threatTargetId().toString(),sounds.getFirst().sourceId());
        assertEquals("entity.player.attack.strong",sounds.getFirst().soundEvent());
        observer.observeBodySound(ORIGIN,null,"entity.player.attack.weak",SoundCategory.PLAYERS,SOURCE,null,0,true);
        assertEquals(1,observer.scanAmbientSounds(ORIGIN,Map.of()).size());
    }

    // 見込み: 一体だけが本人を狙うと、同種の別個体と発音者不明の足音は通常観測に残る。
    // 合格: 模擬ターゲット通知・身体音で、敵対音に狙ったUUIDだけ、通常音に別UUIDと座標音が各一件。
    @Test void onlyTheTargetingSkeletonsNewSoundEntersTheCombatTrack() {
        var classifier = new MobClassifier();
        var threats = tracker(classifier);
        var sounds = new SoundObserver(16, classifier, LoggerFactory.getLogger("sound-test"), () -> 100);
        UUID targeting = UUID.randomUUID(), other = UUID.randomUUID();
        threats.recordServerTarget(null, targeting, true);
        threats.applyServerObservations(null, ORIGIN.threatTargetId());
        for (UUID id : new UUID[]{targeting, other, null}) {
            sounds.observeBodySound(ORIGIN, null, "entity.skeleton.step", SoundCategory.HOSTILE, SOURCE, id, 0, true);
        }
        var hostile = sounds.scanAuditoryThreats(ORIGIN, Map.of());
        var ambient = sounds.scanAmbientSounds(ORIGIN, Map.of());
        assertEquals(1, hostile.size());
        assertEquals(targeting.toString(), hostile.getFirst().sourceId());
        assertEquals(2, ambient.size());
        assertTrue(ambient.stream().anyMatch(sound -> sound.sourceId().equals(other.toString())));
        assertTrue(ambient.stream().anyMatch(sound -> sound.sourceId().startsWith("pos:")));
        assertTrue(ambient.stream().allMatch(sound -> sound.soundEvent().equals("entity.skeleton.step")));
    }

    // 見込み: ターゲットを失った個体の以前の心音は通常音へ戻り、再交戦しても新しい発音までは警告にならない。
    // 合格: 模擬交戦で心音のUUID・時刻を保ち、敵対音と個体警告は解除。独立したスカルク作動は届く。
    @Test void losingTargetRetainsTheOriginalSoundAsAmbientAndRetiresItsOminousWarning() {
        var classifier = new MobClassifier();
        var threats = tracker(classifier);
        var sounds = new SoundObserver(16, classifier, LoggerFactory.getLogger("sound-test"), () -> 100);
        UUID warden = UUID.randomUUID();
        threats.recordServerTarget(null, warden, true);
        threats.applyServerObservations(null, ORIGIN.threatTargetId());
        sounds.observeBodySound(ORIGIN, null, "entity.warden.heartbeat", SoundCategory.HOSTILE, SOURCE, warden, 750, true);
        var initial = sounds.scanAuditoryThreats(ORIGIN, Map.of()).getFirst();
        assertEquals("warden_heartbeat", sounds.worldFacts().ominousKind());

        threats.recordServerTarget(null, warden, false);
        threats.applyServerObservations(null, ORIGIN.threatTargetId());
        assertTrue(sounds.scanAuditoryThreats(ORIGIN, Map.of()).isEmpty());
        var retained = sounds.scanAmbientSounds(ORIGIN, Map.of()).getFirst();
        assertEquals(initial.observedTick(), retained.observedTick());
        assertEquals(warden.toString(), retained.sourceId());
        assertEquals("entity.warden.heartbeat", retained.soundEvent());
        assertTrue(retained.heardAgoMs() >= initial.heardAgoMs());
        assertEquals("", sounds.worldFacts().ominousKind());

        threats.recordServerTarget(null, warden, true);
        threats.applyServerObservations(null, ORIGIN.threatTargetId());
        assertTrue(sounds.scanAuditoryThreats(ORIGIN, Map.of()).isEmpty());
        sounds.observeBodySound(ORIGIN, null, "block.sculk_shrieker.shriek", SoundCategory.BLOCKS, SOURCE, null, 0, true);
        assertEquals("sculk_shrieker", sounds.worldFacts().ominousKind());
    }

    // 見込み: 交戦前の足音を二つの経路で受けても、受信の間に始まった交戦へ前の音を付け替えない。
    // 合格: 模擬身体音で同一発音は通常音一件のまま、その後の新しい発音だけが警戒音になる。
    @Test void aSecondDeliveryDoesNotPromoteAnAmbientSoundButANewEmissionCanWarn() {
        var classifier = new MobClassifier();
        var threats = tracker(classifier);
        var sounds = new SoundObserver(16, classifier, LoggerFactory.getLogger("sound-test"), () -> 100);
        UUID skeleton = UUID.randomUUID();
        sounds.observeBodySound(ORIGIN, null, "entity.skeleton.step", SoundCategory.HOSTILE, SOURCE, skeleton, 500, true);
        threats.recordServerTarget(null, skeleton, true);
        threats.applyServerObservations(null, ORIGIN.threatTargetId());
        sounds.observeBodySound(ORIGIN, null, "entity.skeleton.step", SoundCategory.HOSTILE, SOURCE, null, 450, false);
        assertTrue(sounds.scanAuditoryThreats(ORIGIN, Map.of()).isEmpty());
        assertEquals(1, sounds.scanAmbientSounds(ORIGIN, Map.of()).size());

        sounds.observeBodySound(ORIGIN, null, "entity.skeleton.step", SoundCategory.HOSTILE, SOURCE, skeleton, 0, true);
        assertEquals(skeleton.toString(), sounds.scanAuditoryThreats(ORIGIN, Map.of()).getFirst().sourceId());
        assertTrue(sounds.scanAmbientSounds(ORIGIN, Map.of()).isEmpty());
    }

    private static ThreatObserver tracker(MobClassifier classifier) {
        return new ThreatObserver(new ThreatObserver.Settings(16, 30, 3, 2, 3, 3, 4),
            classifier, new BossTracker(), () -> {}, () -> 100);
    }

}

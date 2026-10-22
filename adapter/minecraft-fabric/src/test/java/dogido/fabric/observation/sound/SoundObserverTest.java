package dogido.fabric.observation.sound;

import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.mobs.MobClassifier;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import net.minecraft.sound.SoundCategory;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import static org.junit.jupiter.api.Assertions.*;

/** 同じ位置の別種の実音を、保存から送信用観測まで区別する回帰検査。 */
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

}

package dogido.fabric.observation.sound;

import dogido.fabric.observation.MobIdentityObservation;
import dogido.fabric.observation.NearbyComposition;
import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.mobs.MobClassifier;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import net.minecraft.sound.SoundCategory;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import static org.junit.jupiter.api.Assertions.*;

/** 見込み: 観測個体のUUIDへ実音を結び、既存の外周内の同種継続音にも引き継ぐ。
 * 合格: 既存SoundObserverから送信音までUUID・根拠・音齢を照合。候補と実音は模擬、Minecraft採取と発話は対象外。 */
class BodySoundSourceTest {
    private static final UUID OWNER = UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final UUID GOLEM = UUID.fromString("44444444-4444-4444-8444-444444444444");
    private static final ObservationOrigin ORIGIN = new ObservationOrigin(UUID.randomUUID(),OWNER,
        Vec3d.ZERO,new Vec3d(0,1,0),0,0,null,null,null);
    private static SoundObserver observer(AtomicLong tick) {
        return new SoundObserver(16,new MobClassifier(),LoggerFactory.getLogger("source-test"),tick::get);
    }
    private static SoundObserver.BodySource target(UUID id,String type,double x,boolean visible) {
        return new SoundObserver.BodySource(id,type,new Vec3d(x,0,0),
            new MobIdentityObservation(id.toString(),null,false),visible,NearbyComposition.MOB_EXIT);
    }
    private static void step(SoundObserver observer,String event,double x) {
        observer.observeBodySound(ORIGIN,null,event,SoundCategory.NEUTRAL,new Vec3d(x,0,0),null,0,true);
    }

    // 見込み: 見えている本人の床材足音とゴーレムの固有足音は、それぞれの観測個体に対応する。
    // 合格: 座標由来のIDでなく各UUIDを返し、別種類を誤帰属しない。模擬候補→実収集処理の検査。
    @Test void observedPlayerAndMobOwnTheirPositionedFootsteps() {
        var sounds=observer(new AtomicLong(100));
        sounds.updateBodySoundSources(ORIGIN,List.of(target(OWNER,"player",1,true),target(GOLEM,"iron_golem",5,true)));
        step(sounds,"block.grass.step",1.1);
        step(sounds,"entity.iron_golem.step",5.1);
        var heard=sounds.scanAmbientSounds(ORIGIN,Map.of());
        assertEquals(2,heard.size());
        assertTrue(heard.stream().anyMatch(s -> s.sourceId().equals(OWNER.toString()) && s.soundEvent().equals("block.grass.step")));
        assertTrue(heard.stream().anyMatch(s -> s.sourceId().equals(GOLEM.toString()) && s.soundEvent().equals("entity.iron_golem.step")));
        assertTrue(heard.stream().allMatch(s -> s.sourceContext().getAsJsonObject("observed_source").get("basis").getAsString().equals("observed")));
        step(sounds,"entity.villager.ambient",5.1);
        assertTrue(sounds.scanAmbientSounds(ORIGIN,Map.of()).stream().anyMatch(s -> s.sourceId().startsWith("pos:")));
    }

    // 見込み: 内側で紐付いた足音が外側へ移っても、同種音が続く間は既存21ブロックまで同じ対象になる。
    // 合格: 15→20.5で一件・同じUUID・continued、21超と未視認の再入域は未特定。新規外周個体も取得しない。
    @Test void continuousSoundRetainsItsObservedSourceOnlyInsideExistingOuterMargin() {
        var tick=new AtomicLong(100); var sounds=observer(tick);
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",15,true)));
        step(sounds,"entity.iron_golem.step",15);
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",20.5,false)));
        step(sounds,"entity.iron_golem.step",20.5);
        var heard=sounds.scanAmbientSounds(ORIGIN,Map.of());
        assertEquals(1,heard.size());
        assertEquals(GOLEM.toString(),heard.getFirst().sourceId());
        assertEquals("continued",heard.getFirst().sourceContext().getAsJsonObject("observed_source").get("basis").getAsString());
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",NearbyComposition.MOB_EXIT+0.1,false)));
        step(sounds,"entity.iron_golem.step",NearbyComposition.MOB_EXIT+0.1);
        assertTrue(sounds.scanAmbientSounds(ORIGIN,Map.of()).stream().anyMatch(s -> s.sourceId().startsWith("pos:")));
        tick.set(120);
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",20.5,false)));
        step(sounds,"entity.iron_golem.step",20.5);
        assertTrue(sounds.scanAmbientSounds(ORIGIN,Map.of()).stream()
            .filter(s -> s.sourceId().equals(GOLEM.toString())).allMatch(s -> s.observedTick() <= 100));
        var unseen=observer(new AtomicLong(100));
        unseen.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",20.5,false)));
        step(unseen,"entity.iron_golem.step",20.5);
        assertTrue(unseen.scanAmbientSounds(ORIGIN,Map.of()).getFirst().sourceId().startsWith("pos:"));
    }

    // 見込み: 同じ種類の別個体は発音位置で分かれ、位置も同じなら一方のIDを勝手に選ばない。
    // 合格: 近い個体のID・同距離時の未特定を確認。実機の位置精度は対象外。
    @Test void sameSpeciesUsesTheClosestObservedSourceWithoutBreakingTiesArbitrarily() {
        var other=UUID.randomUUID();
        var sounds=observer(new AtomicLong(100));
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",3,true),target(other,"iron_golem",5,true)));
        step(sounds,"entity.iron_golem.step",3.1);
        step(sounds,"entity.iron_golem.step",4.9);
        assertEquals(2,sounds.scanAmbientSounds(ORIGIN,Map.of()).stream().map(s -> s.sourceId()).distinct().count());
        step(sounds,"entity.iron_golem.step",4);
        assertTrue(sounds.scanAmbientSounds(ORIGIN,Map.of()).stream().anyMatch(s -> s.sourceId().startsWith("pos:")));
    }

    // 見込み: 同一発音のserver/client再配送で年齢を更新せず、既存期限・身体リセットで紐付けも切れる。
    // 合格: 二配送は一件かつ2秒以上前、期限後とreset後は未特定。音種変更だけで継続させない。
    @Test void pairingExpiryResetAndDifferentSoundKindKeepTheirExistingBoundaries() {
        var tick=new AtomicLong(100); var sounds=observer(tick);
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",3,true)));
        sounds.observeBodySound(ORIGIN,null,"entity.iron_golem.step",SoundCategory.NEUTRAL,new Vec3d(3,0,0),null,2000,true);
        sounds.observeBodySound(ORIGIN,null,"entity.iron_golem.step",SoundCategory.NEUTRAL,new Vec3d(3,0,0),null,1950,false);
        var heard=sounds.scanAmbientSounds(ORIGIN,Map.of());
        assertEquals(1,heard.size()); assertTrue(heard.getFirst().heardAgoMs()>=2000);
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",3,false)));
        step(sounds,"entity.iron_golem.hurt",3);
        assertTrue(sounds.scanAmbientSounds(ORIGIN,Map.of()).stream().anyMatch(s -> s.soundEvent().endsWith("hurt") && s.sourceId().startsWith("pos:")));
        tick.set(401); sounds.expireSoundObservations();
        step(sounds,"entity.iron_golem.step",3);
        assertTrue(sounds.scanAmbientSounds(ORIGIN,Map.of()).getFirst().sourceId().startsWith("pos:"));
        sounds.updateBodySoundSources(ORIGIN,List.of(target(GOLEM,"iron_golem",3,true)));
        sounds.resetRetainedSounds(); step(sounds,"entity.iron_golem.step",3);
        assertTrue(sounds.scanAmbientSounds(ORIGIN,Map.of()).getFirst().sourceId().startsWith("pos:"));
    }
}

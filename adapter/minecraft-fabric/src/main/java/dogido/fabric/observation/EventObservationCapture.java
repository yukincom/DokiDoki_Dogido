package dogido.fabric.observation;

import com.google.gson.JsonObject;
import dogido.fabric.observation.mobs.AmbientMobObservation;
import dogido.fabric.observation.mobs.AmbientMobObserver;
import dogido.fabric.observation.mobs.CombatObserver;
import dogido.fabric.observation.mobs.HostileOutcomeObservation;
import dogido.fabric.observation.mobs.ThreatObservation;
import dogido.fabric.observation.player.PlayerObserver;
import dogido.fabric.observation.smell.SmellObserver;
import dogido.fabric.observation.sound.AudioThreatObservation;
import dogido.fabric.observation.world.WorldObserver;
import dogido.fabric.protocol.EventJsonBuilder;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;

/**
 * 同じtickの元観測を共有し、イベント固有の種別・sequence・脅威・戦闘状態を組み立てる。
 * 取得frameは呼出し元のtickだけが所有し、送信と戦闘状態更新はrootが行う。
 */
public final class EventObservationCapture {
    private final PlayerObserver playerObserver;
    private final WorldObserver worldObserver;
    private final SmellObserver smellObserver;
    private final AmbientMobObserver ambientMobObserver;
    private final CombatObserver combatObserver;
    private final java.util.function.LongSupplier nextSequence;
    private final java.util.function.LongSupplier tickSource;
    private final java.util.function.Supplier<JsonObject> bodyObservation;
    private final java.util.function.Supplier<JsonObject> bodyAutonomy;

    public EventObservationCapture(PlayerObserver playerObserver, WorldObserver worldObserver,
            SmellObserver smellObserver, AmbientMobObserver ambientMobObserver, CombatObserver combatObserver,
            java.util.function.LongSupplier nextSequence, java.util.function.LongSupplier tickSource,
            java.util.function.Supplier<JsonObject> bodyObservation,
            java.util.function.Supplier<JsonObject> bodyAutonomy) {
        this.bodyAutonomy = bodyAutonomy;
        this.playerObserver = playerObserver;
        this.worldObserver = worldObserver;
        this.smellObserver = smellObserver;
        this.ambientMobObserver = ambientMobObserver;
        this.combatObserver = combatObserver;
        this.nextSequence = nextSequence;
        this.tickSource = tickSource;
        this.bodyObservation = bodyObservation;
    }

    /** 新しい周期の取得口を作るだけで、イベントがなければworld・所持品等は読まない。 */
    public WorldObservationFrame frame(ClientPlayerEntity player, ClientWorld world, boolean captureNearbyKinds) {
        long tick = this.tickSource.getAsLong();
        return new WorldObservationFrame(
            () -> this.worldObserver.buildWorld(player, world, tick),
            () -> this.worldObserver.buildNearbyBlocks(player, world, captureNearbyKinds),
            () -> {
                var owned = this.playerObserver.observe(player, world);
                return new WorldObservationFrame.Materials(owned.player(), owned.inventory(),
                    captureNearbyKinds ? this.ambientMobObserver.copyNearbyComposition() : null,
                    this.worldObserver.buildDroppedItems(player, world),
                    this.worldObserver.buildRecentBlockBreaks(tick), this.playerObserver.buildLookTarget(player, world));
            }, bounds -> world.getOtherEntities(null, bounds).stream().map(entity ->
                new WorldObservationFrame.EntitySample(MobIdentityObservation.entityTypeName(entity),
                    new net.minecraft.util.math.Vec3d(entity.getX(), entity.getY(), entity.getZ()), entity)).toList());
    }

    private EventJsonBuilder baseEnvelope(String name, String source, String priority, String certainty,
            WorldObservationFrame frame) {
        JsonObject blocks = frame.blocks();
        var materials = frame.materials();
        EventJsonBuilder root = new EventJsonBuilder(name, source, priority, certainty,
            OffsetDateTime.now().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME), this.smellObserver.current())
            .bodyObservation(this.bodyObservation.get()).bodyAutonomy(this.bodyAutonomy.get());
        root.sequence(this.nextSequence.getAsLong());
        root.player(materials.player());
        root.world(frame.copy());
        root.inventory(materials.inventory());
        root.nearbyMaterials(blocks, materials.nearbyMobs());
        root.droppedItems(materials.droppedItems());
        root.recentBlockBreaks(materials.recentBlockBreaks());
        root.lookTarget(materials.lookTarget());
        return root;
    }

    public JsonObject buildStatusSnapshot(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AudioThreatObservation> ambientSounds,
        List<AmbientMobObservation> ambientMobs
    ) {
        EventJsonBuilder root = baseEnvelope("status_snapshot", "system", "background", "high", worldFrame);
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.ambientSounds(ambientSounds);
        root.passiveMobs(ambientMobs);
        root.combat(this.combatObserver.buildCombat(player, world, worldFrame, threats, audioThreats));
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    public JsonObject buildThreatApproaching(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs
    ) {
        EventJsonBuilder root = baseEnvelope("threat_approaching", "visual", "urgent", "high", worldFrame);
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.combat(this.combatObserver.buildCombat(player, world, worldFrame, threats, audioThreats));
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    public JsonObject buildHostileAudioDetected(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs
    ) {
        EventJsonBuilder root = baseEnvelope("hostile_audio_detected", "auditory", "normal", "medium", worldFrame);
        root.visualThreats(List.of());
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.combat(this.combatObserver.buildCombat(player, world, worldFrame, threats, audioThreats));
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    public JsonObject buildAmbientMobDetected(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<AmbientMobObservation> ambientMobs
    ) {
        EventJsonBuilder root = baseEnvelope("ambient_mob_detected", "visual", "background", "high", worldFrame);
        root.visualThreats(List.of());
        root.auditoryThreats(List.of());
        root.passiveMobs(ambientMobs);
        root.combat(this.combatObserver.buildCombat(player, world, worldFrame, List.of(), List.of()));
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    public JsonObject buildPlayerDied(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs
    ) {
        EventJsonBuilder root = baseEnvelope("player_died", "system", "urgent", "high", worldFrame);
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.combat(this.combatObserver.buildCombat(player, world, worldFrame, threats, audioThreats));
        root.meta(EventJsonBuilder.buildMeta(this.playerObserver.resolveDeathCause(player)));
        return root.finish();
    }

    public JsonObject buildCreeperDetonated(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs,
        java.util.Collection<HostileOutcomeObservation> detonations
    ) {
        EventJsonBuilder root = baseEnvelope("creeper_detonated", "system", "urgent", "high", worldFrame);
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        JsonObject combat = this.combatObserver.buildCombat(player, world, worldFrame, threats, audioThreats);
        root.combatOutcomes(combat, detonations);
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    public JsonObject buildHostileDefeated(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs,
        java.util.Collection<HostileOutcomeObservation> outcomes
    ) {
        EventJsonBuilder root = baseEnvelope("hostile_defeated", "system", "urgent", "high", worldFrame);
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        JsonObject combat = this.combatObserver.buildCombat(player, world, worldFrame, threats, audioThreats);
        root.combatOutcomes(combat, outcomes);
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    public JsonObject buildCombatEnded(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<AmbientMobObservation> ambientMobs
    ) {
        EventJsonBuilder root = baseEnvelope("combat_ended", "system", "normal", "high", worldFrame);
        root.visualThreats(List.of());
        root.auditoryThreats(List.of());
        root.passiveMobs(ambientMobs);
        root.combat(this.combatObserver.buildCombat(player, world, worldFrame, List.of(), List.of()));
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

}

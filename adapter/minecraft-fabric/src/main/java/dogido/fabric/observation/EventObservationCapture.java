package dogido.fabric.observation;

import com.google.gson.JsonObject;
import dogido.fabric.observation.WorldObservationFrame;
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

    private EventJsonBuilder baseEnvelope(String name, String source, String priority, String certainty) {
        return new EventJsonBuilder(name, source, priority, certainty,
            OffsetDateTime.now().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME), this.smellObserver.current()).bodyObservation(this.bodyObservation.get()).bodyAutonomy(this.bodyAutonomy.get());
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
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("status_snapshot", "system", "background", "high");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.ambientSounds(ambientSounds);
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        root.combat(this.combatObserver.buildCombat(player, world, threats, audioThreats));
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
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("threat_approaching", "visual", "urgent", "high");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        root.combat(this.combatObserver.buildCombat(player, world, threats, audioThreats));
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
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("hostile_audio_detected", "auditory", "normal", "medium");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(List.of());
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        root.combat(this.combatObserver.buildCombat(player, world, threats, audioThreats));
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    public JsonObject buildAmbientMobDetected(
        ClientPlayerEntity player,
        ClientWorld world,
        WorldObservationFrame worldFrame,
        List<AmbientMobObservation> ambientMobs
    ) {
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("ambient_mob_detected", "visual", "background", "high");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(List.of());
        root.auditoryThreats(List.of());
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        root.combat(this.combatObserver.buildCombat(player, world, List.of(), List.of()));
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
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("player_died", "system", "urgent", "high");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        root.combat(this.combatObserver.buildCombat(player, world, threats, audioThreats));
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
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("creeper_detonated", "system", "urgent", "high");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        JsonObject combat = this.combatObserver.buildCombat(player, world, threats, audioThreats);
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
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("hostile_defeated", "system", "urgent", "high");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(threats);
        root.auditoryThreats(audioThreats);
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        JsonObject combat = this.combatObserver.buildCombat(player, world, threats, audioThreats);
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
        JsonObject nearbyResources = worldFrame.blocks();
        EventJsonBuilder root = baseEnvelope("combat_ended", "system", "normal", "high");
        root.sequence(this.nextSequence.getAsLong());
        root.player(this.playerObserver.buildPlayer(player, world));
        root.world(worldFrame.copy());
        root.visualThreats(List.of());
        root.auditoryThreats(List.of());
        root.passiveMobs(ambientMobs);
        root.inventory(this.playerObserver.buildInventory(player));
        attachNearbyMaterials(root, player, world, nearbyResources);
        attachLookTarget(root, player, world);
        root.combat(this.combatObserver.buildCombat(player, world, List.of(), List.of()));
        root.meta(EventJsonBuilder.buildMeta(null));
        return root.finish();
    }

    private void attachNearbyMaterials(EventJsonBuilder root, ClientPlayerEntity player,
            ClientWorld world, JsonObject nearbyResources) {
        root.nearbyMaterials(nearbyResources, nearbyResources.has("types")
            ? this.ambientMobObserver.copyNearbyComposition() : null);
        root.droppedItems(this.worldObserver.buildDroppedItems(player, world));
        root.recentBlockBreaks(this.worldObserver.buildRecentBlockBreaks(this.tickSource.getAsLong()));
    }

    private void attachLookTarget(EventJsonBuilder root, ClientPlayerEntity player, ClientWorld world) {
        root.lookTarget(this.playerObserver.buildLookTarget(player, world));
    }
}

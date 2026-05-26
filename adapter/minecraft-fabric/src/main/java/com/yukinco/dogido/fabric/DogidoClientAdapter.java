package com.yukinco.dogido.fabric;

import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.mob.HostileEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.network.packet.s2c.play.PlaySoundFromEntityS2CPacket;
import net.minecraft.network.packet.s2c.play.PlaySoundS2CPacket;
import net.minecraft.registry.Registries;
import net.minecraft.sound.SoundCategory;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;

public final class DogidoClientAdapter implements ClientModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("dogido-client-adapter");
    private static final String SCHEMA_VERSION = "2026-05-24";
    private static final String GAME = "minecraft-java";
    private static final String ADAPTER = "dogido-fabric-client";
    private static final int SOUND_OBSERVATION_TTL_TICKS = 12;
    private static DogidoClientAdapter INSTANCE;

    private DogidoConfig config;
    private DogidoEventClient eventClient;
    private final Map<UUID, Double> lastThreatDistances = new HashMap<>();
    private final Map<UUID, Long> lastThreatSeenTicks = new HashMap<>();
    private final Deque<SoundObservation> recentSoundObservations = new ArrayDeque<>();

    private long tickCounter = 0;
    private long lastSnapshotTick = -1;
    private long lastThreatTick = -1;
    private long lastAudioEventTick = -1;
    private long lastDamageTick = -1000;
    private long lastVisualThreatObservedTick = -1000;
    private long lastAudioThreatObservedTick = -1000;
    private long lastCombatSignalTick = -1000;
    private float lastHealth = 20.0f;
    private String lastThreatSignature = "";
    private String lastAudioSignature = "";
    private boolean combatActive = false;
    private boolean wasDead = false;

    @Override
    public void onInitializeClient() {
        INSTANCE = this;
        this.config = DogidoConfig.load();
        this.eventClient = new DogidoEventClient(LOGGER, this.config);
        ClientTickEvents.END_CLIENT_TICK.register(this::onClientTick);
        LOGGER.info("Dogido client adapter ready: target={}", this.config.serverBaseUrl);
    }

    public static void recordGlobalSoundPacket(PlaySoundS2CPacket packet) {
        DogidoClientAdapter instance = INSTANCE;
        if (instance == null) {
            return;
        }
        instance.onGlobalSoundPacket(packet);
    }

    public static void recordEntitySoundPacket(PlaySoundFromEntityS2CPacket packet) {
        DogidoClientAdapter instance = INSTANCE;
        if (instance == null) {
            return;
        }
        instance.onEntitySoundPacket(packet);
    }

    private void onClientTick(MinecraftClient client) {
        if (!this.config.enabled) {
            return;
        }

        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            this.resetTransientState();
            return;
        }

        this.tickCounter += 1;
        this.eventClient.ensureSession(resolvePlayerName(player));
        expireSoundObservations();

        if (player.getHealth() < this.lastHealth) {
            this.lastDamageTick = this.tickCounter;
        }
        this.lastHealth = player.getHealth();

        List<ThreatObservation> threats = scanThreats(player, world);
        List<ThreatObservation> visibleThreats = filterVisibleThreats(threats);
        List<AudioThreatObservation> audioThreats = scanAuditoryThreats(threats);
        updateCombatTracking(visibleThreats, audioThreats);
        boolean deadNow = isPlayerDead(player);

        if (shouldSendSnapshot()) {
            JsonObject snapshot = buildStatusSnapshot(player, world, visibleThreats, audioThreats);
            this.eventClient.postEvent(snapshot);
            this.lastSnapshotTick = this.tickCounter;
        }

        if (shouldSendThreatEvent(visibleThreats)) {
            JsonObject threatEvent = buildThreatApproaching(player, world, visibleThreats, audioThreats);
            this.eventClient.postEvent(threatEvent);
            this.lastThreatTick = this.tickCounter;
            this.lastThreatSignature = threatSignature(visibleThreats);
        }

        if (shouldSendAudioThreatEvent(visibleThreats, audioThreats)) {
            JsonObject audioEvent = buildHostileAudioDetected(player, world, audioThreats);
            this.eventClient.postEvent(audioEvent);
            this.lastAudioEventTick = this.tickCounter;
            this.lastAudioSignature = audioThreatSignature(audioThreats);
        }

        if (deadNow && !this.wasDead) {
            JsonObject deathEvent = buildPlayerDied(player, world, visibleThreats, audioThreats);
            this.eventClient.postEvent(deathEvent);
            this.combatActive = false;
        } else if (shouldSendCombatEnded(visibleThreats, audioThreats, deadNow)) {
            JsonObject combatEndedEvent = buildCombatEnded(player, world);
            this.eventClient.postEvent(combatEndedEvent);
            this.combatActive = false;
        }

        this.wasDead = deadNow;
        expireThreatMemory();
    }

    private void resetTransientState() {
        this.tickCounter = 0;
        this.lastSnapshotTick = -1;
        this.lastThreatTick = -1;
        this.lastAudioEventTick = -1;
        this.lastDamageTick = -1000;
        this.lastVisualThreatObservedTick = -1000;
        this.lastAudioThreatObservedTick = -1000;
        this.lastCombatSignalTick = -1000;
        this.lastHealth = 20.0f;
        this.lastThreatSignature = "";
        this.lastAudioSignature = "";
        this.combatActive = false;
        this.wasDead = false;
        this.lastThreatDistances.clear();
        this.lastThreatSeenTicks.clear();
        this.recentSoundObservations.clear();
    }

    private boolean shouldSendSnapshot() {
        return this.lastSnapshotTick < 0 || this.tickCounter - this.lastSnapshotTick >= this.config.snapshotIntervalTicks;
    }

    private boolean shouldSendThreatEvent(List<ThreatObservation> threats) {
        if (threats.isEmpty()) {
            return false;
        }
        ThreatObservation nearest = threats.getFirst();
        boolean urgent = nearest.distance() <= this.config.panicDistance
            || nearest.approaching()
            || nearest.isRearThreat();
        String signature = threatSignature(threats);
        int minIntervalTicks = urgent
            ? Math.max(1, this.config.threatScanIntervalTicks / 2)
            : this.config.threatScanIntervalTicks;
        if (this.lastThreatTick >= 0 && this.tickCounter - this.lastThreatTick < minIntervalTicks) {
            return false;
        }
        return urgent || !signature.equals(this.lastThreatSignature);
    }

    private boolean shouldSendAudioThreatEvent(
        List<ThreatObservation> visibleThreats,
        List<AudioThreatObservation> audioThreats
    ) {
        if (!visibleThreats.isEmpty()) {
            return false;
        }
        if (audioThreats.isEmpty()) {
            return false;
        }
        boolean urgent = audioThreats.stream().anyMatch(
            threat -> "touching".equals(threat.distanceBand()) || "very_close".equals(threat.distanceBand())
        );
        int minIntervalTicks = urgent
            ? Math.max(1, this.config.audioScanIntervalTicks / 2)
            : this.config.audioScanIntervalTicks;
        if (this.lastAudioEventTick >= 0 && this.tickCounter - this.lastAudioEventTick < minIntervalTicks) {
            return false;
        }

        String signature = audioThreatSignature(audioThreats);
        return !signature.equals(this.lastAudioSignature);
    }

    private boolean shouldSendCombatEnded(
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        boolean deadNow
    ) {
        if (!this.combatActive || deadNow) {
            return false;
        }
        if (!threats.isEmpty() || !audioThreats.isEmpty()) {
            return false;
        }
        if (this.tickCounter - this.lastDamageTick <= this.config.combatEndedQuietTicks) {
            return false;
        }
        return this.lastCombatSignalTick >= 0
            && this.tickCounter - this.lastCombatSignalTick >= this.config.combatEndedQuietTicks;
    }

    private List<ThreatObservation> scanThreats(ClientPlayerEntity player, ClientWorld world) {
        double scanDistance = Math.max(this.config.maxThreatDistance, this.config.visibleThreatDistance);
        List<Entity> entities = world.getOtherEntities(
            player,
            player.getBoundingBox().expand(scanDistance),
            entity -> entity instanceof HostileEntity && entity.isAlive()
        );

        List<ThreatObservation> threats = new ArrayList<>();
        for (Entity entity : entities) {
            double distance = Math.sqrt(player.squaredDistanceTo(entity));
            if (distance > scanDistance) {
                continue;
            }

            Vec3d entityPosition = new Vec3d(entity.getX(), entity.getY(), entity.getZ());
            String horizontal = classifyHorizontal(player, entityPosition);
            String vertical = classifyVertical(player, entityPosition);
            boolean approaching = isApproaching(entity.getUuid(), distance);
            boolean rearThreat = distance <= this.config.rearWarningDistance
                && ("back".equals(horizontal) || "back_left".equals(horizontal) || "back_right".equals(horizontal));
            boolean lineOfSight = hasLineOfSight(player, world, entity);

            ThreatObservation observation = new ThreatObservation(
                entity.getUuid(),
                entityTypeName(entity),
                distance,
                horizontal,
                vertical,
                approaching,
                rearThreat,
                lineOfSight,
                entity.isOnFire()
            );
            threats.add(observation);
            this.lastThreatDistances.put(entity.getUuid(), distance);
            this.lastThreatSeenTicks.put(entity.getUuid(), this.tickCounter);
        }

        threats.sort(Comparator.comparingDouble(ThreatObservation::distance));
        return threats;
    }

    private List<AudioThreatObservation> scanAuditoryThreats(List<ThreatObservation> threats) {
        Map<String, AudioThreatObservation> deduped = new LinkedHashMap<>();
        for (SoundObservation observation : this.recentSoundObservations) {
            String key = observation.horizontalDirection() + ":" + observation.distanceBand() + ":" + observation.label();
            deduped.putIfAbsent(
                key,
                new AudioThreatObservation(
                    observation.label(),
                    observation.soundEvent(),
                    observation.horizontalDirection(),
                    observation.verticalRelation(),
                    observation.distanceBand(),
                    observation.certainty(),
                    observation.spokenNameAllowed()
                )
            );
        }
        return new ArrayList<>(deduped.values());
    }

    private List<ThreatObservation> filterVisibleThreats(List<ThreatObservation> threats) {
        List<ThreatObservation> visibleThreats = new ArrayList<>();
        for (ThreatObservation threat : threats) {
            if (threat.lineOfSight()) {
                visibleThreats.add(threat);
            }
        }
        return visibleThreats;
    }

    private void updateCombatTracking(List<ThreatObservation> threats, List<AudioThreatObservation> audioThreats) {
        if (!threats.isEmpty()) {
            this.lastVisualThreatObservedTick = this.tickCounter;
        }
        if (!audioThreats.isEmpty()) {
            this.lastAudioThreatObservedTick = this.tickCounter;
        }

        boolean recentDamage = this.tickCounter - this.lastDamageTick <= this.config.combatEndedQuietTicks;
        if (!threats.isEmpty() || !audioThreats.isEmpty() || recentDamage) {
            this.lastCombatSignalTick = this.tickCounter;
            this.combatActive = true;
        }
    }

    private boolean isApproaching(UUID entityId, double currentDistance) {
        Double previousDistance = this.lastThreatDistances.get(entityId);
        return previousDistance != null && currentDistance + 0.2 < previousDistance;
    }

    private void expireThreatMemory() {
        List<UUID> toRemove = new ArrayList<>();
        for (Map.Entry<UUID, Long> entry : this.lastThreatSeenTicks.entrySet()) {
            if (this.tickCounter - entry.getValue() > 100) {
                toRemove.add(entry.getKey());
            }
        }
        for (UUID uuid : toRemove) {
            this.lastThreatSeenTicks.remove(uuid);
            this.lastThreatDistances.remove(uuid);
        }
    }

    private void expireSoundObservations() {
        while (!this.recentSoundObservations.isEmpty()) {
            SoundObservation oldest = this.recentSoundObservations.peekFirst();
            if (oldest == null || this.tickCounter - oldest.observedTick() <= SOUND_OBSERVATION_TTL_TICKS) {
                break;
            }
            this.recentSoundObservations.removeFirst();
        }
    }

    private JsonObject buildStatusSnapshot(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonObject root = baseEnvelope("status_snapshot", "system", "background", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", new JsonArray());
        root.add("combat", buildCombat(threats, audioThreats));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildThreatApproaching(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonObject root = baseEnvelope("threat_approaching", "visual", "urgent", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", new JsonArray());
        root.add("combat", buildCombat(threats, audioThreats));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildHostileAudioDetected(
        ClientPlayerEntity player,
        ClientWorld world,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonObject root = baseEnvelope("hostile_audio_detected", "auditory", "normal", "medium");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", new JsonArray());
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", new JsonArray());
        root.add("combat", buildCombat(List.of(), audioThreats));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildPlayerDied(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonObject root = baseEnvelope("player_died", "system", "urgent", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", new JsonArray());
        root.add("combat", buildCombat(threats, audioThreats));
        root.add("meta", buildMeta(resolveDeathCause(player)));
        return root;
    }

    private JsonObject buildCombatEnded(ClientPlayerEntity player, ClientWorld world) {
        JsonObject root = baseEnvelope("combat_ended", "system", "normal", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", new JsonArray());
        root.add("auditory_threats", new JsonArray());
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", new JsonArray());
        root.add("combat", buildCombat(List.of(), List.of()));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject baseEnvelope(String eventName, String sourceKind, String priorityHint, String certainty) {
        JsonObject root = new JsonObject();
        root.addProperty("schema_version", SCHEMA_VERSION);
        root.addProperty("game", GAME);
        root.addProperty("adapter", ADAPTER);
        root.addProperty("observed_at", OffsetDateTime.now().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME));

        JsonObject event = new JsonObject();
        event.addProperty("name", eventName);
        event.addProperty("source_kind", sourceKind);
        event.addProperty("priority_hint", priorityHint);
        event.addProperty("certainty", certainty);
        root.add("event", event);
        return root;
    }

    private JsonObject buildPlayer(ClientPlayerEntity player, ClientWorld world) {
        JsonObject json = new JsonObject();
        json.addProperty("name", resolvePlayerName(player));

        JsonObject position = new JsonObject();
        position.addProperty("x", round(player.getX()));
        position.addProperty("y", round(player.getY()));
        position.addProperty("z", round(player.getZ()));
        json.add("position", position);

        json.addProperty("yaw", round(player.getYaw()));
        json.addProperty("pitch", round(player.getPitch()));
        json.addProperty("health", round(player.getHealth()));
        json.addProperty("hunger", player.getHungerManager().getFoodLevel());
        json.addProperty("dimension", world.getRegistryKey().getValue().toString());

        ItemStack held = player.getMainHandStack();
        json.addProperty(
            "held_item",
            held.isEmpty() ? "minecraft:air" : Registries.ITEM.getId(held.getItem()).toString()
        );
        return json;
    }

    private JsonObject buildWorld(ClientPlayerEntity player, ClientWorld world) {
        BlockPos pos = player.getBlockPos();
        JsonObject json = new JsonObject();
        long timeOfDay = world.getTimeOfDay() % 24000L;

        json.addProperty("time_of_day", (int) timeOfDay);
        json.addProperty("time_phase", classifyTimePhase(timeOfDay));
        json.addProperty("weather", classifyWeather(world));
        json.addProperty("biome", resolveBiomeName(world, pos));
        json.addProperty("local_light", world.getLightLevel(pos));
        json.addProperty("sky_visible", world.isSkyVisible(pos));
        json.addProperty("ceiling_height", round(estimateCeilingHeight(world, pos)));
        json.addProperty("overhead_cover_type", classifyOverheadCover(world, pos));

        double enclosureScore = estimateEnclosureScore(world, pos);
        int connectedDarkVolume = estimateConnectedDarkVolume(world, pos);
        double nearestDarkSpawnDistance = estimateNearestDarkSpawnDistance(world, pos);
        double darknessScore = estimateDangerDarkness(world, pos, enclosureScore, connectedDarkVolume, nearestDarkSpawnDistance);

        json.addProperty("enclosure_score", round(enclosureScore));
        json.addProperty("connected_dark_volume", connectedDarkVolume);
        json.addProperty("nearest_dark_spawn_distance", round(nearestDarkSpawnDistance));
        json.addProperty("danger_darkness_score", round(darknessScore));
        return json;
    }

    private JsonArray buildVisualThreats(List<ThreatObservation> threats) {
        JsonArray array = new JsonArray();
        for (ThreatObservation threat : threats) {
            JsonObject entry = new JsonObject();
            entry.addProperty("type", threat.type());
            entry.addProperty("entity_id", threat.uuid().toString());
            entry.addProperty("distance", round(threat.distance()));

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", threat.horizontalDirection());
            direction.addProperty("vertical", threat.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("approaching", threat.approaching());
            entry.addProperty("on_fire", threat.onFire());
            entry.addProperty("certainty", "high");
            array.add(entry);
        }
        return array;
    }

    private JsonArray buildAuditoryThreats(List<AudioThreatObservation> audioThreats) {
        JsonArray array = new JsonArray();
        for (AudioThreatObservation threat : audioThreats) {
            JsonObject entry = new JsonObject();
            entry.addProperty("label", threat.label());
            entry.addProperty("sound_event", threat.soundEvent());

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", threat.horizontalDirection());
            direction.addProperty("vertical", threat.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("distance_band", threat.distanceBand());
            entry.addProperty("certainty", threat.certainty());
            entry.addProperty("spoken_name_allowed", threat.spokenNameAllowed());
            array.add(entry);
        }
        return array;
    }

    private JsonObject buildInventory(ClientPlayerEntity player) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        int size = player.getInventory().size();
        for (int slot = 0; slot < size; slot += 1) {
            ItemStack stack = player.getInventory().getStack(slot);
            if (stack.isEmpty()) {
                continue;
            }
            String key = Registries.ITEM.getId(stack.getItem()).getPath();
            counts.merge(key, stack.getCount(), Integer::sum);
        }

        JsonObject json = new JsonObject();
        for (Map.Entry<String, Integer> entry : counts.entrySet()) {
            json.addProperty(entry.getKey(), entry.getValue());
        }
        return json;
    }

    private JsonObject buildCombat(List<ThreatObservation> threats, List<AudioThreatObservation> audioThreats) {
        JsonObject json = new JsonObject();
        json.addProperty("recent_damage_ms", ticksSince(this.lastDamageTick));
        json.addProperty("recent_hostile_visual_ms", ticksSince(this.lastVisualThreatObservedTick));
        json.addProperty("recent_hostile_audio_ms", ticksSince(this.lastAudioThreatObservedTick));
        json.addProperty("hostiles_within_7", countThreatsWithin(threats, 7.0));
        json.addProperty("hostiles_within_10", countThreatsWithin(threats, 10.0));
        json.addProperty("combat_active_hint", this.combatActive || !threats.isEmpty() || !audioThreats.isEmpty());
        return json;
    }

    private JsonObject buildMeta(String deathCause) {
        JsonObject json = new JsonObject();
        json.addProperty("adapter_build", "0.1.0");
        json.addProperty("profile_name", "default");
        json.addProperty("debug", false);
        if (deathCause != null && !deathCause.isBlank()) {
            json.addProperty("death_cause", deathCause);
        }
        return json;
    }

    private int countThreatsWithin(List<ThreatObservation> threats, double distance) {
        int count = 0;
        for (ThreatObservation threat : threats) {
            if (threat.distance() <= distance) {
                count += 1;
            }
        }
        return count;
    }

    private String threatSignature(List<ThreatObservation> threats) {
        StringBuilder builder = new StringBuilder();
        int limit = Math.min(3, threats.size());
        for (int index = 0; index < limit; index += 1) {
            ThreatObservation threat = threats.get(index);
            builder.append(threat.type())
                .append(':')
                .append(threat.horizontalDirection())
                .append(':')
                .append(bucketDistance(threat.distance()))
                .append('|');
        }
        return builder.toString();
    }

    private String audioThreatSignature(List<AudioThreatObservation> audioThreats) {
        StringBuilder builder = new StringBuilder();
        int limit = Math.min(3, audioThreats.size());
        for (int index = 0; index < limit; index += 1) {
            AudioThreatObservation threat = audioThreats.get(index);
            builder.append(threat.label())
                .append(':');
            builder.append(threat.horizontalDirection())
                .append(':')
                .append(threat.distanceBand())
                .append('|');
        }
        return builder.toString();
    }

    private String resolvePlayerName(ClientPlayerEntity player) {
        if (!this.config.playerNameOverride.isBlank()) {
            return this.config.playerNameOverride;
        }
        return player.getName().getString();
    }

    private String classifyWeather(ClientWorld world) {
        if (world.isThundering()) {
            return "thunder";
        }
        if (world.isRaining()) {
            return "rain";
        }
        return "clear";
    }

    private String resolveBiomeName(ClientWorld world, BlockPos pos) {
        Optional<Identifier> biomeId = world.getBiome(pos).getKey().map(key -> key.getValue());
        return biomeId.map(Identifier::getPath).orElse("unknown");
    }

    private void onGlobalSoundPacket(PlaySoundS2CPacket packet) {
        MinecraftClient client = MinecraftClient.getInstance();
        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            return;
        }
        if (packet.getCategory() != SoundCategory.HOSTILE) {
            return;
        }

        String soundEventId = soundEventId(packet.getSound());
        String hostileLabel = hostileLabelFromSoundEvent(soundEventId);
        if (hostileLabel == null) {
            return;
        }

        Vec3d source = new Vec3d(packet.getX(), packet.getY(), packet.getZ());
        recordSoundObservation(player, soundEventId, hostileLabel, source, false);
    }

    private void onEntitySoundPacket(PlaySoundFromEntityS2CPacket packet) {
        MinecraftClient client = MinecraftClient.getInstance();
        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            return;
        }
        if (packet.getCategory() != SoundCategory.HOSTILE) {
            return;
        }

        Entity entity = world.getEntityById(packet.getEntityId());
        String soundEventId = soundEventId(packet.getSound());
        if (!(entity instanceof HostileEntity hostileEntity) || !hostileEntity.isAlive()) {
            String fallbackLabel = hostileLabelFromSoundEvent(soundEventId);
            if (fallbackLabel == null) {
                return;
            }
            if (entity != null) {
                recordSoundObservation(
                    player,
                    soundEventId,
                    fallbackLabel,
                    new Vec3d(entity.getX(), entity.getY(), entity.getZ()),
                    false
                );
            }
            return;
        }

        recordSoundObservation(
            player,
            soundEventId,
            entityTypeName(hostileEntity),
            new Vec3d(hostileEntity.getX(), hostileEntity.getY(), hostileEntity.getZ()),
            true
        );
    }

    private void recordSoundObservation(
        ClientPlayerEntity player,
        String soundEventId,
        String hostileLabel,
        Vec3d source,
        boolean spokenNameAllowed
    ) {
        double distance = Math.sqrt(player.squaredDistanceTo(source));
        if (distance > this.config.audioThreatDistance) {
            return;
        }

        String horizontal = classifyHorizontal(player, source);
        String vertical = classifyVertical(player, source);
        String distanceBand = bucketDistance(distance);
        String certainty = distance <= 4.0 ? "high" : distance <= 8.0 ? "medium" : "low";

        this.recentSoundObservations.addLast(
            new SoundObservation(
                this.tickCounter,
                hostileLabel,
                soundEventId,
                horizontal,
                vertical,
                distanceBand,
                certainty,
                spokenNameAllowed
            )
        );
        expireSoundObservations();
        this.lastAudioThreatObservedTick = this.tickCounter;
    }

    private String soundEventId(net.minecraft.registry.entry.RegistryEntry<net.minecraft.sound.SoundEvent> sound) {
        return sound.getKey().map(key -> key.getValue().getPath()).orElse("unknown");
    }

    private String hostileLabelFromSoundEvent(String soundEventId) {
        String[] hostileIds = {"creeper", "zombie", "skeleton", "spider", "witch", "enderman", "slime", "drowned"};
        for (String hostileId : hostileIds) {
            if (soundEventId.contains(hostileId)) {
                return hostileId;
            }
        }
        return null;
    }

    private String classifyTimePhase(long timeOfDay) {
        if (timeOfDay < 2000L) {
            return "morning";
        }
        if (timeOfDay < 11000L) {
            return "day";
        }
        if (timeOfDay < 13000L) {
            return "evening";
        }
        return "night";
    }

    private double estimateCeilingHeight(ClientWorld world, BlockPos origin) {
        BlockPos.Mutable cursor = new BlockPos.Mutable(origin.getX(), origin.getY() + 1, origin.getZ());
        int maxSteps = 24;
        for (int step = 1; step <= maxSteps; step += 1) {
            if (!world.getBlockState(cursor).isAir()) {
                return step;
            }
            cursor.move(0, 1, 0);
        }
        return maxSteps;
    }

    private String classifyOverheadCover(ClientWorld world, BlockPos origin) {
        BlockPos.Mutable cursor = new BlockPos.Mutable(origin.getX(), origin.getY() + 1, origin.getZ());
        int maxSteps = 24;
        for (int step = 1; step <= maxSteps; step += 1) {
            BlockState state = world.getBlockState(cursor);
            if (!state.isAir()) {
                return classifyCoverBlock(state);
            }
            cursor.move(0, 1, 0);
        }
        return "none";
    }

    private String classifyCoverBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (blockId.contains("leaves") || blockId.contains("vine") || blockId.contains("moss")) {
            return "foliage";
        }
        if (blockId.endsWith("_log") || blockId.endsWith("_wood") || blockId.contains("stem") || blockId.contains("hyphae")) {
            return "wood";
        }
        return "solid";
    }

    private double estimateEnclosureScore(ClientWorld world, BlockPos origin) {
        int solid = 0;
        int total = 0;
        for (int dx = -2; dx <= 2; dx += 1) {
            for (int dy = -1; dy <= 1; dy += 1) {
                for (int dz = -2; dz <= 2; dz += 1) {
                    if (dx == 0 && dy == 0 && dz == 0) {
                        continue;
                    }
                    BlockPos sample = origin.add(dx, dy, dz);
                    BlockState state = world.getBlockState(sample);
                    total += 1;
                    if (!state.isAir()) {
                        solid += 1;
                    }
                }
            }
        }
        double base = total == 0 ? 0.0 : (double) solid / (double) total;
        if (!world.isSkyVisible(origin)) {
            base += 0.15;
        }
        return clamp01(base);
    }

    private int estimateConnectedDarkVolume(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -6; dx <= 6; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -6; dz <= 6; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (world.getBlockState(sample).isAir() && world.getLightLevel(sample) <= 7) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private double estimateNearestDarkSpawnDistance(ClientWorld world, BlockPos origin) {
        double nearest = 999.0;
        for (int dx = -8; dx <= 8; dx += 1) {
            for (int dy = -4; dy <= 4; dy += 1) {
                for (int dz = -8; dz <= 8; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (!world.getBlockState(sample).isAir()) {
                        continue;
                    }
                    if (world.getLightLevel(sample) > 7) {
                        continue;
                    }
                    double distance = Math.sqrt(sample.getSquaredDistance(origin));
                    if (distance < nearest) {
                        nearest = distance;
                    }
                }
            }
        }
        return nearest == 999.0 ? 99.0 : nearest;
    }

    private double estimateDangerDarkness(
        ClientWorld world,
        BlockPos origin,
        double enclosureScore,
        int connectedDarkVolume,
        double nearestDarkSpawnDistance
    ) {
        double lightPenalty = (15.0 - world.getLightLevel(origin)) / 15.0;
        double darkVolumeScore = Math.min(1.0, connectedDarkVolume / 80.0);
        double spawnDistanceScore = nearestDarkSpawnDistance >= 99.0
            ? 0.0
            : Math.max(0.0, (12.0 - nearestDarkSpawnDistance) / 12.0);
        double skyPenalty = world.isSkyVisible(origin) ? 0.0 : 0.15;
        return clamp01(lightPenalty * 0.35 + enclosureScore * 0.2 + darkVolumeScore * 0.3 + spawnDistanceScore * 0.15 + skyPenalty);
    }

    private boolean hasLineOfSight(ClientPlayerEntity player, ClientWorld world, Entity entity) {
        Vec3d start = player.getEyePos();
        Vec3d end = new Vec3d(entity.getX(), entity.getY() + entity.getHeight() * 0.6, entity.getZ());
        HitResult hit = world.raycast(
            new RaycastContext(
                start,
                end,
                RaycastContext.ShapeType.COLLIDER,
                RaycastContext.FluidHandling.NONE,
                player
            )
        );
        return hit.getType() == HitResult.Type.MISS;
    }

    private boolean isPlayerDead(ClientPlayerEntity player) {
        return !player.isAlive() || player.getHealth() <= 0.0f;
    }

    private String resolveDeathCause(ClientPlayerEntity player) {
        try {
            return player.getDamageTracker().getDeathMessage().getString();
        } catch (Exception ignored) {
            return "unknown";
        }
    }

    private String entityTypeName(Entity entity) {
        return Registries.ENTITY_TYPE.getId(entity.getType()).getPath();
    }

    private String classifyHorizontal(ClientPlayerEntity player, Vec3d targetPos) {
        Vec3d toTarget = new Vec3d(targetPos.x - player.getX(), 0.0, targetPos.z - player.getZ());
        if (toTarget.lengthSquared() < 0.0001) {
            return "front";
        }
        Vec3d normalized = toTarget.normalize();

        double yawRadians = Math.toRadians(player.getYaw());
        double forwardX = -Math.sin(yawRadians);
        double forwardZ = Math.cos(yawRadians);

        double dot = forwardX * normalized.x + forwardZ * normalized.z;
        double cross = forwardZ * normalized.x - forwardX * normalized.z;
        double angle = Math.toDegrees(Math.atan2(cross, dot));

        if (angle >= -22.5 && angle < 22.5) {
            return "front";
        }
        if (angle >= 22.5 && angle < 67.5) {
            return "front_left";
        }
        if (angle >= 67.5 && angle < 112.5) {
            return "left";
        }
        if (angle >= 112.5 && angle < 157.5) {
            return "back_left";
        }
        if (angle >= 157.5 || angle < -157.5) {
            return "back";
        }
        if (angle >= -157.5 && angle < -112.5) {
            return "back_right";
        }
        if (angle >= -112.5 && angle < -67.5) {
            return "right";
        }
        return "front_right";
    }

    private String classifyVertical(ClientPlayerEntity player, Vec3d targetPos) {
        double deltaY = targetPos.y - player.getY();
        if (deltaY > 1.5) {
            return "above";
        }
        if (deltaY < -1.5) {
            return "below";
        }
        return "same";
    }

    private String bucketDistance(double distance) {
        if (distance <= 1.5) {
            return "touching";
        }
        if (distance <= 4.0) {
            return "very_close";
        }
        if (distance <= 8.0) {
            return "close";
        }
        if (distance <= 16.0) {
            return "mid";
        }
        return "far";
    }

    private long ticksSince(long tick) {
        return Math.max(0L, (this.tickCounter - tick) * 50L);
    }

    private double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }

    private double clamp01(double value) {
        return Math.max(0.0, Math.min(1.0, value));
    }

    private record ThreatObservation(
        UUID uuid,
        String type,
        double distance,
        String horizontalDirection,
        String verticalRelation,
        boolean approaching,
        boolean isRearThreat,
        boolean lineOfSight,
        boolean onFire
    ) {
    }

    private record AudioThreatObservation(
        String label,
        String soundEvent,
        String horizontalDirection,
        String verticalRelation,
        String distanceBand,
        String certainty,
        boolean spokenNameAllowed
    ) {
    }

    private record SoundObservation(
        long observedTick,
        String label,
        String soundEvent,
        String horizontalDirection,
        String verticalRelation,
        String distanceBand,
        String certainty,
        boolean spokenNameAllowed
    ) {
    }
}

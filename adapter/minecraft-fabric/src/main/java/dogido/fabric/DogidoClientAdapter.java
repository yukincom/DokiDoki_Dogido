package dogido.fabric;

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
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.block.BlockState;
import net.minecraft.block.enums.DoubleBlockHalf;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.mob.HostileEntity;
import net.minecraft.entity.passive.VillagerEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.entity.passive.SheepEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.network.packet.s2c.play.PlaySoundFromEntityS2CPacket;
import net.minecraft.network.packet.s2c.play.PlaySoundS2CPacket;
import net.minecraft.registry.Registries;
import net.minecraft.sound.SoundCategory;
import net.minecraft.state.property.Properties;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
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
    private final Map<UUID, Long> lineOfSightStartedTicks = new HashMap<>();
    private final Map<UUID, Long> confirmedVisibleTicks = new HashMap<>();
    private final Deque<SoundObservation> recentSoundObservations = new ArrayDeque<>();

    private long tickCounter = 0;
    private long lastSnapshotTick = -1;
    private long lastThreatTick = -1;
    private long lastAudioEventTick = -1;
    private long lastAudioDispatchObservationTick = -1;
    private long lastDamageTick = -1000;
    private long lastVisualThreatObservedTick = -1000;
    private long lastAudioThreatObservedTick = -1000;
    private long lastCombatSignalTick = -1000;
    private float lastHealth = 20.0f;
    private String lastThreatSignature = "";
    private String lastAudioSignature = "";
    private boolean combatActive = false;
    private boolean wasDead = false;
    private boolean wasSleeping = false;
    private boolean respawnPointObserved = false;
    private BlockPos observedRespawnPos = null;
    private String observedRespawnDimension = null;
    private String pendingUserText = null;

    @Override
    public void onInitializeClient() {
        INSTANCE = this;
        this.config = DogidoConfig.load();
        this.eventClient = new DogidoEventClient(LOGGER, this.config);
        ClientTickEvents.END_CLIENT_TICK.register(this::onClientTick);
        ClientSendMessageEvents.CHAT.register(this::rememberUserText);
        ClientSendMessageEvents.COMMAND.register(command -> rememberUserText("/" + command));
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

        boolean sleepingNow = player.isSleeping();
        if (sleepingNow && !this.wasSleeping && countNearbyBeds(world, player.getBlockPos()) > 0) {
            this.respawnPointObserved = true;
            this.observedRespawnPos = player.getBlockPos().toImmutable();
            this.observedRespawnDimension = world.getRegistryKey().getValue().toString();
        }
        this.wasSleeping = sleepingNow;

        if (player.getHealth() < this.lastHealth) {
            this.lastDamageTick = this.tickCounter;
        }
        this.lastHealth = player.getHealth();

        List<ThreatObservation> threats = scanThreats(player, world);
        List<ThreatObservation> visibleThreats = filterVisibleThreats(threats);
        List<AudioThreatObservation> audioThreats = scanAuditoryThreats(player);
        List<AudioThreatObservation> unseenAudioThreats = filterUnseenAudioThreats(visibleThreats, audioThreats);
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

        if (shouldSendAudioThreatEvent(unseenAudioThreats)) {
            JsonObject audioEvent = buildHostileAudioDetected(player, world, unseenAudioThreats);
            this.eventClient.postEvent(audioEvent);
            this.lastAudioEventTick = this.tickCounter;
            this.lastAudioDispatchObservationTick = latestAudioObservationTick(unseenAudioThreats);
            this.lastAudioSignature = audioThreatSignature(unseenAudioThreats);
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
        this.lastAudioDispatchObservationTick = -1;
        this.lastDamageTick = -1000;
        this.lastVisualThreatObservedTick = -1000;
        this.lastAudioThreatObservedTick = -1000;
        this.lastCombatSignalTick = -1000;
        this.lastHealth = 20.0f;
        this.lastThreatSignature = "";
        this.lastAudioSignature = "";
        this.combatActive = false;
        this.wasDead = false;
        this.wasSleeping = false;
        this.respawnPointObserved = false;
        this.observedRespawnPos = null;
        this.observedRespawnDimension = null;
        this.pendingUserText = null;
        this.lastThreatDistances.clear();
        this.lastThreatSeenTicks.clear();
        this.lineOfSightStartedTicks.clear();
        this.confirmedVisibleTicks.clear();
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

    private boolean shouldSendAudioThreatEvent(List<AudioThreatObservation> audioThreats) {
        if (audioThreats.isEmpty()) {
            return false;
        }
        long latestObservationTick = latestAudioObservationTick(audioThreats);
        if (latestObservationTick <= this.lastAudioDispatchObservationTick) {
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

    private List<AudioThreatObservation> filterUnseenAudioThreats(
        List<ThreatObservation> visibleThreats,
        List<AudioThreatObservation> audioThreats
    ) {
        if (audioThreats.isEmpty()) {
            return List.of();
        }
        java.util.Set<String> visibleIds = new java.util.HashSet<>();
        for (ThreatObservation threat : visibleThreats) {
            visibleIds.add(threat.uuid().toString());
        }
        List<AudioThreatObservation> filtered = new ArrayList<>();
        for (AudioThreatObservation threat : audioThreats) {
            if (visibleIds.contains(threat.sourceId())) {
                continue;
            }
            filtered.add(threat);
        }
        return filtered;
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
                entity.isOnFire(),
                entity.isTouchingWater() || entity.isSubmergedInWater()
            );
            threats.add(observation);
            this.lastThreatDistances.put(entity.getUuid(), distance);
            this.lastThreatSeenTicks.put(entity.getUuid(), this.tickCounter);
        }

        threats.sort(Comparator.comparingDouble(ThreatObservation::distance));
        return threats;
    }

    private List<AudioThreatObservation> scanAuditoryThreats(ClientPlayerEntity player) {
        Map<String, AudioThreatObservation> deduped = new LinkedHashMap<>();
        for (SoundObservation observation : this.recentSoundObservations) {
            Vec3d source = new Vec3d(observation.sourceX(), observation.sourceY(), observation.sourceZ());
            double distance = Math.sqrt(player.squaredDistanceTo(source));
            if (distance > this.config.audioThreatDistance) {
                continue;
            }

            String horizontal = classifyHorizontal(player, source);
            String vertical = classifyVertical(player, source);
            String distanceBand = bucketDistance(distance);
            String certainty = distance <= 4.0 ? "high" : distance <= 8.0 ? "medium" : "low";

            deduped.put(
                observation.sourceId(),
                new AudioThreatObservation(
                    observation.label(),
                    observation.sourceId(),
                    observation.soundEvent(),
                    horizontal,
                    vertical,
                    distanceBand,
                    certainty,
                    observation.spokenNameAllowed(),
                    observation.observedTick()
                )
            );
        }

        List<AudioThreatObservation> audioThreats = new ArrayList<>(deduped.values());
        audioThreats.sort(Comparator.comparingInt(observation -> distanceBandRank(observation.distanceBand())));
        return audioThreats;
    }

    private List<ThreatObservation> filterVisibleThreats(List<ThreatObservation> threats) {
        List<ThreatObservation> visibleThreats = new ArrayList<>();
        int lineOfSightCount = 0;
        for (ThreatObservation threat : threats) {
            if (threat.lineOfSight()) {
                lineOfSightCount += 1;
            }
        }
        boolean immediateForVisibleCluster = lineOfSightCount >= 2;
        for (ThreatObservation threat : threats) {
            if (shouldTreatAsVisible(threat, immediateForVisibleCluster)) {
                visibleThreats.add(threat);
            }
        }
        return visibleThreats;
    }

    private boolean shouldTreatAsVisible(ThreatObservation threat, boolean immediateForVisibleCluster) {
        UUID id = threat.uuid();
        if (threat.lineOfSight()) {
            this.lineOfSightStartedTicks.putIfAbsent(id, this.tickCounter);
            long visibleFor = this.tickCounter - this.lineOfSightStartedTicks.getOrDefault(id, this.tickCounter);
            boolean immediate = threat.distance() <= this.config.panicDistance || threat.isRearThreat();
            if (immediate || immediateForVisibleCluster || visibleFor >= this.config.visualConfirmTicks) {
                this.confirmedVisibleTicks.put(id, this.tickCounter);
                return true;
            }
            return false;
        }

        this.lineOfSightStartedTicks.remove(id);
        Long confirmedTick = this.confirmedVisibleTicks.get(id);
        if (confirmedTick == null) {
            return false;
        }
        if (this.tickCounter - confirmedTick <= this.config.visualHoldTicks) {
            return true;
        }
        this.confirmedVisibleTicks.remove(id);
        return false;
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
            this.lineOfSightStartedTicks.remove(uuid);
            this.confirmedVisibleTicks.remove(uuid);
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

    private long latestAudioObservationTick(List<AudioThreatObservation> audioThreats) {
        long latest = -1;
        for (AudioThreatObservation observation : audioThreats) {
            latest = Math.max(latest, observation.observedTick());
        }
        return latest;
    }

    private JsonObject buildStatusSnapshot(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("status_snapshot", "system", "background", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
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
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("threat_approaching", "visual", "urgent", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(threats, audioThreats));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildHostileAudioDetected(
        ClientPlayerEntity player,
        ClientWorld world,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("hostile_audio_detected", "auditory", "normal", "medium");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", new JsonArray());
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
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
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("player_died", "system", "urgent", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(threats, audioThreats));
        root.add("meta", buildMeta(resolveDeathCause(player)));
        return root;
    }

    private JsonObject buildCombatEnded(ClientPlayerEntity player, ClientWorld world) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("combat_ended", "system", "normal", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", new JsonArray());
        root.add("auditory_threats", new JsonArray());
        root.add("peaceful_mobs", new JsonArray());
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
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
        boolean submerged = isWaterContext(player);

        json.addProperty("time_of_day", (int) timeOfDay);
        json.addProperty("time_phase", classifyTimePhase(timeOfDay));
        json.addProperty("weather", classifyWeather(world));
        json.addProperty("biome", resolveBiomeName(world, pos));
        json.addProperty("local_light", world.getLightLevel(pos));
        json.addProperty("sky_visible", world.isSkyVisible(pos));
        double ceilingHeight = estimateCeilingHeight(world, pos);
        double enclosureScore = estimateEnclosureScore(world, pos);
        int nearbyDoorCount = countNearbyDoors(world, pos);
        int openDoorCount = countOpenDoors(world, pos);
        int nearbyBedCount = countNearbyBeds(world, pos);
        int nearbySleepingPeopleCount = countNearbySleepingPeople(world, player);
        int draftyOpeningCount = countDraftyOpenings(world, pos);
        int submergedDepthBlocks = estimateSubmergedDepthBlocks(world, pos, submerged);
        int nearbyFireflyBushCount = countNearbyFireflyBushes(world, pos);
        int cardinalWallCount = countCardinalWalls(world, pos);
        Double respawnDistance = estimateRespawnDistance(world, pos);
        json.addProperty("ceiling_height", round(ceilingHeight));
        json.addProperty("overhead_cover_type", classifyOverheadCover(world, pos));
        json.addProperty("is_submerged", submerged);
        json.addProperty("submerged_depth_blocks", submergedDepthBlocks);
        json.addProperty("air_supply", player.getAir());
        json.addProperty("nearby_door_count", nearbyDoorCount);
        json.addProperty("open_door_count", openDoorCount);
        json.addProperty("nearby_bed_count", nearbyBedCount);
        json.addProperty("nearby_sleeping_people_count", nearbySleepingPeopleCount);
        json.addProperty("drafty_opening_count", draftyOpeningCount);
        json.addProperty("nearby_firefly_bush_count", nearbyFireflyBushCount);
        json.addProperty("respawn_point_set", this.respawnPointObserved);
        json.addProperty("cardinal_wall_count", cardinalWallCount);
        if (respawnDistance != null) {
            json.addProperty("respawn_distance", round(respawnDistance));
        }
        json.addProperty(
            "safe_zone_with_door",
            isSafeZoneWithDoor(world, pos, submerged, nearbyDoorCount, enclosureScore, ceilingHeight)
        );

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
            entry.addProperty("in_water", threat.inWater());
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
            entry.addProperty("source_id", threat.sourceId());
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

    private JsonArray buildNearbyResources(ClientPlayerEntity player, ClientWorld world) {
        BlockPos origin = player.getBlockPos();
        Map<String, Double> nearestDistances = new LinkedHashMap<>();
        Map<String, String> resourceTypes = new LinkedHashMap<>();

        for (int dx = -8; dx <= 8; dx += 1) {
            for (int dy = -3; dy <= 4; dy += 1) {
                for (int dz = -8; dz <= 8; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    String resourceName = nearbyResourceNameForBlock(world.getBlockState(sample));
                    if (resourceName == null) {
                        continue;
                    }
                    double distance = Math.sqrt(sample.getSquaredDistance(origin));
                    mergeNearbyResource(resourceTypes, nearestDistances, "block", resourceName, distance);
                }
            }
        }

        for (Entity entity : world.getOtherEntities(player, new net.minecraft.util.math.Box(
            origin.getX() - 8.0, origin.getY() - 3.0, origin.getZ() - 8.0,
            origin.getX() + 9.0, origin.getY() + 5.0, origin.getZ() + 9.0
        ))) {
            if (!(entity instanceof SheepEntity sheep) || !sheep.isAlive()) {
                continue;
            }
            String resourceName = sheep.getColor().asString() + "_wool";
            double distance = Math.sqrt(player.squaredDistanceTo(sheep));
            mergeNearbyResource(resourceTypes, nearestDistances, "mob", resourceName, distance);
        }

        JsonArray array = new JsonArray();
        nearestDistances.entrySet().stream()
            .sorted(Map.Entry.comparingByValue())
            .limit(16)
            .forEach(entry -> {
                JsonObject item = new JsonObject();
                item.addProperty("type", resourceTypes.get(entry.getKey()));
                item.addProperty("name", entry.getKey());
                item.addProperty("distance", round(entry.getValue()));
                array.add(item);
            });
        return array;
    }

    private String nearbyResourceNameForBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (blockId.endsWith("_log") || blockId.endsWith("_planks") || blockId.endsWith("_wool")) {
            return blockId;
        }
        if ("coal_ore".equals(blockId) || "deepslate_coal_ore".equals(blockId)) {
            return "coal_ore";
        }
        return null;
    }

    private void mergeNearbyResource(
        Map<String, String> resourceTypes,
        Map<String, Double> nearestDistances,
        String type,
        String resourceName,
        double distance
    ) {
        Double previous = nearestDistances.get(resourceName);
        if (previous != null && previous <= distance) {
            return;
        }
        resourceTypes.put(resourceName, type);
        nearestDistances.put(resourceName, distance);
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
        String userText = consumePendingUserText();
        if (userText != null && !userText.isBlank()) {
            json.addProperty("user_text", userText);
        }
        return json;
    }

    private void rememberUserText(String message) {
        if (message == null) {
            return;
        }
        String normalized = message.trim();
        if (normalized.isEmpty()) {
            return;
        }
        if (normalized.length() > 160) {
            normalized = normalized.substring(0, 160);
        }
        this.pendingUserText = normalized;
    }

    private String consumePendingUserText() {
        String userText = this.pendingUserText;
        this.pendingUserText = null;
        return userText;
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
        builder.append("count=").append(threats.size()).append('|');
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (ThreatObservation threat : threats) {
            counts.merge(threat.type(), 1, Integer::sum);
        }
        counts.entrySet().stream()
            .sorted((left, right) -> {
                int countCompare = Integer.compare(right.getValue(), left.getValue());
                return countCompare != 0 ? countCompare : left.getKey().compareTo(right.getKey());
            })
            .forEach(entry -> builder.append(entry.getKey()).append('=').append(entry.getValue()).append('|'));
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

    private int distanceBandRank(String band) {
        return switch (band) {
            case "touching" -> 0;
            case "very_close" -> 1;
            case "close" -> 2;
            case "mid" -> 3;
            case "far" -> 4;
            default -> 99;
        };
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
        String sourceId = resolveSoundSourceId(world, source, hostileLabel, soundEventId);
        recordSoundObservation(
            player,
            soundEventId,
            hostileLabel,
            source,
            true,
            sourceId
        );
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
                    false,
                    entity.getUuid().toString()
                );
            }
            return;
        }

        recordSoundObservation(
            player,
            soundEventId,
            entityTypeName(hostileEntity),
            new Vec3d(hostileEntity.getX(), hostileEntity.getY(), hostileEntity.getZ()),
            true,
            hostileEntity.getUuid().toString()
        );
    }

    private void recordSoundObservation(
        ClientPlayerEntity player,
        String soundEventId,
        String hostileLabel,
        Vec3d source,
        boolean spokenNameAllowed,
        String sourceId
    ) {
        double distance = Math.sqrt(player.squaredDistanceTo(source));
        if (distance > this.config.audioThreatDistance) {
            return;
        }

        this.recentSoundObservations.removeIf(existing -> existing.sourceId().equals(sourceId));
        this.recentSoundObservations.addLast(
            new SoundObservation(
                this.tickCounter,
                sourceId,
                hostileLabel,
                soundEventId,
                source.x,
                source.y,
                source.z,
                spokenNameAllowed
            )
        );
        expireSoundObservations();
        this.lastAudioThreatObservedTick = this.tickCounter;
    }

    private String resolveSoundSourceId(ClientWorld world, Vec3d source, String hostileLabel, String soundEventId) {
        Entity nearest = null;
        double nearestDistance = 4.5;
        for (Entity entity : world.getOtherEntities(null, new net.minecraft.util.math.Box(
            source.x - 4.5, source.y - 4.5, source.z - 4.5,
            source.x + 4.5, source.y + 4.5, source.z + 4.5
        ))) {
            if (!(entity instanceof HostileEntity hostile) || !hostile.isAlive()) {
                continue;
            }
            if (!entityTypeName(hostile).equals(hostileLabel)) {
                continue;
            }
            double dx = entity.getX() - source.x;
            double dy = entity.getY() - source.y;
            double dz = entity.getZ() - source.z;
            double distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
            if (distance < nearestDistance) {
                nearest = entity;
                nearestDistance = distance;
            }
        }
        if (nearest != null) {
            return nearest.getUuid().toString();
        }
        long bucketX = Math.round(source.x / 4.0);
        long bucketY = Math.round(source.y / 4.0);
        long bucketZ = Math.round(source.z / 4.0);
        return "pos:" + soundEventId + ":" + bucketX + ":" + bucketY + ":" + bucketZ;
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
        if (timeOfDay < 14000L) {
            return "evening";
        }
        return "night";
    }

    private double estimateCeilingHeight(ClientWorld world, BlockPos origin) {
        BlockPos.Mutable cursor = new BlockPos.Mutable(origin.getX(), origin.getY() + 1, origin.getZ());
        int maxSteps = 24;
        for (int step = 1; step <= maxSteps; step += 1) {
            if (!isOpenMedium(world.getBlockState(cursor))) {
                return step;
            }
            cursor.move(0, 1, 0);
        }
        return maxSteps;
    }

    private String classifyOverheadCover(ClientWorld world, BlockPos origin) {
        BlockPos.Mutable cursor = new BlockPos.Mutable(origin.getX(), origin.getY() + 1, origin.getZ());
        int maxSteps = 24;
        boolean sawFluid = false;
        for (int step = 1; step <= maxSteps; step += 1) {
            BlockState state = world.getBlockState(cursor);
            if (state.getFluidState().isEmpty() == false) {
                sawFluid = true;
                cursor.move(0, 1, 0);
                continue;
            }
            if (!state.isAir()) {
                return classifyCoverBlock(state);
            }
            cursor.move(0, 1, 0);
        }
        return sawFluid ? "fluid" : "none";
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
                    if (!isOpenMedium(state)) {
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
                    if (isOpenMedium(world.getBlockState(sample)) && world.getLightLevel(sample) <= 7) {
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
                    if (!isOpenMedium(world.getBlockState(sample))) {
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

    private boolean isWaterContext(ClientPlayerEntity player) {
        return player.isTouchingWater() || player.isSubmergedInWater();
    }

    private int estimateSubmergedDepthBlocks(ClientWorld world, BlockPos origin, boolean submerged) {
        if (!submerged) {
            return 0;
        }
        int depth = 0;
        BlockPos.Mutable cursor = new BlockPos.Mutable(origin.getX(), origin.getY(), origin.getZ());
        for (int step = 0; step < 8; step += 1) {
            BlockState state = world.getBlockState(cursor);
            if (state.getFluidState().isEmpty()) {
                break;
            }
            depth += 1;
            cursor.move(0, 1, 0);
        }
        return depth;
    }

    private int countNearbyFireflyBushes(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -16; dx <= 16; dx += 1) {
            for (int dy = -4; dy <= 6; dy += 1) {
                for (int dz = -16; dz <= 16; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    if ("firefly_bush".equals(blockId)) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private boolean isOpenMedium(BlockState state) {
        return state.isAir() || !state.getFluidState().isEmpty();
    }

    private int countNearbyDoors(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -2; dx <= 2; dx += 1) {
            for (int dy = -1; dy <= 2; dy += 1) {
                for (int dz = -2; dz <= 2; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    BlockState state = world.getBlockState(sample);
                    String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
                    if (blockId.endsWith("_door")) {
                        if (
                            state.contains(Properties.DOUBLE_BLOCK_HALF)
                            && state.get(Properties.DOUBLE_BLOCK_HALF) != DoubleBlockHalf.LOWER
                        ) {
                            continue;
                        }
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private int countOpenDoors(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -2; dx <= 2; dx += 1) {
            for (int dy = -1; dy <= 2; dy += 1) {
                for (int dz = -2; dz <= 2; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    BlockState state = world.getBlockState(sample);
                    String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
                    if (!blockId.endsWith("_door")) {
                        continue;
                    }
                    if (
                        state.contains(Properties.DOUBLE_BLOCK_HALF)
                        && state.get(Properties.DOUBLE_BLOCK_HALF) != DoubleBlockHalf.LOWER
                    ) {
                        continue;
                    }
                    if (state.contains(Properties.OPEN) && Boolean.TRUE.equals(state.get(Properties.OPEN))) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private int countNearbyBeds(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -5; dx <= 5; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -5; dz <= 5; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (sample.getSquaredDistance(origin) > 25.0) {
                        continue;
                    }
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    if (blockId.endsWith("_bed")) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private int countNearbySleepingPeople(ClientWorld world, ClientPlayerEntity player) {
        List<Entity> entities = world.getOtherEntities(
            player,
            player.getBoundingBox().expand(5.0, 3.0, 5.0),
            entity -> entity.isAlive()
                && entity instanceof LivingEntity living
                && living.isSleeping()
                && (entity instanceof VillagerEntity || entity instanceof PlayerEntity)
        );

        int count = 0;
        for (Entity entity : entities) {
            if (player.squaredDistanceTo(entity) <= 25.0) {
                count += 1;
            }
        }
        return count;
    }

    private int countCardinalWalls(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (Direction direction : new Direction[] {
            Direction.NORTH,
            Direction.SOUTH,
            Direction.EAST,
            Direction.WEST,
        }) {
            if (isCardinalWall(world, origin, direction)) {
                count += 1;
            }
        }
        return count;
    }

    private boolean isCardinalWall(ClientWorld world, BlockPos origin, Direction direction) {
        BlockPos foot = origin.offset(direction);
        BlockPos head = foot.up();
        return isShelterWallBlock(world.getBlockState(foot)) && isShelterWallBlock(world.getBlockState(head));
    }

    private boolean isShelterWallBlock(BlockState state) {
        if (isOpenMedium(state)) {
            return false;
        }
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return !blockId.endsWith("_door")
            && !blockId.endsWith("_trapdoor")
            && !blockId.endsWith("_fence_gate");
    }

    private Double estimateRespawnDistance(ClientWorld world, BlockPos origin) {
        if (!this.respawnPointObserved || this.observedRespawnPos == null || this.observedRespawnDimension == null) {
            return null;
        }
        String currentDimension = world.getRegistryKey().getValue().toString();
        if (!this.observedRespawnDimension.equals(currentDimension)) {
            return null;
        }
        return Math.sqrt(origin.getSquaredDistance(this.observedRespawnPos));
    }

    private int countDraftyOpenings(ClientWorld world, BlockPos origin) {
        int count = 0;
        int[] perimeter = {-3, -2, -1, 0, 1, 2, 3};
        for (int dy = 0; dy <= 1; dy += 1) {
            for (int edge : perimeter) {
                count += isDraftyOpening(world, origin, edge, dy, -3, 0, 0, -1) ? 1 : 0;
                count += isDraftyOpening(world, origin, edge, dy, 3, 0, 0, 1) ? 1 : 0;
                count += isDraftyOpening(world, origin, -3, dy, edge, -1, 0, 0) ? 1 : 0;
                count += isDraftyOpening(world, origin, 3, dy, edge, 1, 0, 0) ? 1 : 0;
            }
        }
        return count;
    }

    private boolean isDraftyOpening(
        ClientWorld world,
        BlockPos origin,
        int dx,
        int dy,
        int dz,
        int outwardX,
        int outwardY,
        int outwardZ
    ) {
        BlockPos sample = origin.add(dx, dy, dz);
        BlockState state = world.getBlockState(sample);
        if (!isOpenMedium(state)) {
            return false;
        }
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (blockId.contains("glass") || blockId.contains("bars") || blockId.contains("fence")) {
            return false;
        }
        BlockPos outward = sample.add(outwardX, outwardY, outwardZ);
        BlockState outwardState = world.getBlockState(outward);
        return isOpenMedium(outwardState);
    }

    private boolean isSafeZoneWithDoor(
        ClientWorld world,
        BlockPos origin,
        boolean submerged,
        int nearbyDoorCount,
        double enclosureScore,
        double ceilingHeight
    ) {
        if (submerged || nearbyDoorCount <= 0) {
            return false;
        }
        int localLight = world.getLightLevel(origin);
        return localLight >= 8 && (
            enclosureScore >= 0.18
                || ceilingHeight <= 5.0
                || !world.isSkyVisible(origin)
        );
    }

    private boolean hasLineOfSight(ClientPlayerEntity player, ClientWorld world, Entity entity) {
        Vec3d start = player.getEyePos();
        double height = Math.max(entity.getHeight(), 0.5);
        Vec3d[] samplePoints = new Vec3d[] {
            new Vec3d(entity.getX(), entity.getY() + height * 0.25, entity.getZ()),
            new Vec3d(entity.getX(), entity.getY() + height * 0.55, entity.getZ()),
            new Vec3d(entity.getX(), entity.getY() + height * 0.85, entity.getZ()),
        };
        for (Vec3d end : samplePoints) {
            HitResult hit = world.raycast(
                new RaycastContext(
                    start,
                    end,
                    RaycastContext.ShapeType.COLLIDER,
                    RaycastContext.FluidHandling.NONE,
                    player
                )
            );
            if (hit.getType() == HitResult.Type.MISS) {
                return true;
            }
        }
        return false;
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

        if (angle >= -35.0 && angle < 35.0) {
            return "front";
        }
        if (angle >= 35.0 && angle < 80.0) {
            return "front_left";
        }
        if (angle >= 80.0 && angle < 125.0) {
            return "left";
        }
        if (angle >= 125.0 && angle < 155.0) {
            return "back_left";
        }
        if (angle >= 155.0 || angle < -155.0) {
            return "back";
        }
        if (angle >= -155.0 && angle < -125.0) {
            return "back_right";
        }
        if (angle >= -125.0 && angle < -80.0) {
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
        boolean onFire,
        boolean inWater
    ) {
    }

    private record AudioThreatObservation(
        String label,
        String sourceId,
        String soundEvent,
        String horizontalDirection,
        String verticalRelation,
        String distanceBand,
        String certainty,
        boolean spokenNameAllowed,
        long observedTick
    ) {
    }

    private record SoundObservation(
        long observedTick,
        String sourceId,
        String label,
        String soundEvent,
        double sourceX,
        double sourceY,
        double sourceZ,
        boolean spokenNameAllowed
    ) {
    }
}

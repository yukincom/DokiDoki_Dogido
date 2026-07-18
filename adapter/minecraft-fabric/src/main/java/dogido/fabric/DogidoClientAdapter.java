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
import java.util.Set;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.block.BlockState;
import net.minecraft.block.DoorBlock;
import net.minecraft.block.enums.DoubleBlockHalf;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.boss.dragon.EnderDragonEntity;
import net.minecraft.entity.boss.dragon.phase.PhaseType;
import net.minecraft.entity.mob.HostileEntity;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.entity.mob.Monster;
import net.minecraft.entity.mob.PiglinEntity;
import net.minecraft.entity.mob.ShulkerEntity;
import net.minecraft.entity.mob.SpiderEntity;
import net.minecraft.entity.mob.ZombifiedPiglinEntity;
import net.minecraft.entity.passive.BeeEntity;
import net.minecraft.entity.passive.DolphinEntity;
import net.minecraft.entity.passive.FoxEntity;
import net.minecraft.entity.passive.GoatEntity;
import net.minecraft.entity.passive.IronGolemEntity;
import net.minecraft.entity.passive.LlamaEntity;
import net.minecraft.entity.passive.PandaEntity;
import net.minecraft.entity.passive.PolarBearEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.entity.passive.SheepEntity;
import net.minecraft.entity.passive.VillagerEntity;
import net.minecraft.entity.passive.WolfEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.village.VillagerData;
import net.minecraft.network.packet.s2c.play.PlaySoundFromEntityS2CPacket;
import net.minecraft.network.packet.s2c.play.PlaySoundS2CPacket;
import net.minecraft.registry.Registries;
import net.minecraft.registry.RegistryKey;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.sound.SoundCategory;
import net.minecraft.state.property.Properties;
import net.minecraft.structure.StructureStart;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;
import net.minecraft.world.World;

public final class DogidoClientAdapter implements ClientModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("dogido-client-adapter");
    private static final int SOUND_OBSERVATION_TTL_TICKS = 12;
    private static final int OMINOUS_SOUND_TTL_TICKS = 80;
    private static final int WEATHER_SOUND_TTL_TICKS = 80;
    private static final int LIGHTNING_STRIKE_TTL_TICKS = 40;
    private static final int WARDEN_SPECIAL_LATCH_TICKS = 100;
    private static final double LARGE_POSITION_JUMP_BLOCKS = 48.0;
    private static final int BOSS_OMEN_SCAN_RADIUS = 20;
    private static final int WITHER_OMEN_SCAN_RADIUS = 8;
    private static final int LIT_INTERIOR_SAFE_LIGHT_THRESHOLD = 9;
    private static final int LIT_INTERIOR_SAFE_MAX_CONNECTED_VOLUME = 24;
    private static final double LIT_INTERIOR_SAFE_MIN_SPAWN_DISTANCE = 4.0;
    private static final double LIT_INTERIOR_SAFE_MAX_CEILING_HEIGHT = 5.0;
    private static final int OCCLUDED_AUDIO_HORIZONTAL_BLOCKS = 8;
    private static final int OCCLUDED_AUDIO_VERTICAL_BLOCKS = 5;
    private static final double AMBIENT_MOB_DISTANCE = 16.0;
    private static final double GLOBAL_SOUND_SOURCE_MATCH_RADIUS = 4.0;
    // エンダーアイ投擲音: 鮮度 TTL（サーバ側 ender_eye_recent_ms=2000ms と同期）と投擲者判定の距離
    private static final int ENDER_EYE_LAUNCH_TTL_TICKS = 40;
    private static final double ENDER_EYE_LAUNCH_MATCH_RADIUS = 8.0;
    // ドラゴン戦: 視覚脅威の30マス制限とは別に、アリーナ全域を追跡する
    private static final double DRAGON_SCAN_RADIUS = 160.0;
    private static final int DRAGON_DEFEAT_WINDOW_TICKS = 400;
    // ポータルブロック検知: ネザー/エンドポータルは5ブロック、エンドゲートウェイは15ブロック
    private static final int PORTAL_SCAN_RADIUS = 5;
    private static final int GATEWAY_SCAN_RADIUS = 15;
    private static final int END_PORTAL_FRAME_SCAN_RADIUS = 5;
    private static final String[][] HOSTILE_SOUND_LABEL_PATTERNS = {
        {"zombified_piglin", "zombified_piglin"},
        {"zombie_pigman", "zombified_piglin"},
        {"zombie_villager", "zombie_villager"},
        {"wither_skeleton", "wither_skeleton"},
        {"piglin_brute", "piglin_brute"},
        {"magma_cube", "magma_cube"},
        {"warden", "warden"},
        {"creeper", "creeper"},
        {"skeleton", "skeleton"},
        {"spider", "spider"},
        {"witch", "witch"},
        {"enderman", "enderman"},
        {"drowned", "drowned"},
        {"zoglin", "zoglin"},
        {"hoglin", "hoglin"},
        {"ghast", "ghast"},
        {"blaze", "blaze"},
        {"slime", "slime"},
        {"piglin", "piglin"},
        {"zombie", "zombie"}
    };
    private static final Set<String> AMBIENT_NEUTRAL_MONSTER_IDS = Set.of(
        "enderman",
        "spider",
        "cave_spider",
        "drowned",
        "piglin",
        "zombified_piglin"
    );
    private static DogidoClientAdapter INSTANCE;

    private DogidoConfig config;
    private DogidoEventClient eventClient;
    private final Map<UUID, Double> lastThreatDistances = new HashMap<>();
    private final Map<UUID, Long> lastThreatSeenTicks = new HashMap<>();
    private final Map<UUID, Float> lastThreatHealths = new HashMap<>();
    private final Map<UUID, Long> lineOfSightStartedTicks = new HashMap<>();
    private final Map<UUID, Long> confirmedVisibleTicks = new HashMap<>();
    private final Deque<SoundObservation> recentSoundObservations = new ArrayDeque<>();
    /** 村人・家畜など非敵対の周囲音。戦闘判定には使わない。 */
    private final Deque<SoundObservation> recentAmbientSoundObservations = new ArrayDeque<>();

    private long tickCounter = 0;
    private long lastSnapshotTick = -1;
    private long lastThreatTick = -1;
    private long lastAudioEventTick = -1;
    private long lastAmbientMobEventTick = -1;
    private long lastAudioDispatchObservationTick = -1;
    private long lastDamageTick = -1000;
    private long lastVisualThreatObservedTick = -1000;
    private long lastAudioThreatObservedTick = -1000;
    private long lastOminousSoundObservedTick = -1000;
    private long lastExplosionObservedTick = -1000;
    private long lastRainSoundObservedTick = -1000;
    private long lastThunderSoundObservedTick = -1000;
    private long lastNearbyLightningObservedTick = -1000;
    private double lastNearbyLightningDistance = 99.0;
    private long lastEnderEyeLaunchObservedTick = -1000;
    // プレイヤー座標を含む構造物 id（統合サーバーのスレッドで照会した結果のキャッシュ）
    private volatile String structureAtPlayer = null;
    private long lastStructureProbeTick = -1000;
    private long lastCombatSignalTick = -1000;
    private long lastWardenSeenTick = -1000;
    private long lastWardenDeathSoundTick = -1000;
    private int trackedWardenEntityId = -1;
    private long lastWardenDefeatObservedTick = -1000;
    private long lastDragonSeenTick = -1000;
    private long lastDragonDeathSoundTick = -1000;
    private int trackedDragonEntityId = -1;
    private long lastDragonDefeatObservedTick = -1000;
    // 一度でも殻を開いた（動き出した）シュルカー。閉じたままの個体は「ブロックのフリ」を尊重して黙る
    private final Set<UUID> awakenedShulkerIds = new java.util.HashSet<>();
    private long lastWardenEndCrystalObservedTick = -1000;
    private long lastWardenTntSetupObservedTick = -1000;
    private double lastWardenSeenX = 0.0;
    private double lastWardenSeenY = 0.0;
    private double lastWardenSeenZ = 0.0;
    private float lastHealth = 20.0f;
    private String lastThreatSignature = "";
    private String lastAudioSignature = "";
    private String lastAmbientMobSignature = "";
    private String lastOminousSoundKind = "";
    private boolean combatActive = false;
    private boolean wasDead = false;
    private boolean wasSleeping = false;
    private boolean respawnPointObserved = false;
    private BlockPos observedRespawnPos = null;
    private String observedRespawnDimension = null;
    private String lastDimensionId = null;
    private String pendingUserText = null;
    private double lastPlayerX = Double.NaN;
    private double lastPlayerY = Double.NaN;
    private double lastPlayerZ = Double.NaN;

    @Override
    public void onInitializeClient() {
        INSTANCE = this;
        this.config = DogidoConfig.load();
        this.eventClient = new DogidoEventClient(LOGGER, this.config);
        ClientTickEvents.END_CLIENT_TICK.register(this::onClientTick);
        ClientSendMessageEvents.CHAT.register(this::rememberUserText);
        ClientSendMessageEvents.COMMAND.register(command -> rememberUserText("/" + command));
        LOGGER.info(
            "Dogido client adapter ready: target={} version={} build={}",
            this.config.serverBaseUrl,
            DogidoBuildInfo.ADAPTER_VERSION,
            DogidoBuildInfo.ADAPTER_BUILD
        );
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
        String currentDimensionId = world.getRegistryKey().getValue().toString();
        if (this.lastDimensionId != null && !this.lastDimensionId.equals(currentDimensionId)) {
            resetThreatStateForDimensionChange();
        }
        this.lastDimensionId = currentDimensionId;
        if (
            Double.isFinite(this.lastPlayerX)
                && hasLargePositionJump(player.getX(), player.getY(), player.getZ())
        ) {
            resetThreatStateForPositionJump();
        }
        this.lastPlayerX = player.getX();
        this.lastPlayerY = player.getY();
        this.lastPlayerZ = player.getZ();

        this.tickCounter += 1;
        this.eventClient.ensureSession(resolvePlayerName(player));
        expireSoundObservations();
        observeNearbyLightning(player, world);
        probeStructureAtPlayer(client, player, world);

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
        observeWardenSpecialSetups(player, world, threats);
        observeTrackedWardenDefeat(world);
        observeTrackedDragonDefeat(world);
        List<ThreatObservation> visibleThreats = filterVisibleThreats(threats);
        List<AudioThreatObservation> audioThreats = scanAuditoryThreats(player);
        List<AudioThreatObservation> ambientSounds = scanAmbientSounds(player);
        List<AudioThreatObservation> unseenAudioThreats = filterUnseenAudioThreats(visibleThreats, audioThreats);
        List<AmbientMobObservation> ambientMobs = scanAmbientMobs(player, world);
        updateCombatTracking(visibleThreats, audioThreats);
        boolean deadNow = isPlayerDead(player);

        if (shouldSendSnapshot()) {
            JsonObject snapshot = buildStatusSnapshot(player, world, visibleThreats, audioThreats, ambientSounds, ambientMobs);
            this.eventClient.postEvent(snapshot);
            this.lastSnapshotTick = this.tickCounter;
        }

        if (shouldSendThreatEvent(visibleThreats)) {
            JsonObject threatEvent = buildThreatApproaching(player, world, visibleThreats, audioThreats, ambientMobs);
            this.eventClient.postEvent(threatEvent);
            this.lastThreatTick = this.tickCounter;
            this.lastThreatSignature = threatSignature(visibleThreats);
        }

        if (shouldSendAudioThreatEvent(unseenAudioThreats)) {
            JsonObject audioEvent = buildHostileAudioDetected(player, world, threats, unseenAudioThreats, ambientMobs);
            this.eventClient.postEvent(audioEvent);
            this.lastAudioEventTick = this.tickCounter;
            this.lastAudioDispatchObservationTick = latestAudioObservationTick(unseenAudioThreats);
            this.lastAudioSignature = audioThreatSignature(unseenAudioThreats);
        }

        if (shouldSendAmbientMobEvent(ambientMobs, visibleThreats, unseenAudioThreats, deadNow)) {
            JsonObject ambientEvent = buildAmbientMobDetected(player, world, ambientMobs);
            this.eventClient.postEvent(ambientEvent);
            this.lastAmbientMobEventTick = this.tickCounter;
            this.lastAmbientMobSignature = ambientMobSignature(ambientMobs);
        }

        if (deadNow && !this.wasDead) {
            JsonObject deathEvent = buildPlayerDied(player, world, visibleThreats, audioThreats, ambientMobs);
            this.eventClient.postEvent(deathEvent);
            this.combatActive = false;
        } else if (shouldSendCombatEnded(visibleThreats, audioThreats, deadNow)) {
            JsonObject combatEndedEvent = buildCombatEnded(player, world, ambientMobs);
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
        this.lastAmbientMobEventTick = -1;
        this.lastAudioDispatchObservationTick = -1;
        this.lastDamageTick = -1000;
        this.lastVisualThreatObservedTick = -1000;
        this.lastAudioThreatObservedTick = -1000;
        this.lastOminousSoundObservedTick = -1000;
        this.lastExplosionObservedTick = -1000;
        this.lastRainSoundObservedTick = -1000;
        this.lastThunderSoundObservedTick = -1000;
        this.lastNearbyLightningObservedTick = -1000;
        this.lastNearbyLightningDistance = 99.0;
        this.lastEnderEyeLaunchObservedTick = -1000;
        this.structureAtPlayer = null;
        this.lastStructureProbeTick = -1000;
        this.lastCombatSignalTick = -1000;
        this.lastWardenSeenTick = -1000;
        this.lastWardenDeathSoundTick = -1000;
        this.trackedWardenEntityId = -1;
        this.lastWardenDefeatObservedTick = -1000;
        this.lastWardenEndCrystalObservedTick = -1000;
        this.lastWardenTntSetupObservedTick = -1000;
        this.lastDragonSeenTick = -1000;
        this.lastDragonDeathSoundTick = -1000;
        this.trackedDragonEntityId = -1;
        this.lastDragonDefeatObservedTick = -1000;
        this.awakenedShulkerIds.clear();
        this.lastHealth = 20.0f;
        this.lastThreatSignature = "";
        this.lastAudioSignature = "";
        this.lastAmbientMobSignature = "";
        this.lastOminousSoundKind = "";
        this.combatActive = false;
        this.wasDead = false;
        this.wasSleeping = false;
        this.respawnPointObserved = false;
        this.observedRespawnPos = null;
        this.observedRespawnDimension = null;
        this.lastDimensionId = null;
        this.pendingUserText = null;
        this.lastPlayerX = Double.NaN;
        this.lastPlayerY = Double.NaN;
        this.lastPlayerZ = Double.NaN;
        this.lastThreatDistances.clear();
        this.lastThreatSeenTicks.clear();
        this.lineOfSightStartedTicks.clear();
        this.confirmedVisibleTicks.clear();
        this.lastThreatHealths.clear();
        this.recentSoundObservations.clear();
        this.recentAmbientSoundObservations.clear();
    }

    private void resetThreatStateForDimensionChange() {
        this.lastThreatTick = -1;
        this.lastAudioEventTick = -1;
        this.lastAmbientMobEventTick = -1;
        this.lastAudioDispatchObservationTick = -1;
        this.lastDamageTick = -1000;
        this.lastVisualThreatObservedTick = -1000;
        this.lastAudioThreatObservedTick = -1000;
        this.lastOminousSoundObservedTick = -1000;
        this.lastExplosionObservedTick = -1000;
        this.lastRainSoundObservedTick = -1000;
        this.lastThunderSoundObservedTick = -1000;
        this.lastNearbyLightningObservedTick = -1000;
        this.lastNearbyLightningDistance = 99.0;
        this.lastEnderEyeLaunchObservedTick = -1000;
        this.structureAtPlayer = null;
        this.lastStructureProbeTick = -1000;
        this.lastCombatSignalTick = -1000;
        this.lastWardenSeenTick = -1000;
        this.lastWardenDeathSoundTick = -1000;
        this.trackedWardenEntityId = -1;
        this.lastWardenDefeatObservedTick = -1000;
        this.lastWardenEndCrystalObservedTick = -1000;
        this.lastWardenTntSetupObservedTick = -1000;
        this.lastDragonSeenTick = -1000;
        this.lastDragonDeathSoundTick = -1000;
        this.trackedDragonEntityId = -1;
        this.lastDragonDefeatObservedTick = -1000;
        this.awakenedShulkerIds.clear();
        this.lastThreatSignature = "";
        this.lastAudioSignature = "";
        this.lastAmbientMobSignature = "";
        this.lastOminousSoundKind = "";
        this.combatActive = false;
        this.lastPlayerX = Double.NaN;
        this.lastPlayerY = Double.NaN;
        this.lastPlayerZ = Double.NaN;
        this.lastThreatDistances.clear();
        this.lastThreatSeenTicks.clear();
        this.lineOfSightStartedTicks.clear();
        this.confirmedVisibleTicks.clear();
        this.lastThreatHealths.clear();
        this.recentSoundObservations.clear();
        this.recentAmbientSoundObservations.clear();
    }

    private void resetThreatStateForPositionJump() {
        resetThreatStateForDimensionChange();
    }

    private boolean hasLargePositionJump(double x, double y, double z) {
        double dx = x - this.lastPlayerX;
        double dy = y - this.lastPlayerY;
        double dz = z - this.lastPlayerZ;
        double thresholdSquared = LARGE_POSITION_JUMP_BLOCKS * LARGE_POSITION_JUMP_BLOCKS;
        return (dx * dx) + (dy * dy) + (dz * dz) >= thresholdSquared;
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

    private boolean shouldSendAmbientMobEvent(
        List<AmbientMobObservation> ambientMobs,
        List<ThreatObservation> visibleThreats,
        List<AudioThreatObservation> unseenAudioThreats,
        boolean deadNow
    ) {
        if (ambientMobs.isEmpty() || deadNow) {
            return false;
        }
        if (!visibleThreats.isEmpty() || !unseenAudioThreats.isEmpty()) {
            return false;
        }
        if (this.combatActive || this.tickCounter - this.lastDamageTick <= this.config.combatEndedQuietTicks) {
            return false;
        }
        int ambientIntervalTicks = Math.min(this.config.ambientMobIntervalTicks, 60);
        long sinceLastAmbientEvent = this.lastAmbientMobEventTick >= 0
            ? this.tickCounter - this.lastAmbientMobEventTick
            : Long.MAX_VALUE;
        if (sinceLastAmbientEvent < ambientIntervalTicks) {
            return false;
        }
        String signature = ambientMobSignature(ambientMobs);
        if (!signature.equals(this.lastAmbientMobSignature)) {
            return true;
        }
        return sinceLastAmbientEvent >= ambientIntervalTicks * 4L;
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
        double scanDistance = Math.max(
            Math.max(this.config.maxThreatDistance, this.config.visibleThreatDistance),
            30.0
        );
        List<Entity> entities = world.getOtherEntities(
            player,
            player.getBoundingBox().expand(scanDistance),
            this::isThreatCandidateEntity
        );

        List<ThreatObservation> threats = new ArrayList<>();
        for (Entity entity : entities) {
            if (!(entity instanceof LivingEntity living)) {
                continue;
            }
            MobDisposition disposition = classifyMobDisposition(player, living);
            if (!disposition.threatNow()) {
                continue;
            }
            double distance = Math.sqrt(player.squaredDistanceTo(entity));
            if (distance > scanDistance) {
                continue;
            }

            Vec3d entityPosition = new Vec3d(entity.getX(), entity.getY(), entity.getZ());
            String horizontal = classifyHorizontal(player, entityPosition);
            String vertical = classifyVertical(player, entityPosition);
            boolean approaching = isApproaching(entity.getUuid(), distance);
            boolean rearThreat = distance <= Math.min(this.config.rearWarningDistance, 3.0)
                && ("back".equals(horizontal) || "back_left".equals(horizontal) || "back_right".equals(horizontal));
            boolean lineOfSight = hasLineOfSight(player, world, entity);
            float currentHealth = living.getHealth();
            Float previousHealth = this.lastThreatHealths.get(entity.getUuid());
            boolean recentlyHurt = previousHealth != null && currentHealth + 0.25f < previousHealth;

            ThreatObservation observation = new ThreatObservation(
                entity.getUuid(),
                disposition.type(),
                distance,
                horizontal,
                vertical,
                approaching,
                rearThreat,
                lineOfSight,
                entity.isOnFire(),
                entity.isTouchingWater() || entity.isSubmergedInWater(),
                entity.getX(),
                entity.getY(),
                entity.getZ(),
                currentHealth,
                recentlyHurt
            );
            threats.add(observation);
            this.lastThreatDistances.put(entity.getUuid(), distance);
            this.lastThreatSeenTicks.put(entity.getUuid(), this.tickCounter);
            this.lastThreatHealths.put(entity.getUuid(), currentHealth);
            if ("warden".equals(disposition.type())) {
                this.lastWardenSeenTick = this.tickCounter;
                this.lastWardenSeenX = entity.getX();
                this.lastWardenSeenY = entity.getY();
                this.lastWardenSeenZ = entity.getZ();
                // 討伐確認のため、目の前のウォーデンを entity ID で直接追跡する
                this.trackedWardenEntityId = entity.getId();
            }
            if ("ender_dragon".equals(disposition.type())) {
                this.lastDragonSeenTick = this.tickCounter;
                this.trackedDragonEntityId = entity.getId();
            }
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

    private List<AmbientMobObservation> scanAmbientMobs(ClientPlayerEntity player, ClientWorld world) {
        List<Entity> entities = world.getOtherEntities(
            player,
            player.getBoundingBox().expand(AMBIENT_MOB_DISTANCE),
            this::isAmbientMobCandidate
        );

        List<AmbientMobObservation> mobs = new ArrayList<>();
        for (Entity entity : entities) {
            if (!(entity instanceof LivingEntity living)) {
                continue;
            }
            MobDisposition disposition = classifyMobDisposition(player, living);
            if (!disposition.ambientEligible()) {
                continue;
            }
            double distance = Math.sqrt(player.squaredDistanceTo(entity));
            if (distance > AMBIENT_MOB_DISTANCE) {
                continue;
            }
            if (!hasLineOfSight(player, world, entity)) {
                continue;
            }

            Vec3d entityPosition = new Vec3d(entity.getX(), entity.getY(), entity.getZ());
            Boolean isBaby = null;
            String profession = null;
            String villagerType = null;
            if (living instanceof VillagerEntity villager) {
                isBaby = villager.isBaby();
                profession = villagerProfessionId(villager);
                villagerType = villagerTypeId(villager);
            }
            mobs.add(
                new AmbientMobObservation(
                    entity.getUuid(),
                    disposition.type(),
                    distance,
                    classifyHorizontal(player, entityPosition),
                    classifyVertical(player, entityPosition),
                    disposition.temperament(),
                    disposition.cautionReason(),
                    isBaby,
                    profession,
                    villagerType
                )
            );
        }

        mobs.sort(Comparator.comparingDouble(AmbientMobObservation::distance));
        return mobs;
    }

    private boolean isAmbientMobCandidate(Entity entity) {
        if (!(entity instanceof LivingEntity living) || !living.isAlive()) {
            return false;
        }
        if (entity instanceof PlayerEntity) {
            return false;
        }
        // HostileEntity 継承でないモンスター（ドラゴン・ガスト・ファントム・スライム・
        // シュルカー等）を友好モブ扱いしないよう Monster インターフェースで判定する
        if (entity instanceof Monster) {
            return AMBIENT_NEUTRAL_MONSTER_IDS.contains(entityTypeName(entity));
        }
        return true;
    }

    private boolean isThreatCandidateEntity(Entity entity) {
        if (!(entity instanceof LivingEntity living) || !living.isAlive()) {
            return false;
        }
        if (entity instanceof PlayerEntity) {
            return false;
        }
        return entity instanceof Monster
            || entity instanceof BeeEntity
            || entity instanceof DolphinEntity
            || entity instanceof IronGolemEntity
            || entity instanceof LlamaEntity
            || entity instanceof PandaEntity
            || entity instanceof PolarBearEntity
            || entity instanceof WolfEntity;
    }

    private MobDisposition classifyMobDisposition(ClientPlayerEntity player, LivingEntity entity) {
        String type = entityTypeName(entity);
        if (entity instanceof ShulkerEntity shulker) {
            // 閉じたままのシュルカーは「ブロックのフリ」を尊重して気づかないフリをする。
            // 一度でも殻を開いたら（動き出したら）以後は脅威として扱う
            return new MobDisposition(type, false, isShulkerAwakened(shulker), null, "hostile");
        }
        if (entity instanceof Monster) {
            if (!AMBIENT_NEUTRAL_MONSTER_IDS.contains(type)) {
                return new MobDisposition(type, false, true, null, "hostile");
            }
            boolean hostileNow = isNeutralMobHostileToPlayer(player, entity);
            return new MobDisposition(type, !hostileNow, hostileNow, "neutral", neutralCautionReason(entity));
        }

        if (entity instanceof FoxEntity) {
            return new MobDisposition(type, true, false, "friendly", null);
        }
        if (entity instanceof GoatEntity) {
            return new MobDisposition(type, true, false, "neutral", "charge");
        }
        if (entity instanceof BeeEntity
            || entity instanceof DolphinEntity
            || entity instanceof IronGolemEntity
            || entity instanceof LlamaEntity
            || entity instanceof PandaEntity
            || entity instanceof PolarBearEntity
            || entity instanceof WolfEntity) {
            boolean hostileNow = isNeutralMobHostileToPlayer(player, entity);
            return new MobDisposition(
                type,
                !hostileNow,
                hostileNow,
                hostileNow ? null : "neutral",
                neutralCautionReason(entity)
            );
        }

        return new MobDisposition(type, true, false, "friendly", null);
    }

    private boolean isShulkerAwakened(ShulkerEntity shulker) {
        // openProgress は殻の開き具合（0.0=完全に閉じてブロックのフリ中）
        if (shulker.getOpenProgress(0.0f) > 0.0f) {
            this.awakenedShulkerIds.add(shulker.getUuid());
        }
        return this.awakenedShulkerIds.contains(shulker.getUuid());
    }

    private boolean isNeutralMobHostileToPlayer(ClientPlayerEntity player, LivingEntity entity) {
        if (entity instanceof MobEntity mob) {
            LivingEntity target = mob.getTarget();
            if (target != null && target.getUuid().equals(player.getUuid())) {
                return true;
            }
        }
        if (entity instanceof ZombifiedPiglinEntity || entity instanceof PiglinEntity) {
            return entity instanceof MobEntity mob
                && mob.getTarget() != null
                && mob.getTarget().getUuid().equals(player.getUuid());
        }
        return false;
    }

    private String neutralCautionReason(LivingEntity entity) {
        if (entity instanceof BeeEntity) {
            return "swarm";
        }
        if (entity instanceof DolphinEntity || entity instanceof WolfEntity) {
            return "retaliates";
        }
        if (entity instanceof SpiderEntity) {
            return "darkness";
        }
        if (entity instanceof LlamaEntity) {
            return "spit";
        }
        if (entity instanceof PolarBearEntity) {
            return "protective";
        }
        if (entity instanceof net.minecraft.entity.mob.DrownedEntity) {
            return "water";
        }
        if (entity instanceof PandaEntity) {
            return "temper";
        }
        if (entity instanceof PiglinEntity) {
            return "gold";
        }
        if (entity instanceof ZombifiedPiglinEntity) {
            return "provoked_only";
        }
        if (entity instanceof IronGolemEntity) {
            return "village_guard";
        }
        if (entity instanceof GoatEntity) {
            return "charge";
        }
        return null;
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
            this.lastThreatHealths.remove(uuid);
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
        expireAmbientSoundObservations();
    }

    private void expireAmbientSoundObservations() {
        while (!this.recentAmbientSoundObservations.isEmpty()) {
            SoundObservation oldest = this.recentAmbientSoundObservations.peekFirst();
            if (oldest == null || this.tickCounter - oldest.observedTick() <= SOUND_OBSERVATION_TTL_TICKS) {
                break;
            }
            this.recentAmbientSoundObservations.removeFirst();
        }
    }

    private List<AudioThreatObservation> scanAmbientSounds(ClientPlayerEntity player) {
        Map<String, AudioThreatObservation> deduped = new LinkedHashMap<>();
        for (SoundObservation observation : this.recentAmbientSoundObservations) {
            Vec3d source = new Vec3d(observation.sourceX(), observation.sourceY(), observation.sourceZ());
            double distance = Math.sqrt(player.squaredDistanceTo(source));
            if (distance > this.config.audioThreatDistance) {
                continue;
            }
            String horizontal = classifyHorizontal(player, source);
            String vertical = classifyVertical(player, source);
            String distanceBand = bucketDistance(distance);
            String certainty = distance <= 6.0 ? "medium" : "low";
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
                    true,
                    observation.observedTick()
                )
            );
        }
        List<AudioThreatObservation> ambient = new ArrayList<>(deduped.values());
        ambient.sort(Comparator.comparingInt(observation -> distanceBandRank(observation.distanceBand())));
        return ambient;
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
        List<AudioThreatObservation> audioThreats,
        List<AudioThreatObservation> ambientSounds,
        List<AmbientMobObservation> ambientMobs
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("status_snapshot", "system", "background", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        root.add("ambient_sounds", buildAmbientSounds(ambientSounds));
        attachPassiveMobs(root, ambientMobs);
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(player, world, threats, audioThreats));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildThreatApproaching(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("threat_approaching", "visual", "urgent", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        attachPassiveMobs(root, ambientMobs);
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(player, world, threats, audioThreats));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildHostileAudioDetected(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("hostile_audio_detected", "auditory", "normal", "medium");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", new JsonArray());
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        attachPassiveMobs(root, ambientMobs);
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(player, world, threats, audioThreats));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildAmbientMobDetected(
        ClientPlayerEntity player,
        ClientWorld world,
        List<AmbientMobObservation> ambientMobs
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("ambient_mob_detected", "visual", "background", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", new JsonArray());
        root.add("auditory_threats", new JsonArray());
        attachPassiveMobs(root, ambientMobs);
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(player, world, List.of(), List.of()));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject buildPlayerDied(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats,
        List<AmbientMobObservation> ambientMobs
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("player_died", "system", "urgent", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", buildVisualThreats(threats));
        root.add("auditory_threats", buildAuditoryThreats(audioThreats));
        attachPassiveMobs(root, ambientMobs);
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(player, world, threats, audioThreats));
        root.add("meta", buildMeta(resolveDeathCause(player)));
        return root;
    }

    private JsonObject buildCombatEnded(
        ClientPlayerEntity player,
        ClientWorld world,
        List<AmbientMobObservation> ambientMobs
    ) {
        JsonArray nearbyResources = buildNearbyResources(player, world);
        JsonObject root = baseEnvelope("combat_ended", "system", "normal", "high");
        root.addProperty("sequence", this.eventClient.nextSequence());
        root.add("player", buildPlayer(player, world));
        root.add("world", buildWorld(player, world));
        root.add("visual_threats", new JsonArray());
        root.add("auditory_threats", new JsonArray());
        attachPassiveMobs(root, ambientMobs);
        root.add("inventory", buildInventory(player));
        root.add("nearby_resources", nearbyResources);
        root.add("combat", buildCombat(player, world, List.of(), List.of()));
        root.add("meta", buildMeta(null));
        return root;
    }

    private JsonObject baseEnvelope(String eventName, String sourceKind, String priorityHint, String certainty) {
        JsonObject root = new JsonObject();
        root.addProperty("schema_version", DogidoBuildInfo.SCHEMA_VERSION);
        root.addProperty("game", DogidoBuildInfo.GAME);
        root.addProperty("adapter", DogidoBuildInfo.ADAPTER_NAME);
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
        JsonArray activeEffects = new JsonArray();
        for (net.minecraft.entity.effect.StatusEffectInstance effect : player.getStatusEffects()) {
            Identifier effectId = effect.getEffectType().getKey().map(key -> key.getValue()).orElse(null);
            if (effectId == null) {
                continue;
            }
            activeEffects.add(effectId.getPath());
        }
        json.add("active_status_effects", activeEffects);
        return json;
    }

    private JsonObject buildWorld(ClientPlayerEntity player, ClientWorld world) {
        BlockPos pos = player.getBlockPos();
        JsonObject json = new JsonObject();
        long timeOfDay = world.getTimeOfDay() % 24000L;
        boolean submerged = isWaterContext(player);

        if (usesDayNightCycle(world)) {
            json.addProperty("time_of_day", (int) timeOfDay);
            json.addProperty("time_phase", classifyTimePhase(timeOfDay));
        }
        json.addProperty("weather", classifyWeather(world));
        json.addProperty("biome", resolveBiomeName(world, pos));
        String structureName = this.structureAtPlayer;
        if (structureName != null && !structureName.isBlank()) {
            json.addProperty("structure", structureName);
        }
        json.addProperty("local_light", world.getLightLevel(pos));
        json.addProperty("sky_visible", world.isSkyVisible(pos));
        double ceilingHeight = estimateCeilingHeight(world, pos);
        String overheadCoverType = classifyOverheadCover(world, pos);
        double enclosureScore = estimateEnclosureScore(world, pos);
        int nearbyDoorCount = countNearbyDoors(world, pos);
        int openDoorCount = countOpenDoors(world, pos);
        int nearbyBedCount = countNearbyBeds(world, pos);
        int nearbySleepingPeopleCount = countNearbySleepingPeople(world, player);
        int draftyOpeningCount = countDraftyOpenings(world, pos);
        int submergedDepthBlocks = estimateSubmergedDepthBlocks(world, pos, submerged);
        int nearbyFireflyBushCount = countNearbyFireflyBushes(world, pos);
        int cardinalWallCount = countCardinalWalls(world, pos);
        int doubleHeightOpenSideCount = countDoubleHeightOpenSides(world, pos);
        int nearbyLightSourceCount = countNearbyLightSources(world, pos);
        double nearestLightSourceDistance = estimateNearestLightSourceDistance(world, pos);
        int nearbyDamagingLightSourceCount = countNearbyDamagingLightSources(world, pos);
        double nearestDamagingLightSourceDistance = estimateNearestDamagingLightSourceDistance(world, pos);
        boolean standingOnMagmaBlock = isStandingOnMagmaBlock(world, player);
        String bossOmenKind = detectBossOmenKind(player, world, pos);
        long ominousSoundRecentMs = ticksSince(this.lastOminousSoundObservedTick);
        long rainSoundRecentMs = ticksSince(this.lastRainSoundObservedTick);
        long thunderSoundRecentMs = ticksSince(this.lastThunderSoundObservedTick);
        long nearbyLightningRecentMs = ticksSince(this.lastNearbyLightningObservedTick);
        Double respawnDistance = estimateRespawnDistance(world, pos);
        json.addProperty("ceiling_height", round(ceilingHeight));
        json.addProperty("overhead_cover_type", overheadCoverType);
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
        json.addProperty("double_height_open_side_count", doubleHeightOpenSideCount);
        json.addProperty("standing_on_magma_block", standingOnMagmaBlock);
        if (bossOmenKind != null) {
            json.addProperty("boss_omen_kind", bossOmenKind);
        }
        if (!this.lastOminousSoundKind.isBlank() && ominousSoundRecentMs <= OMINOUS_SOUND_TTL_TICKS * 50L) {
            json.addProperty("ominous_sound_kind", this.lastOminousSoundKind);
            json.addProperty("ominous_sound_recent_ms", ominousSoundRecentMs);
        }
        if (rainSoundRecentMs <= WEATHER_SOUND_TTL_TICKS * 50L) {
            json.addProperty("rain_sound_recent_ms", rainSoundRecentMs);
        }
        if (thunderSoundRecentMs <= WEATHER_SOUND_TTL_TICKS * 50L) {
            json.addProperty("thunder_sound_recent_ms", thunderSoundRecentMs);
        }
        if (nearbyLightningRecentMs <= LIGHTNING_STRIKE_TTL_TICKS * 50L && this.lastNearbyLightningDistance <= 30.0) {
            json.addProperty("nearby_lightning_strike_recent_ms", nearbyLightningRecentMs);
            json.addProperty("nearby_lightning_strike_distance", round(this.lastNearbyLightningDistance));
        }
        long enderEyeLaunchRecentMs = ticksSince(this.lastEnderEyeLaunchObservedTick);
        if (enderEyeLaunchRecentMs <= ENDER_EYE_LAUNCH_TTL_TICKS * 50L) {
            json.addProperty("ender_eye_launch_recent_ms", enderEyeLaunchRecentMs);
        }
        if (respawnDistance != null) {
            json.addProperty("respawn_distance", round(respawnDistance));
        }
        json.addProperty(
            "safe_zone_with_door",
            isSafeZoneWithDoor(world, pos, submerged, nearbyDoorCount, enclosureScore, ceilingHeight)
        );

        int connectedDarkVolume = estimateConnectedDarkVolume(world, pos);
        double nearestDarkSpawnDistance = estimateNearestDarkSpawnDistance(world, pos);
        double darknessScore = estimateDangerDarkness(
            world,
            pos,
            overheadCoverType,
            enclosureScore,
            connectedDarkVolume,
            nearestDarkSpawnDistance,
            nearbyLightSourceCount,
            nearestLightSourceDistance,
            cardinalWallCount,
            ceilingHeight
        );

        json.addProperty("enclosure_score", round(enclosureScore));
        json.addProperty("connected_dark_volume", connectedDarkVolume);
        json.addProperty("nearest_dark_spawn_distance", round(nearestDarkSpawnDistance));
        json.addProperty("nearby_light_source_count", nearbyLightSourceCount);
        json.addProperty("nearest_light_source_distance", round(nearestLightSourceDistance));
        json.addProperty("nearby_damaging_light_source_count", nearbyDamagingLightSourceCount);
        json.addProperty("nearest_damaging_light_source_distance", round(nearestDamagingLightSourceDistance));
        json.addProperty("danger_darkness_score", round(darknessScore));

        String[] portalInfo = scanNearbyPortals(world, pos);
        if (portalInfo != null) {
            json.addProperty("nearby_portal_type", portalInfo[0]);
            json.addProperty("nearby_portal_distance", round(Double.parseDouble(portalInfo[1])));
        }

        double frameDistance = scanNearbyEndPortalFrame(world, pos);
        if (frameDistance >= 0) {
            json.addProperty("nearby_end_portal_frame_distance", round(frameDistance));
        }

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

    private JsonArray buildAmbientSounds(List<AudioThreatObservation> ambientSounds) {
        JsonArray array = new JsonArray();
        int limit = Math.min(ambientSounds.size(), 6);
        for (int index = 0; index < limit; index += 1) {
            AudioThreatObservation sound = ambientSounds.get(index);
            JsonObject entry = new JsonObject();
            entry.addProperty("type", sound.label());
            entry.addProperty("source_id", sound.sourceId());
            entry.addProperty("sound_event", sound.soundEvent());

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", sound.horizontalDirection());
            direction.addProperty("vertical", sound.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("distance_band", sound.distanceBand());
            entry.addProperty("certainty", sound.certainty());
            array.add(entry);
        }
        return array;
    }

    private JsonArray buildPassiveMobs(List<AmbientMobObservation> ambientMobs) {
        JsonArray array = new JsonArray();
        int limit = Math.min(ambientMobs.size(), 4);
        for (int index = 0; index < limit; index += 1) {
            AmbientMobObservation mob = ambientMobs.get(index);
            JsonObject entry = new JsonObject();
            entry.addProperty("type", mob.type());
            entry.addProperty("distance", round(mob.distance()));

            JsonObject direction = new JsonObject();
            direction.addProperty("horizontal", mob.horizontalDirection());
            direction.addProperty("vertical", mob.verticalRelation());
            entry.add("direction", direction);

            entry.addProperty("certainty", "high");
            if (mob.temperament() != null && !mob.temperament().isBlank()) {
                entry.addProperty("temperament", mob.temperament());
            }
            if (mob.cautionReason() != null && !mob.cautionReason().isBlank()) {
                entry.addProperty("caution_reason", mob.cautionReason());
            }
            if (mob.isBaby() != null) {
                entry.addProperty("is_baby", mob.isBaby());
            }
            if (mob.profession() != null && !mob.profession().isBlank()) {
                entry.addProperty("profession", mob.profession());
            }
            if (mob.villagerType() != null && !mob.villagerType().isBlank()) {
                entry.addProperty("villager_type", mob.villagerType());
            }
            array.add(entry);
        }
        return array;
    }

    private void attachPassiveMobs(JsonObject root, List<AmbientMobObservation> ambientMobs) {
        JsonArray passiveMobs = buildPassiveMobs(ambientMobs);
        root.add("passive_mobs", passiveMobs);
        root.add("peaceful_mobs", passiveMobs.deepCopy());
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
                    BlockState state = world.getBlockState(sample);
                    String resourceName = nearbyResourceNameForBlock(state);
                    if (resourceName == null) {
                        continue;
                    }
                    if (!isAirExposedNearbyResource(world, sample)) {
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

    private boolean isAirExposedNearbyResource(ClientWorld world, BlockPos pos) {
        for (Direction direction : Direction.values()) {
            if (world.getBlockState(pos.offset(direction)).isAir()) {
                return true;
            }
        }
        return false;
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

    private JsonObject buildCombat(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats,
        List<AudioThreatObservation> audioThreats
    ) {
        JsonObject json = new JsonObject();
        json.addProperty("recent_damage_ms", ticksSince(this.lastDamageTick));
        json.addProperty("recent_hostile_visual_ms", ticksSince(this.lastVisualThreatObservedTick));
        json.addProperty("recent_hostile_audio_ms", ticksSince(this.lastAudioThreatObservedTick));
        json.addProperty("hostiles_within_7", countThreatsWithin(threats, 7.0));
        json.addProperty("hostiles_within_10", countThreatsWithin(threats, 10.0));
        json.addProperty("hostiles_within_30_ground", countGroundThreatsWithin(threats, 30.0));
        json.addProperty("combat_active_hint", this.combatActive || !threats.isEmpty() || !audioThreats.isEmpty());
        int nearbyExperienceOrbCount = countNearbyExperienceOrbs(world, player);
        json.addProperty("nearby_experience_orb_count", nearbyExperienceOrbCount);
        ThreatObservation warden = nearestThreatOfType(threats, "warden");
        if (warden != null) {
            // ウォーデン周辺とプレイヤー周辺で同じ個体を二重に数えないよう max を取る
            int nearbyIronGolemCount = Math.max(
                countNearbyEntityType(world, warden, "iron_golem", 20.0),
                countNearbyEntityTypeAroundPlayer(world, player, "iron_golem", 20.0));
            int nearbyEndCrystalCount = Math.max(
                countNearbyEntityType(world, warden, "end_crystal", 32.0),
                countNearbyEntityTypeAroundPlayer(world, player, "end_crystal", 32.0));
            int nearbyTntMinecartCount = Math.max(
                countNearbyEntityType(world, warden, "tnt_minecart", 28.0)
                    + countNearbyEntityType(world, warden, "tnt", 28.0),
                countNearbyEntityTypeAroundPlayer(world, player, "tnt_minecart", 28.0)
                    + countNearbyEntityTypeAroundPlayer(world, player, "tnt", 28.0));
            long recentExplosionMs = ticksSince(this.lastExplosionObservedTick);
            json.addProperty("warden_recently_hurt", warden.recentlyHurt());
            json.addProperty("warden_ranged_trap_active", isWardenRangedTrapActive(player, warden));
            json.addProperty("warden_nearby_iron_golem_count", nearbyIronGolemCount);
            json.addProperty(
                "warden_end_crystal_bombardment_active",
                recentExplosionMs <= 1500L
                    && (
                        nearbyEndCrystalCount > 0
                        || ticksSince(this.lastWardenEndCrystalObservedTick) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
                    )
            );
            json.addProperty("warden_nearby_end_crystal_count", nearbyEndCrystalCount);
            json.addProperty(
                "warden_tnt_minecart_setup_active",
                nearbyTntMinecartCount > 0
                    || ticksSince(this.lastWardenTntSetupObservedTick) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
            );
            json.addProperty("warden_nearby_tnt_minecart_count", nearbyTntMinecartCount);
        } else if (this.lastWardenSeenTick >= 0 && this.tickCounter - this.lastWardenSeenTick <= 200) {
            // 同上: プレイヤー周辺と最終目撃地点周辺の二重計上を避ける
            int nearbyIronGolemCount = Math.max(
                countNearbyEntityTypeAroundPlayer(world, player, "iron_golem", 20.0),
                countNearbyEntityTypeAroundPoint(world, this.lastWardenSeenX, this.lastWardenSeenY, this.lastWardenSeenZ, "iron_golem", 20.0));
            int nearbyEndCrystalCount = Math.max(
                countNearbyEntityTypeAroundPlayer(world, player, "end_crystal", 32.0),
                countNearbyEntityTypeAroundPoint(world, this.lastWardenSeenX, this.lastWardenSeenY, this.lastWardenSeenZ, "end_crystal", 32.0));
            int nearbyTntMinecartCount = Math.max(
                countNearbyEntityTypeAroundPlayer(world, player, "tnt_minecart", 28.0)
                    + countNearbyEntityTypeAroundPlayer(world, player, "tnt", 28.0),
                countNearbyEntityTypeAroundPoint(world, this.lastWardenSeenX, this.lastWardenSeenY, this.lastWardenSeenZ, "tnt_minecart", 28.0)
                    + countNearbyEntityTypeAroundPoint(world, this.lastWardenSeenX, this.lastWardenSeenY, this.lastWardenSeenZ, "tnt", 28.0));
            long recentExplosionMs = ticksSince(this.lastExplosionObservedTick);
            json.addProperty("warden_nearby_iron_golem_count", nearbyIronGolemCount);
            json.addProperty(
                "warden_end_crystal_bombardment_active",
                recentExplosionMs <= 1500L
                    && (
                        nearbyEndCrystalCount > 0
                        || ticksSince(this.lastWardenEndCrystalObservedTick) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
                    )
            );
            json.addProperty("warden_nearby_end_crystal_count", nearbyEndCrystalCount);
            json.addProperty(
                "warden_tnt_minecart_setup_active",
                nearbyTntMinecartCount > 0
                    || ticksSince(this.lastWardenTntSetupObservedTick) <= WARDEN_SPECIAL_LATCH_TICKS * 50L
            );
            json.addProperty("warden_nearby_tnt_minecart_count", nearbyTntMinecartCount);
            // 討伐直後（姿が消えてから10秒以内）の combat_ended でも
            // 討伐確認が届くようにここでも送る
            json.addProperty("warden_defeat_confirmed", isRecentWardenDefeatConfirmed(world));
        } else {
            json.addProperty("warden_defeat_confirmed", isRecentWardenDefeatConfirmed(world));
        }
        appendDragonCombatInfo(json, player, world);
        return json;
    }

    private void observeWardenSpecialSetups(
        ClientPlayerEntity player,
        ClientWorld world,
        List<ThreatObservation> threats
    ) {
        ThreatObservation warden = nearestThreatOfType(threats, "warden");
        boolean recentWardenContext = warden != null
            || (this.lastWardenSeenTick >= 0 && this.tickCounter - this.lastWardenSeenTick <= 200);
        if (!recentWardenContext) {
            return;
        }

        int nearbyEndCrystalCount = countNearbyEntityTypeAroundPlayer(world, player, "end_crystal", 32.0);
        int nearbyTntMinecartCount = countNearbyEntityTypeAroundPlayer(world, player, "tnt_minecart", 28.0)
            + countNearbyEntityTypeAroundPlayer(world, player, "tnt", 28.0);
        if (warden != null) {
            nearbyEndCrystalCount += countNearbyEntityType(world, warden, "end_crystal", 32.0);
            nearbyTntMinecartCount += countNearbyEntityType(world, warden, "tnt_minecart", 28.0);
            nearbyTntMinecartCount += countNearbyEntityType(world, warden, "tnt", 28.0);
        } else if (this.lastWardenSeenTick >= 0 && this.tickCounter - this.lastWardenSeenTick <= 200) {
            nearbyEndCrystalCount += countNearbyEntityTypeAroundPoint(
                world,
                this.lastWardenSeenX,
                this.lastWardenSeenY,
                this.lastWardenSeenZ,
                "end_crystal",
                32.0
            );
            nearbyTntMinecartCount += countNearbyEntityTypeAroundPoint(
                world,
                this.lastWardenSeenX,
                this.lastWardenSeenY,
                this.lastWardenSeenZ,
                "tnt_minecart",
                28.0
            );
            nearbyTntMinecartCount += countNearbyEntityTypeAroundPoint(
                world,
                this.lastWardenSeenX,
                this.lastWardenSeenY,
                this.lastWardenSeenZ,
                "tnt",
                28.0
            );
        }

        if (nearbyEndCrystalCount > 0) {
            this.lastWardenEndCrystalObservedTick = this.tickCounter;
        }
        if (nearbyTntMinecartCount > 0) {
            this.lastWardenTntSetupObservedTick = this.tickCounter;
        }
    }

    private JsonObject buildMeta(String deathCause) {
        JsonObject json = new JsonObject();
        json.addProperty("adapter_build", DogidoBuildInfo.ADAPTER_BUILD);
        json.addProperty("profile_name", DogidoBuildInfo.PROFILE_NAME);
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

    private int countGroundThreatsWithin(List<ThreatObservation> threats, double distance) {
        int count = 0;
        for (ThreatObservation threat : threats) {
            if (threat.distance() > distance || isFlyingThreatType(threat.type())) {
                continue;
            }
            count += 1;
        }
        return count;
    }

    private ThreatObservation nearestThreatOfType(List<ThreatObservation> threats, String hostileType) {
        for (ThreatObservation threat : threats) {
            if (hostileType.equals(threat.type())) {
                return threat;
            }
        }
        return null;
    }

    private int countNearbyEntityType(ClientWorld world, ThreatObservation origin, String entityType, double radius) {
        int count = 0;
        for (Entity entity : world.getOtherEntities(
            null,
            new net.minecraft.util.math.Box(
                origin.x() - radius, origin.y() - radius, origin.z() - radius,
                origin.x() + radius, origin.y() + radius, origin.z() + radius
            )
        )) {
            if (!entityType.equals(entityTypeName(entity))) {
                continue;
            }
            double dx = entity.getX() - origin.x();
            double dy = entity.getY() - origin.y();
            double dz = entity.getZ() - origin.z();
            if (Math.sqrt(dx * dx + dy * dy + dz * dz) <= radius) {
                count += 1;
            }
        }
        return count;
    }

    private int countNearbyEntityTypeAroundPlayer(
        ClientWorld world,
        ClientPlayerEntity player,
        String entityType,
        double radius
    ) {
        int count = 0;
        for (Entity entity : world.getOtherEntities(
            null,
            player.getBoundingBox().expand(radius, radius, radius)
        )) {
            if (entityType.equals(entityTypeName(entity))) {
                count += 1;
            }
        }
        return count;
    }

    private int countNearbyEntityTypeAroundPoint(
        ClientWorld world,
        double centerX,
        double centerY,
        double centerZ,
        String entityType,
        double radius
    ) {
        int count = 0;
        for (Entity entity : world.getOtherEntities(
            null,
            new net.minecraft.util.math.Box(
                centerX - radius, centerY - radius, centerZ - radius,
                centerX + radius, centerY + radius, centerZ + radius
            )
        )) {
            if (!entityType.equals(entityTypeName(entity))) {
                continue;
            }
            double dx = entity.getX() - centerX;
            double dy = entity.getY() - centerY;
            double dz = entity.getZ() - centerZ;
            if (Math.sqrt(dx * dx + dy * dy + dz * dz) <= radius) {
                count += 1;
            }
        }
        return count;
    }

    private int countNearbyExperienceOrbs(ClientWorld world, ClientPlayerEntity player) {
        int count = 0;
        for (Entity entity : world.getOtherEntities(
            null,
            player.getBoundingBox().expand(24.0, 12.0, 24.0)
        )) {
            if ("experience_orb".equals(entityTypeName(entity))) {
                count += 1;
            }
        }
        return count;
    }

    private void recordWardenDeathSound(String soundEventId) {
        if (soundEventId != null && soundEventId.contains("warden") && soundEventId.contains("death")) {
            this.lastWardenDeathSoundTick = this.tickCounter;
        }
    }

    private void recordDragonDeathSound(String soundEventId) {
        if (soundEventId != null && soundEventId.contains("ender_dragon") && soundEventId.contains("death")) {
            this.lastDragonDeathSoundTick = this.tickCounter;
        }
    }

    private void observeTrackedDragonDefeat(ClientWorld world) {
        if (this.trackedDragonEntityId < 0) {
            return;
        }
        Entity entity = world.getEntityById(this.trackedDragonEntityId);
        if (!(entity instanceof LivingEntity living)) {
            return;
        }
        // 死亡アニメーション（DYINGフェーズ）開始時点で討伐とみなす
        boolean dyingPhase = entity instanceof EnderDragonEntity dragon
            && dragon.getPhaseManager().getCurrent().getType() == PhaseType.DYING;
        if (living.isDead() || living.getHealth() <= 0.0f || living.deathTime > 0 || dyingPhase) {
            this.lastDragonDefeatObservedTick = this.tickCounter;
            this.trackedDragonEntityId = -1;
        }
    }

    private boolean isRecentDragonDefeatConfirmed() {
        if (this.lastDragonSeenTick < 0 || this.tickCounter - this.lastDragonSeenTick > DRAGON_DEFEAT_WINDOW_TICKS) {
            return false;
        }
        if (this.lastDragonDefeatObservedTick >= 0
            && this.tickCounter - this.lastDragonDefeatObservedTick <= DRAGON_DEFEAT_WINDOW_TICKS) {
            return true;
        }
        return this.lastDragonDeathSoundTick >= 0
            && this.tickCounter - this.lastDragonDeathSoundTick <= DRAGON_DEFEAT_WINDOW_TICKS;
    }

    private EnderDragonEntity findNearestDragon(ClientPlayerEntity player, ClientWorld world) {
        EnderDragonEntity nearest = null;
        double nearestDistance = DRAGON_SCAN_RADIUS;
        for (Entity entity : world.getOtherEntities(
            player,
            player.getBoundingBox().expand(DRAGON_SCAN_RADIUS),
            candidate -> candidate instanceof EnderDragonEntity
        )) {
            double distance = Math.sqrt(player.squaredDistanceTo(entity));
            if (distance <= nearestDistance) {
                nearest = (EnderDragonEntity) entity;
                nearestDistance = distance;
            }
        }
        return nearest;
    }

    private String dragonPhaseName(EnderDragonEntity dragon) {
        PhaseType<?> type = dragon.getPhaseManager().getCurrent().getType();
        if (type == PhaseType.STRAFE_PLAYER) {
            return "strafe_player";
        }
        if (type == PhaseType.LANDING_APPROACH) {
            return "landing_approach";
        }
        if (type == PhaseType.LANDING) {
            return "landing";
        }
        if (type == PhaseType.TAKEOFF) {
            return "takeoff";
        }
        if (type == PhaseType.SITTING_FLAMING) {
            return "sitting_flaming";
        }
        if (type == PhaseType.SITTING_SCANNING) {
            return "sitting_scanning";
        }
        if (type == PhaseType.SITTING_ATTACKING) {
            return "sitting_attacking";
        }
        if (type == PhaseType.CHARGING_PLAYER) {
            return "charging_player";
        }
        if (type == PhaseType.DYING) {
            return "dying";
        }
        if (type == PhaseType.HOVER) {
            return "hover";
        }
        return "holding_pattern";
    }

    private void appendDragonCombatInfo(JsonObject json, ClientPlayerEntity player, ClientWorld world) {
        String dimensionId = world.getRegistryKey().getValue().toString();
        boolean dragonPossible = "minecraft:the_end".equals(dimensionId)
            || (this.lastDragonSeenTick >= 0 && this.tickCounter - this.lastDragonSeenTick <= DRAGON_DEFEAT_WINDOW_TICKS);
        if (!dragonPossible) {
            return;
        }
        EnderDragonEntity dragon = findNearestDragon(player, world);
        if (dragon != null) {
            this.lastDragonSeenTick = this.tickCounter;
            this.trackedDragonEntityId = dragon.getId();
            Vec3d dragonPosition = new Vec3d(dragon.getX(), dragon.getY(), dragon.getZ());
            json.addProperty("dragon_phase", dragonPhaseName(dragon));
            json.addProperty("dragon_distance", round(Math.sqrt(player.squaredDistanceTo(dragonPosition))));
            json.addProperty("dragon_horizontal", classifyHorizontal(player, dragonPosition));
            json.addProperty("dragon_vertical", classifyVertical(player, dragonPosition));
            json.addProperty(
                "end_crystal_count",
                countNearbyEntityTypeAroundPlayer(world, player, "end_crystal", DRAGON_SCAN_RADIUS)
            );
        }
        if (this.lastDragonSeenTick >= 0 && this.tickCounter - this.lastDragonSeenTick <= DRAGON_DEFEAT_WINDOW_TICKS) {
            json.addProperty("dragon_defeat_confirmed", isRecentDragonDefeatConfirmed());
        }
    }

    private void observeTrackedWardenDefeat(ClientWorld world) {
        if (this.trackedWardenEntityId < 0) {
            return;
        }
        Entity entity = world.getEntityById(this.trackedWardenEntityId);
        if (!(entity instanceof LivingEntity living)) {
            return;
        }
        // HP0 / isDead / 死亡アニメーション進行中を「討伐」とみなす。
        // 地面に潜って消える despawn は HP が残ったまま entity が消えるので、ここには引っかからない。
        if (living.isDead() || living.getHealth() <= 0.0f || living.deathTime > 0) {
            this.lastWardenDefeatObservedTick = this.tickCounter;
            this.trackedWardenEntityId = -1;
        }
    }

    private boolean isRecentWardenDefeatConfirmed(ClientWorld world) {
        if (this.lastWardenSeenTick < 0 || this.tickCounter - this.lastWardenSeenTick > 400) {
            return false;
        }
        // 追跡中の entity の死亡を直接観測したのが最優先（キル手段を問わず確実）
        if (this.lastWardenDefeatObservedTick >= 0
            && this.tickCounter - this.lastWardenDefeatObservedTick <= 400) {
            return true;
        }
        // 死亡音はフォールバック。XPオーブはプレイヤーキル時しか落ちない。
        if (this.lastWardenDeathSoundTick >= 0
            && this.tickCounter - this.lastWardenDeathSoundTick <= 400) {
            return true;
        }
        for (Entity entity : world.getOtherEntities(
            null,
            new net.minecraft.util.math.Box(
                this.lastWardenSeenX - 20.0, this.lastWardenSeenY - 12.0, this.lastWardenSeenZ - 20.0,
                this.lastWardenSeenX + 20.0, this.lastWardenSeenY + 12.0, this.lastWardenSeenZ + 20.0
            )
        )) {
            if ("experience_orb".equals(entityTypeName(entity))) {
                return true;
            }
        }
        return false;
    }

    private boolean isWardenRangedTrapActive(ClientPlayerEntity player, ThreatObservation warden) {
        String heldItemId = Registries.ITEM.getId(player.getMainHandStack().getItem()).getPath();
        boolean rangedWeapon = "bow".equals(heldItemId) || "crossbow".equals(heldItemId);
        if (!rangedWeapon) {
            return false;
        }
        return player.getY() - warden.y() >= 5.0 && warden.distance() >= 8.0;
    }

    private boolean isFlyingThreatType(String hostileType) {
        return "blaze".equals(hostileType)
            || "ender_dragon".equals(hostileType)
            || "ghast".equals(hostileType)
            || "phantom".equals(hostileType)
            || "vex".equals(hostileType)
            || "wither".equals(hostileType);
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

    private void probeStructureAtPlayer(MinecraftClient client, ClientPlayerEntity player, ClientWorld world) {
        if (this.tickCounter - this.lastStructureProbeTick < this.config.snapshotIntervalTicks) {
            return;
        }
        this.lastStructureProbeTick = this.tickCounter;
        MinecraftServer server = client.getServer();
        if (server == null) {
            // リモートサーバー接続時はクライアントから構造物情報を取得できない
            this.structureAtPlayer = null;
            return;
        }
        BlockPos pos = player.getBlockPos();
        RegistryKey<World> worldKey = world.getRegistryKey();
        // 構造物参照はサーバー側データなので、統合サーバーのスレッドで照会して結果だけ受け取る
        server.execute(() -> {
            ServerWorld serverWorld = server.getWorld(worldKey);
            if (serverWorld == null) {
                this.structureAtPlayer = null;
                return;
            }
            // ピース単位の内包判定（バニラの実績判定と同じ基準）
            StructureStart start = serverWorld.getStructureAccessor().getStructureContaining(pos, entry -> true);
            if (start == null || !start.hasChildren()) {
                this.structureAtPlayer = null;
                return;
            }
            Identifier structureId = serverWorld.getRegistryManager()
                .getOrThrow(RegistryKeys.STRUCTURE)
                .getId(start.getStructure());
            this.structureAtPlayer = structureId == null ? null : structureId.getPath();
        });
    }

    private void onGlobalSoundPacket(PlaySoundS2CPacket packet) {
        MinecraftClient client = MinecraftClient.getInstance();
        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            return;
        }
        String soundEventId = soundEventId(packet.getSound());
        recordWardenDeathSound(soundEventId);
        recordDragonDeathSound(soundEventId);
        String ominousKind = classifyOminousSoundKind(soundEventId);
        if (ominousKind != null) {
            recordOminousSoundObservation(ominousKind);
        }
        String weatherSoundKind = classifyWeatherSoundKind(soundEventId);
        if (weatherSoundKind != null) {
            recordWeatherSoundObservation(weatherSoundKind);
        }
        if (isExplosionSound(soundEventId)) {
            this.lastExplosionObservedTick = this.tickCounter;
        }
        if (soundEventId.contains("ender_eye.launch")) {
            // プレイヤー近傍の投擲音のみ拾う（マルチで他人の投擲を実況しない）
            double launchDx = packet.getX() - player.getX();
            double launchDy = packet.getY() - player.getY();
            double launchDz = packet.getZ() - player.getZ();
            double launchDistanceSquared = (launchDx * launchDx) + (launchDy * launchDy) + (launchDz * launchDz);
            if (launchDistanceSquared <= ENDER_EYE_LAUNCH_MATCH_RADIUS * ENDER_EYE_LAUNCH_MATCH_RADIUS) {
                this.lastEnderEyeLaunchObservedTick = this.tickCounter;
            }
        }
        Vec3d source = new Vec3d(packet.getX(), packet.getY(), packet.getZ());
        if (packet.getCategory() != SoundCategory.HOSTILE) {
            // 村人などの NEUTRAL 音。戦闘トラックには載せない。
            if (packet.getCategory() == SoundCategory.NEUTRAL
                || ambientLabelFromSoundEvent(soundEventId) != null) {
                tryRecordAmbientFromGlobalSound(player, world, soundEventId, source);
            }
            return;
        }
        String defaultHostileLabel = hostileLabelFromSoundEvent(soundEventId);
        if (defaultHostileLabel == null) {
            return;
        }

        SoundSourceResolution resolution = resolveGlobalHostileSoundSource(player, world, source, defaultHostileLabel, soundEventId);
        if (resolution == null) {
            return;
        }
        recordSoundObservation(
            player,
            world,
            soundEventId,
            resolution.hostileLabel(),
            source,
            resolution.spokenNameAllowed(),
            resolution.sourceId()
        );
    }

    private SoundSourceResolution resolveGlobalHostileSoundSource(
        ClientPlayerEntity player,
        ClientWorld world,
        Vec3d source,
        String defaultHostileLabel,
        String soundEventId
    ) {
        LivingEntity nearest = null;
        double nearestDistance = GLOBAL_SOUND_SOURCE_MATCH_RADIUS;
        boolean calmNeutralSourceNearby = false;
        for (Entity entity : world.getOtherEntities(null, new net.minecraft.util.math.Box(
            source.x - GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.y - GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.z - GLOBAL_SOUND_SOURCE_MATCH_RADIUS,
            source.x + GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.y + GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.z + GLOBAL_SOUND_SOURCE_MATCH_RADIUS
        ))) {
            if (!(entity instanceof LivingEntity living) || !living.isAlive()) {
                continue;
            }
            MobDisposition disposition = classifyMobDisposition(player, living);
            double dx = entity.getX() - source.x;
            double dy = entity.getY() - source.y;
            double dz = entity.getZ() - source.z;
            double distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
            if (!disposition.threatNow()) {
                if (disposition.ambientEligible() && likelyMatchesNeutralHostileSound(defaultHostileLabel, disposition.type())) {
                    calmNeutralSourceNearby = true;
                }
                continue;
            }
            if (distance < nearestDistance) {
                nearest = living;
                nearestDistance = distance;
            }
        }
        if (nearest == null) {
            if (calmNeutralSourceNearby) {
                return null;
            }
            return new SoundSourceResolution(
                defaultHostileLabel,
                resolveSoundSourceId(world, source, defaultHostileLabel, soundEventId),
                true
            );
        }
        String nearestType = entityTypeName(nearest);
        if (nearestType == null || nearestType.isBlank()) {
            return new SoundSourceResolution(
                defaultHostileLabel,
                nearest.getUuid().toString(),
                true
            );
        }
        return new SoundSourceResolution(nearestType, nearest.getUuid().toString(), true);
    }

    private boolean likelyMatchesNeutralHostileSound(String hostileLabel, String entityType) {
        if (hostileLabel.equals(entityType)) {
            return true;
        }
        if ("zombie".equals(hostileLabel) && "zombified_piglin".equals(entityType)) {
            return true;
        }
        return "spider".equals(hostileLabel) && "cave_spider".equals(entityType);
    }

    private void onEntitySoundPacket(PlaySoundFromEntityS2CPacket packet) {
        MinecraftClient client = MinecraftClient.getInstance();
        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            return;
        }
        String soundEventId = soundEventId(packet.getSound());
        recordWardenDeathSound(soundEventId);
        recordDragonDeathSound(soundEventId);
        String ominousKind = classifyOminousSoundKind(soundEventId);
        if (ominousKind != null) {
            recordOminousSoundObservation(ominousKind);
        }
        if (isExplosionSound(soundEventId)) {
            this.lastExplosionObservedTick = this.tickCounter;
        }
        Entity entity = world.getEntityById(packet.getEntityId());
        if (packet.getCategory() != SoundCategory.HOSTILE) {
            if (entity instanceof LivingEntity living
                && living.isAlive()
                && !(entity instanceof PlayerEntity)
                && !classifyMobDisposition(player, living).threatNow()
                && (packet.getCategory() == SoundCategory.NEUTRAL
                    || ambientLabelFromSoundEvent(soundEventId) != null
                    || isAmbientMobCandidate(entity))) {
                String type = entityTypeName(living);
                if (type == null || type.isBlank()) {
                    type = ambientLabelFromSoundEvent(soundEventId);
                }
                if (type != null && !type.isBlank()) {
                    recordAmbientSoundObservation(
                        player,
                        soundEventId,
                        type,
                        new Vec3d(living.getX(), living.getY(), living.getZ()),
                        living.getUuid().toString()
                    );
                }
            }
            return;
        }

        if (!(entity instanceof LivingEntity hostileEntity) || !(entity instanceof Monster) || !hostileEntity.isAlive()) {
            String fallbackLabel = hostileLabelFromSoundEvent(soundEventId);
            if (fallbackLabel == null) {
                return;
            }
            if (entity != null) {
                recordSoundObservation(
                    player,
                    world,
                    soundEventId,
                    fallbackLabel,
                    new Vec3d(entity.getX(), entity.getY(), entity.getZ()),
                    false,
                    entity.getUuid().toString()
                );
            }
            return;
        }
        if (!classifyMobDisposition(player, hostileEntity).threatNow()) {
            // 敵対モブ音カテゴリでも、いまは中立の個体は ambient 側へ
            String type = entityTypeName(hostileEntity);
            if (type != null && !type.isBlank()) {
                recordAmbientSoundObservation(
                    player,
                    soundEventId,
                    type,
                    new Vec3d(hostileEntity.getX(), hostileEntity.getY(), hostileEntity.getZ()),
                    hostileEntity.getUuid().toString()
                );
            }
            return;
        }

        recordSoundObservation(
            player,
            world,
            soundEventId,
            entityTypeName(hostileEntity),
            new Vec3d(hostileEntity.getX(), hostileEntity.getY(), hostileEntity.getZ()),
            true,
            hostileEntity.getUuid().toString()
        );
    }

    private void recordSoundObservation(
        ClientPlayerEntity player,
        ClientWorld world,
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
        if (!shouldObserveOccludedAudio(player, world, source)) {
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

    private void recordAmbientSoundObservation(
        ClientPlayerEntity player,
        String soundEventId,
        String entityType,
        Vec3d source,
        String sourceId
    ) {
        double distance = Math.sqrt(player.squaredDistanceTo(source));
        if (distance > this.config.audioThreatDistance) {
            return;
        }
        String id = sourceId == null || sourceId.isBlank()
            ? "ambient:" + entityType + ":" + Math.round(source.x) + ":" + Math.round(source.z)
            : sourceId;
        this.recentAmbientSoundObservations.removeIf(existing -> existing.sourceId().equals(id));
        this.recentAmbientSoundObservations.addLast(
            new SoundObservation(
                this.tickCounter,
                id,
                entityType,
                soundEventId,
                source.x,
                source.y,
                source.z,
                true
            )
        );
        expireAmbientSoundObservations();
    }

    private boolean tryRecordAmbientFromGlobalSound(
        ClientPlayerEntity player,
        ClientWorld world,
        String soundEventId,
        Vec3d source
    ) {
        String typeHint = ambientLabelFromSoundEvent(soundEventId);
        LivingEntity nearest = null;
        double nearestDistance = 6.0;
        for (Entity entity : world.getOtherEntities(null, new net.minecraft.util.math.Box(
            source.x - 6.0, source.y - 6.0, source.z - 6.0,
            source.x + 6.0, source.y + 6.0, source.z + 6.0
        ))) {
            if (!(entity instanceof LivingEntity living) || !living.isAlive() || entity instanceof PlayerEntity) {
                continue;
            }
            MobDisposition disposition = classifyMobDisposition(player, living);
            if (disposition.threatNow()) {
                continue;
            }
            if (!disposition.ambientEligible() && typeHint == null) {
                continue;
            }
            double dx = entity.getX() - source.x;
            double dy = entity.getY() - source.y;
            double dz = entity.getZ() - source.z;
            double distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
            if (distance < nearestDistance) {
                nearest = living;
                nearestDistance = distance;
            }
        }
        if (nearest != null) {
            String type = entityTypeName(nearest);
            if (type == null || type.isBlank()) {
                type = typeHint != null ? typeHint : "unknown";
            }
            recordAmbientSoundObservation(
                player,
                soundEventId,
                type,
                new Vec3d(nearest.getX(), nearest.getY(), nearest.getZ()),
                nearest.getUuid().toString()
            );
            return true;
        }
        if (typeHint != null) {
            recordAmbientSoundObservation(player, soundEventId, typeHint, source, null);
            return true;
        }
        return false;
    }

    private String ambientLabelFromSoundEvent(String soundEventId) {
        if (soundEventId == null || soundEventId.isBlank()) {
            return null;
        }
        String id = soundEventId.toLowerCase(java.util.Locale.ROOT);
        // 具体的なものを先に
        String[][] patterns = {
            {"villager", "villager"},
            {"wandering_trader", "wandering_trader"},
            {"iron_golem", "iron_golem"},
            {"snow_golem", "snow_golem"},
            {"allay", "allay"},
            {"axolotl", "axolotl"},
            {"cat", "cat"},
            {"ocelot", "ocelot"},
            {"wolf", "wolf"},
            {"parrot", "parrot"},
            {"chicken", "chicken"},
            {"cow", "cow"},
            {"pig", "pig"},
            {"sheep", "sheep"},
            {"horse", "horse"},
            {"donkey", "donkey"},
            {"mule", "mule"},
            {"llama", "llama"},
            {"camel", "camel"},
            {"frog", "frog"},
            {"goat", "goat"},
            {"bee", "bee"},
            {"fox", "fox"},
            {"rabbit", "rabbit"},
            {"panda", "panda"},
            {"sniffer", "sniffer"},
            {"armadillo", "armadillo"},
            {"turtle", "turtle"},
            {"dolphin", "dolphin"},
            {"squid", "squid"},
            {"glow_squid", "glow_squid"},
            {"bat", "bat"},
            {"strider", "strider"},
        };
        for (String[] pattern : patterns) {
            if (id.contains(pattern[0])) {
                return pattern[1];
            }
        }
        return null;
    }

    private String resolveSoundSourceId(ClientWorld world, Vec3d source, String hostileLabel, String soundEventId) {
        Entity nearest = null;
        double nearestDistance = 4.5;
        ClientPlayerEntity player = MinecraftClient.getInstance().player;
        for (Entity entity : world.getOtherEntities(null, new net.minecraft.util.math.Box(
            source.x - 4.5, source.y - 4.5, source.z - 4.5,
            source.x + 4.5, source.y + 4.5, source.z + 4.5
        ))) {
            if (!(entity instanceof LivingEntity hostile) || !(entity instanceof Monster) || !hostile.isAlive()) {
                continue;
            }
            if (player != null && !classifyMobDisposition(player, hostile).threatNow()) {
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
        // Match specific identifiers and legacy aliases before generic ones like "zombie".
        for (String[] pattern : HOSTILE_SOUND_LABEL_PATTERNS) {
            if (soundEventId.contains(pattern[0])) {
                return pattern[1];
            }
        }
        return null;
    }

    private String classifyOminousSoundKind(String soundEventId) {
        if (soundEventId == null || soundEventId.isBlank()) {
            return null;
        }
        if (soundEventId.contains("sculk_shrieker") || soundEventId.contains("shriek")) {
            return "sculk_shrieker";
        }
        if (soundEventId.contains("sculk_sensor")) {
            return "sculk_sensor";
        }
        if (soundEventId.contains("sonic_boom") || soundEventId.contains("sonic_charge")) {
            // ビームは専用 kind で送る（チャージ音=予兆の段階で送れば悲鳴が間に合う）。
            // サーバ側はこの kind を受けると即座に悲鳴を上げる。
            return "warden_sonic_boom";
        }
        if (soundEventId.contains("warden")) {
            if (soundEventId.contains("death")) {
                // 死亡音は討伐確認に使う。presence として latch すると
                // 討伐後しばらく不穏空気が残ってしまうので除外
                return null;
            }
            if (soundEventId.contains("heartbeat")) {
                return "warden_heartbeat";
            }
            return "warden_presence";
        }
        return null;
    }

    private String classifyWeatherSoundKind(String soundEventId) {
        if (soundEventId == null || soundEventId.isBlank()) {
            return null;
        }
        if (soundEventId.contains("weather.rain") || soundEventId.contains("rain.above")) {
            return "rain";
        }
        if (soundEventId.contains("lightning_bolt.thunder") || soundEventId.contains("weather.thunder") || soundEventId.contains("thunder")) {
            return "thunder";
        }
        return null;
    }

    private boolean isExplosionSound(String soundEventId) {
        if (soundEventId == null || soundEventId.isBlank()) {
            return false;
        }
        return soundEventId.contains("explode") || soundEventId.contains("explosion");
    }

    private void recordOminousSoundObservation(String kind) {
        this.lastOminousSoundObservedTick = this.tickCounter;
        this.lastOminousSoundKind = kind;
    }

    private void recordWeatherSoundObservation(String kind) {
        if ("rain".equals(kind)) {
            this.lastRainSoundObservedTick = this.tickCounter;
            return;
        }
        if ("thunder".equals(kind)) {
            this.lastThunderSoundObservedTick = this.tickCounter;
        }
    }

    private void observeNearbyLightning(ClientPlayerEntity player, ClientWorld world) {
        double nearestDistance = Double.POSITIVE_INFINITY;
        for (Entity entity : world.getOtherEntities(
            null,
            player.getBoundingBox().expand(30.0, 16.0, 30.0)
        )) {
            if (!"lightning_bolt".equals(entityTypeName(entity))) {
                continue;
            }
            double distance = player.distanceTo(entity);
            if (distance < nearestDistance) {
                nearestDistance = distance;
            }
        }
        if (nearestDistance == Double.POSITIVE_INFINITY) {
            return;
        }
        this.lastNearbyLightningObservedTick = this.tickCounter;
        this.lastNearbyLightningDistance = nearestDistance;
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

    private String detectBossOmenKind(ClientPlayerEntity player, ClientWorld world, BlockPos origin) {
        if (isEnderDragonSummonScene(world, origin)) {
            return "ender_dragon_summon";
        }
        if (isEnderDragonArenaScene(world, origin)) {
            return "ender_dragon_arena";
        }
        if (isWitherAssemblyScene(world, origin)) {
            return "wither_assembly";
        }
        return null;
    }

    private boolean isEnderDragonArenaScene(ClientWorld world, BlockPos origin) {
        String dimensionId = world.getRegistryKey().getValue().toString();
        if (!"minecraft:the_end".equals(dimensionId) && !"the_end".equals(dimensionId)) {
            return false;
        }
        int portalFrames = 0;
        int portalBlocks = 0;
        int dragonEggs = 0;
        for (int dx = -BOSS_OMEN_SCAN_RADIUS; dx <= BOSS_OMEN_SCAN_RADIUS; dx += 1) {
            for (int dy = -8; dy <= 8; dy += 1) {
                for (int dz = -BOSS_OMEN_SCAN_RADIUS; dz <= BOSS_OMEN_SCAN_RADIUS; dz += 1) {
                    String blockId = Registries.BLOCK.getId(world.getBlockState(origin.add(dx, dy, dz)).getBlock()).getPath();
                    if ("end_portal_frame".equals(blockId)) {
                        portalFrames += 1;
                    } else if ("end_portal".equals(blockId)) {
                        portalBlocks += 1;
                    } else if ("dragon_egg".equals(blockId)) {
                        dragonEggs += 1;
                    }
                }
            }
        }
        return dragonEggs > 0 || portalBlocks >= 4 || portalFrames >= 8;
    }

    private boolean isEnderDragonSummonScene(ClientWorld world, BlockPos origin) {
        if (!isEnderDragonArenaScene(world, origin)) {
            return false;
        }
        int nearbyEndCrystals = 0;
        for (Entity entity : world.getOtherEntities(
            null,
            new net.minecraft.util.math.Box(
                origin.getX() - 12.0, origin.getY() - 8.0, origin.getZ() - 12.0,
                origin.getX() + 13.0, origin.getY() + 9.0, origin.getZ() + 13.0
            )
        )) {
            String entityType = entityTypeName(entity);
            if ("end_crystal".equals(entityType)) {
                nearbyEndCrystals += 1;
            }
        }
        return nearbyEndCrystals >= 4;
    }

    private boolean isWitherAssemblyScene(ClientWorld world, BlockPos origin) {
        for (int dx = -WITHER_OMEN_SCAN_RADIUS; dx <= WITHER_OMEN_SCAN_RADIUS; dx += 1) {
            for (int dy = -4; dy <= 4; dy += 1) {
                for (int dz = -WITHER_OMEN_SCAN_RADIUS; dz <= WITHER_OMEN_SCAN_RADIUS; dz += 1) {
                    BlockPos base = origin.add(dx, dy, dz);
                    if (isWitherAssemblyAt(world, base, Direction.EAST) || isWitherAssemblyAt(world, base, Direction.SOUTH)) {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    private boolean isWitherAssemblyAt(ClientWorld world, BlockPos base, Direction armDirection) {
        if (!isWitherBodyBlock(world.getBlockState(base))) {
            return false;
        }
        BlockPos leftArm = base.offset(armDirection);
        BlockPos rightArm = base.offset(armDirection.getOpposite());
        BlockPos stem = base.down();
        if (!isWitherBodyBlock(world.getBlockState(leftArm))
            || !isWitherBodyBlock(world.getBlockState(rightArm))
            || !isWitherBodyBlock(world.getBlockState(stem))) {
            return false;
        }
        return isWitherSkullBlock(world.getBlockState(base.up()))
            && isWitherSkullBlock(world.getBlockState(leftArm.up()))
            && isWitherSkullBlock(world.getBlockState(rightArm.up()));
    }

    private boolean isWitherBodyBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return "soul_sand".equals(blockId) || "soul_soil".equals(blockId);
    }

    private boolean isWitherSkullBlock(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return "wither_skeleton_skull".equals(blockId) || "wither_skeleton_wall_skull".equals(blockId);
    }

    private boolean usesDayNightCycle(ClientWorld world) {
        return "minecraft:overworld".equals(world.getRegistryKey().getValue().toString());
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
        String overheadCoverType,
        double enclosureScore,
        int connectedDarkVolume,
        double nearestDarkSpawnDistance,
        int nearbyLightSourceCount,
        double nearestLightSourceDistance,
        int cardinalWallCount,
        double ceilingHeight
    ) {
        int localLight = world.getLightLevel(origin);
        double lightPenalty = (15.0 - localLight) / 15.0;
        double darkVolumeScore = Math.min(1.0, connectedDarkVolume / 80.0);
        double spawnDistanceScore = nearestDarkSpawnDistance >= 99.0
            ? 0.0
            : Math.max(0.0, (12.0 - nearestDarkSpawnDistance) / 12.0);
        double nearbyLightSourceRelief = nearbyLightSourceCount <= 0 || nearestLightSourceDistance >= 99.0
            ? 0.0
            : Math.max(0.0, (4.0 - nearestLightSourceDistance) / 4.0);
        double skyPenalty = world.isSkyVisible(origin) ? 0.0 : 0.15;
        boolean litInteriorPocket = isLitInteriorSafePocket(
            world,
            origin,
            localLight,
            overheadCoverType,
            connectedDarkVolume,
            nearestDarkSpawnDistance,
            nearbyLightSourceCount,
            nearestLightSourceDistance,
            ceilingHeight
        );
        if (litInteriorPocket) {
            return clamp01(
                lightPenalty * 0.18
                    + darkVolumeScore * 0.12
                    + spawnDistanceScore * 0.08
                    - nearbyLightSourceRelief * 0.25
            );
        }
        boolean crampedDarkBurrow = !world.isSkyVisible(origin)
            && ceilingHeight <= 3.0
            && cardinalWallCount >= 3
            && connectedDarkVolume <= 12;
        if (crampedDarkBurrow) {
            return clamp01(
                lightPenalty * 0.15
                    + darkVolumeScore * 0.25
                    + spawnDistanceScore * 0.1
                    - nearbyLightSourceRelief * 0.2
            );
        }
        return clamp01(
            lightPenalty * 0.35
                + enclosureScore * 0.2
                + darkVolumeScore * 0.3
                + spawnDistanceScore * 0.15
                + skyPenalty
                - nearbyLightSourceRelief * 0.3
        );
    }

    private boolean isLitInteriorSafePocket(
        ClientWorld world,
        BlockPos origin,
        int localLight,
        String overheadCoverType,
        int connectedDarkVolume,
        double nearestDarkSpawnDistance,
        int nearbyLightSourceCount,
        double nearestLightSourceDistance,
        double ceilingHeight
    ) {
        if (world.isSkyVisible(origin)) {
            return false;
        }
        boolean nearbyLightSource = nearbyLightSourceCount > 0
            && nearestLightSourceDistance <= LIT_INTERIOR_SAFE_MIN_SPAWN_DISTANCE;
        if (localLight < LIT_INTERIOR_SAFE_LIGHT_THRESHOLD && !nearbyLightSource) {
            return false;
        }
        if ("foliage".equals(overheadCoverType) || "fluid".equals(overheadCoverType)) {
            return false;
        }
        if (ceilingHeight > LIT_INTERIOR_SAFE_MAX_CEILING_HEIGHT) {
            return false;
        }
        if (connectedDarkVolume > LIT_INTERIOR_SAFE_MAX_CONNECTED_VOLUME) {
            return false;
        }
        return nearestDarkSpawnDistance >= LIT_INTERIOR_SAFE_MIN_SPAWN_DISTANCE;
    }

    private int countNearbyLightSources(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -4; dx <= 4; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -4; dz <= 4; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (isNearbyLightSource(world.getBlockState(sample))) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private double estimateNearestLightSourceDistance(ClientWorld world, BlockPos origin) {
        double nearest = 999.0;
        for (int dx = -4; dx <= 4; dx += 1) {
            for (int dy = -3; dy <= 3; dy += 1) {
                for (int dz = -4; dz <= 4; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (!isNearbyLightSource(world.getBlockState(sample))) {
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

    private boolean isNearbyLightSource(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if ("fire".equals(blockId) || "soul_fire".equals(blockId)) {
            return true;
        }
        if (isLitCampfire(state, blockId)) {
            return true;
        }
        return state.getLuminance() >= 10;
    }

    private int countNearbyDamagingLightSources(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -2; dx <= 2; dx += 1) {
            for (int dy = -1; dy <= 2; dy += 1) {
                for (int dz = -2; dz <= 2; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (isDamagingLightSource(world.getBlockState(sample))) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private double estimateNearestDamagingLightSourceDistance(ClientWorld world, BlockPos origin) {
        double nearest = 999.0;
        for (int dx = -2; dx <= 2; dx += 1) {
            for (int dy = -1; dy <= 2; dy += 1) {
                for (int dz = -2; dz <= 2; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    if (!isDamagingLightSource(world.getBlockState(sample))) {
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

    private boolean isDamagingLightSource(BlockState state) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if ("fire".equals(blockId) || "soul_fire".equals(blockId)) {
            return true;
        }
        if (isLitCampfire(state, blockId)) {
            return true;
        }
        return "magma_block".equals(blockId)
            || "lava".equals(blockId)
            || "lava_cauldron".equals(blockId);
    }

    private boolean isLitCampfire(BlockState state, String blockId) {
        if (!"campfire".equals(blockId) && !"soul_campfire".equals(blockId)) {
            return false;
        }
        return !state.contains(Properties.LIT) || Boolean.TRUE.equals(state.get(Properties.LIT));
    }

    private boolean isStandingOnMagmaBlock(ClientWorld world, ClientPlayerEntity player) {
        BlockPos blockBelow = player.getBlockPos().down();
        String blockId = Registries.BLOCK.getId(world.getBlockState(blockBelow).getBlock()).getPath();
        return "magma_block".equals(blockId);
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

    private double scanNearbyEndPortalFrame(ClientWorld world, BlockPos origin) {
        double best = Double.MAX_VALUE;
        for (int dx = -END_PORTAL_FRAME_SCAN_RADIUS; dx <= END_PORTAL_FRAME_SCAN_RADIUS; dx += 1) {
            for (int dy = -END_PORTAL_FRAME_SCAN_RADIUS; dy <= END_PORTAL_FRAME_SCAN_RADIUS; dy += 1) {
                for (int dz = -END_PORTAL_FRAME_SCAN_RADIUS; dz <= END_PORTAL_FRAME_SCAN_RADIUS; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    if ("end_portal_frame".equals(blockId)) {
                        double d = Math.sqrt(sample.getSquaredDistance(origin));
                        if (d <= END_PORTAL_FRAME_SCAN_RADIUS && d < best) {
                            best = d;
                        }
                    }
                }
            }
        }
        return best == Double.MAX_VALUE ? -1 : best;
    }

    private String[] scanNearbyPortals(ClientWorld world, BlockPos origin) {
        String bestType = null;
        double bestDistance = Double.MAX_VALUE;
        for (int dx = -GATEWAY_SCAN_RADIUS; dx <= GATEWAY_SCAN_RADIUS; dx += 1) {
            for (int dy = -8; dy <= 8; dy += 1) {
                for (int dz = -GATEWAY_SCAN_RADIUS; dz <= GATEWAY_SCAN_RADIUS; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    String portalType = null;
                    double maxRange = 0;
                    if ("nether_portal".equals(blockId)) {
                        portalType = "nether_portal";
                        maxRange = PORTAL_SCAN_RADIUS;
                    } else if ("end_portal".equals(blockId)) {
                        portalType = "end_portal";
                        maxRange = PORTAL_SCAN_RADIUS;
                    } else if ("end_gateway".equals(blockId)) {
                        portalType = "end_gateway";
                        maxRange = GATEWAY_SCAN_RADIUS;
                    }
                    if (portalType == null) {
                        continue;
                    }
                    double distance = Math.sqrt(sample.getSquaredDistance(origin));
                    if (distance > maxRange) {
                        continue;
                    }
                    if (distance < bestDistance) {
                        bestType = portalType;
                        bestDistance = distance;
                    }
                }
            }
        }
        if (bestType == null) {
            return null;
        }
        return new String[]{bestType, String.valueOf(bestDistance)};
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

    private int countDoubleHeightOpenSides(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (Direction direction : new Direction[] {
            Direction.NORTH,
            Direction.SOUTH,
            Direction.EAST,
            Direction.WEST,
        }) {
            if (isDoubleHeightOpenSide(world, origin, direction)) {
                count += 1;
            }
        }
        return count;
    }

    private boolean isDoubleHeightOpenSide(ClientWorld world, BlockPos origin, Direction direction) {
        BlockPos foot = origin.offset(direction);
        BlockPos head = foot.up();
        return isOpenMedium(world.getBlockState(foot)) && isOpenMedium(world.getBlockState(head));
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
        double height = Math.max(entity.getHeight(), 0.5);
        Vec3d[] samplePoints = new Vec3d[] {
            new Vec3d(entity.getX(), entity.getY() + height * 0.25, entity.getZ()),
            new Vec3d(entity.getX(), entity.getY() + height * 0.55, entity.getZ()),
            new Vec3d(entity.getX(), entity.getY() + height * 0.85, entity.getZ()),
        };
        for (Vec3d end : samplePoints) {
            if (hasDirectLineOfSight(player, world, end) || hasAdjacentWindowLineOfSight(player, world, end)) {
                return true;
            }
        }
        return false;
    }

    private boolean shouldObserveOccludedAudio(ClientPlayerEntity player, ClientWorld world, Vec3d source) {
        if (hasDirectLineOfSight(player, world, source)) {
            return true;
        }
        if (hasAdjacentAcousticOpeningToward(player, world, source)) {
            return true;
        }
        BlockPos playerPos = player.getBlockPos();
        BlockPos sourcePos = BlockPos.ofFloored(source);
        return Math.abs(sourcePos.getX() - playerPos.getX()) <= OCCLUDED_AUDIO_HORIZONTAL_BLOCKS
            && Math.abs(sourcePos.getZ() - playerPos.getZ()) <= OCCLUDED_AUDIO_HORIZONTAL_BLOCKS
            && Math.abs(sourcePos.getY() - playerPos.getY()) <= OCCLUDED_AUDIO_VERTICAL_BLOCKS;
    }

    private boolean hasDirectLineOfSight(ClientPlayerEntity player, ClientWorld world, Vec3d end) {
        HitResult hit = world.raycast(
            new RaycastContext(
                player.getEyePos(),
                end,
                RaycastContext.ShapeType.COLLIDER,
                RaycastContext.FluidHandling.NONE,
                player
            )
        );
        return hit.getType() == HitResult.Type.MISS;
    }

    private boolean hasAdjacentWindowLineOfSight(ClientPlayerEntity player, ClientWorld world, Vec3d end) {
        Vec3d start = player.getEyePos();
        for (Direction side : Direction.Type.HORIZONTAL) {
            if (!isTargetOnSide(player, end, side)) {
                continue;
            }
            if (!hasAdjacentViewThroughBlock(world, player.getBlockPos(), side)) {
                continue;
            }
            if (hasLineThroughAdjacentViewBlocks(world, player.getBlockPos(), start, end, side)) {
                return true;
            }
        }
        return false;
    }

    private boolean hasAdjacentAcousticOpeningToward(ClientPlayerEntity player, ClientWorld world, Vec3d source) {
        BlockPos origin = player.getBlockPos();
        for (Direction side : Direction.Type.HORIZONTAL) {
            if (!isTargetOnSide(player, source, side)) {
                continue;
            }
            if (hasAdjacentAcousticOpening(world, origin, side)) {
                return true;
            }
        }
        return false;
    }

    private boolean hasLineThroughAdjacentViewBlocks(
        ClientWorld world,
        BlockPos playerBlock,
        Vec3d start,
        Vec3d end,
        Direction side
    ) {
        Vec3d delta = end.subtract(start);
        double distance = delta.length();
        if (distance <= 0.0001) {
            return true;
        }
        int steps = Math.max(8, (int) Math.ceil(distance * 8.0));
        Vec3d step = delta.multiply(1.0 / steps);
        boolean passedAdjacentViewBlock = false;

        for (int index = 1; index <= steps; index += 1) {
            Vec3d point = start.add(step.multiply(index));
            BlockPos sample = BlockPos.ofFloored(point);
            if (sample.equals(playerBlock) || sample.equals(playerBlock.up())) {
                continue;
            }
            BlockState state = world.getBlockState(sample);
            if (isVisuallyEmpty(world, sample, state)) {
                continue;
            }
            if (!passedAdjacentViewBlock) {
                if (!isImmediateSideBlock(playerBlock, sample, side) || !isViewThroughBlock(state)) {
                    return false;
                }
                passedAdjacentViewBlock = true;
                continue;
            }
            if (!isViewThroughBlock(state)) {
                return false;
            }
        }
        return passedAdjacentViewBlock;
    }

    private boolean hasAdjacentAcousticOpening(ClientWorld world, BlockPos origin, Direction side) {
        return isAcousticOpeningBlock(world.getBlockState(origin.offset(side)))
            || isAcousticOpeningBlock(world.getBlockState(origin.up().offset(side)));
    }

    private boolean hasAdjacentViewThroughBlock(ClientWorld world, BlockPos origin, Direction side) {
        return isViewThroughBlock(world.getBlockState(origin.offset(side)))
            || isViewThroughBlock(world.getBlockState(origin.up().offset(side)));
    }

    private boolean isImmediateSideBlock(BlockPos origin, BlockPos sample, Direction side) {
        return sample.equals(origin.offset(side)) || sample.equals(origin.up().offset(side));
    }

    private boolean isTargetOnSide(ClientPlayerEntity player, Vec3d target, Direction side) {
        return switch (side) {
            case EAST -> target.x > player.getX();
            case WEST -> target.x < player.getX();
            case SOUTH -> target.z > player.getZ();
            case NORTH -> target.z < player.getZ();
            default -> false;
        };
    }

    private boolean isVisuallyEmpty(ClientWorld world, BlockPos pos, BlockState state) {
        return state.isAir() || state.getCollisionShape(world, pos).isEmpty();
    }

    private boolean isAcousticOpeningBlock(BlockState state) {
        if (state.isAir()) {
            return false;
        }
        String path = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (state.getBlock() instanceof DoorBlock) {
            return true;
        }
        return path.endsWith("_bars")
            || path.contains("grate")
            || path.contains("lattice");
    }

    private boolean isViewThroughBlock(BlockState state) {
        if (state.isAir()) {
            return false;
        }
        String path = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (state.getBlock() instanceof DoorBlock) {
            return true;
        }
        return path.contains("glass")
            || path.endsWith("_bars")
            || path.contains("grate")
            || path.contains("lattice");
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

    private String ambientMobSignature(List<AmbientMobObservation> ambientMobs) {
        StringBuilder builder = new StringBuilder();
        int limit = Math.min(ambientMobs.size(), 2);
        for (int index = 0; index < limit; index += 1) {
            AmbientMobObservation mob = ambientMobs.get(index);
            if (index > 0) {
                builder.append("|");
            }
            builder.append(mob.type())
                .append("@")
                .append(mob.horizontalDirection())
                .append("@")
                .append(mob.temperament() == null ? "" : mob.temperament())
                .append("@")
                .append(mob.profession() == null ? "" : mob.profession())
                .append("@")
                .append(Boolean.TRUE.equals(mob.isBaby()) ? "baby" : "");
        }
        return builder.toString();
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
        boolean inWater,
        double x,
        double y,
        double z,
        float health,
        boolean recentlyHurt
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

    private record AmbientMobObservation(
        UUID uuid,
        String type,
        double distance,
        String horizontalDirection,
        String verticalRelation,
        String temperament,
        String cautionReason,
        Boolean isBaby,
        String profession,
        String villagerType
    ) {
    }

    /**
     * 村人 profession の短名（farmer / none / nitwit 等）。
     *
     * 注意: RegistryEntry.getKey() は Direct 登録だと empty になることがある。
     * クライアント同期の VillagerData はほぼ Direct なので、value() + Registries.getId を正とする。
     * getKey() だけに頼ると常に none 落ちし、就職後も求職者のままになる。
     */
    private String villagerProfessionId(VillagerEntity villager) {
        VillagerData data = villager.getVillagerData();
        Identifier id = Registries.VILLAGER_PROFESSION.getId(data.profession().value());
        if (id != null) {
            return id.getPath();
        }
        return data.profession()
            .getKey()
            .map(key -> key.getValue().getPath())
            .orElse("none");
    }

    private String villagerTypeId(VillagerEntity villager) {
        VillagerData data = villager.getVillagerData();
        Identifier id = Registries.VILLAGER_TYPE.getId(data.type().value());
        if (id != null) {
            return id.getPath();
        }
        return data.type()
            .getKey()
            .map(key -> key.getValue().getPath())
            .orElse(null);
    }

    private record MobDisposition(
        String type,
        boolean ambientEligible,
        boolean threatNow,
        String temperament,
        String cautionReason
    ) {
    }

    private record SoundSourceResolution(
        String hostileLabel,
        String sourceId,
        boolean spokenNameAllowed
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

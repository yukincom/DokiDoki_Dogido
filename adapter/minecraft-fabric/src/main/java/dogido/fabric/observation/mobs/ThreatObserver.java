package dogido.fabric.observation.mobs;

import dogido.fabric.observation.ObservationOrigin;

import dogido.fabric.observation.MobEnvironmentObservation;
import dogido.fabric.observation.mobs.MobClassifier.MobDisposition;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.observation.sound.AudioThreatObservation;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Queue;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.stream.Collectors;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.mob.CreeperEntity;
import net.minecraft.entity.mob.Monster;
import net.minecraft.entity.passive.BeeEntity;
import net.minecraft.entity.passive.DolphinEntity;
import net.minecraft.entity.passive.IronGolemEntity;
import net.minecraft.entity.passive.LlamaEntity;
import net.minecraft.entity.passive.PandaEntity;
import net.minecraft.entity.passive.PolarBearEntity;
import net.minecraft.entity.passive.WolfEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.network.packet.s2c.play.ExplosionS2CPacket;
import net.minecraft.util.math.Vec3d;

import static dogido.fabric.observation.MobIdentityObservation.mobIdentity;
import static dogido.fabric.observation.ObservationGeometry.classifyCardinal;
import static dogido.fabric.observation.ObservationGeometry.classifyHorizontal;
import static dogido.fabric.observation.ObservationGeometry.classifyVertical;
import static dogido.fabric.observation.ObservationVisibility.hasLineOfSight;

/** 敵の実観測と個体別の追跡を管理する。発見は通常範囲、追跡中は外側5ブロックまで保持する。 */
public final class ThreatObserver {
    private static final double VISUAL_RANGE_MARGIN = 5.0;
    private final Map<UUID, Double> lastThreatDistances = new HashMap<>();
    private final Map<UUID, Long> lastThreatSeenTicks = new HashMap<>();
    private final Map<UUID, Float> lastThreatHealths = new HashMap<>();
    private final Map<UUID, String> lastThreatTypes = new HashMap<>();
    private final Map<UUID, Integer> lastThreatEntityIds = new HashMap<>();
    private final Map<UUID, Vec3d> lastThreatPositions = new HashMap<>();
    private final Map<UUID, HostileOutcomeObservation> hostileOutcomes = new LinkedHashMap<>();
    private final Queue<ServerSignal> serverSignals = new ConcurrentLinkedQueue<>();
    private final Map<UUID, Long> confirmedDeaths = new HashMap<>();
    private final Set<UUID> playerAttacked = new HashSet<>();
    private long resolvedDamageTick = -1001;
    private boolean serverOwnsDeaths;
    private final Deque<ExplosionObservation> recentExplosionObservations = new ArrayDeque<>();
    private final Map<UUID, Long> lineOfSightStartedTicks = new HashMap<>();
    private final Map<UUID, Long> confirmedVisibleTicks = new HashMap<>();
    private long lastDamageTick = -1000;
    private long lastVisualThreatObservedTick = -1000;
    private long lastExplosionObservedTick = -1000;
    private long lastCombatSignalTick = -1000;
    private boolean combatActive = false;
    // 本人が関わった戦闘だけを、既存の戦闘終了猶予まで保持する。
    private boolean playerEngaged;
    private float lastHealth = 20.0f;

    private final Settings settings;
    private final MobClassifier mobClassifier;
    private final BossTracker bossTracker;
    private final Runnable markAudioObserved;
    private final java.util.function.LongSupplier tickSource;

    public record Settings(double maxThreatDistance, double visibleThreatDistance,
        double rearWarningDistance, double panicDistance, int visualConfirmTicks,
        int visualHoldTicks, int combatEndedQuietTicks) {}

    public ThreatObserver(Settings settings, MobClassifier mobClassifier, BossTracker bossTracker,
            Runnable markAudioObserved, java.util.function.LongSupplier tickSource) {
        this.settings = settings;
        this.mobClassifier = mobClassifier;
        this.bossTracker = bossTracker;
        this.markAudioObserved = markAudioObserved;
        this.tickSource = tickSource;
    }

    public void observeHealth(ClientPlayerEntity player) {
        if (player.getHealth() < this.lastHealth) this.lastDamageTick = this.tickSource.getAsLong();
        this.lastHealth = player.getHealth();
    }

    public void recordServerDeath(ServerWorld world, UUID id, UUID attacker, boolean explosion,
            String killedHostileType, boolean noOtherAttacker) {
        this.serverSignals.add(new ServerDeath(world, id, attacker, explosion, killedHostileType, noOtherAttacker));
    }

    public void recordServerTarget(ServerWorld world, UUID mob) {
        this.serverSignals.add(new ServerTarget(world, mob));
    }

    public void observePlayerAttack(UUID mob) {
        this.playerAttacked.add(mob);
        observePlayerEngagement();
    }

    public List<HostileOutcomeObservation> outcomes() { return List.copyOf(this.hostileOutcomes.values()); }
    public long lastDamageTick() { return this.lastDamageTick; }
    public long lastVisualObservedTick() { return this.lastVisualThreatObservedTick; }
    public long lastExplosionObservedTick() { return this.lastExplosionObservedTick; }
    public boolean combatActive() { return this.combatActive; }
    public boolean playerEngaged() { return this.playerEngaged; }

    public void observePlayerEngagement() {
        this.playerEngaged = true;
        this.combatActive = true;
        this.lastCombatSignalTick = this.tickSource.getAsLong();
    }

    public void endCombat() {
        this.combatActive = false;
        this.playerEngaged = false;
        this.playerAttacked.clear();
    }
    public void finishConfirmedKill() {
        endCombat();
        this.resolvedDamageTick = this.lastDamageTick;
    }

    public void resetHealth() { this.lastHealth = 20.0f; }

    public void resetObservationTimes() {
        this.lastDamageTick = -1000;
        this.resolvedDamageTick = -1001;
        this.lastVisualThreatObservedTick = -1000;
        this.lastExplosionObservedTick = -1000;
        this.lastCombatSignalTick = -1000;
        endCombat();
    }

    public void resetTracking() {
        this.lastThreatDistances.clear();
        this.lastThreatSeenTicks.clear();
        this.lineOfSightStartedTicks.clear();
        this.confirmedVisibleTicks.clear();
        this.lastThreatHealths.clear();
        this.lastThreatTypes.clear();
        this.lastThreatEntityIds.clear();
        this.lastThreatPositions.clear();
        this.confirmedDeaths.clear();
        this.serverSignals.clear();
        clearCombatOutcomeObservations();
    }

    private double acquisitionDistance() {
        return Math.max(this.settings.maxThreatDistance(), this.settings.visibleThreatDistance());
    }

    /** 追跡用の走査外周。新規発見と敵数の集計は従来の通常範囲。単位はブロック。 */
    private double scanDistance() {
        return acquisitionDistance() + VISUAL_RANGE_MARGIN;
    }

    private boolean withinVisualRange(UUID id, double distance) {
        double limit = this.confirmedVisibleTicks.containsKey(id) ? scanDistance() : acquisitionDistance();
        return Double.isFinite(distance) && distance >= 0.0 && distance <= limit;
    }

    public List<ThreatObservation> scanThreats(ClientPlayerEntity player, ClientWorld world) {
        return scanThreats(ObservationOrigin.player(player),world);
    }
    /** 身体の観測は別インスタンスで採り、現在の視線だけを返す。player戦闘の確認・保持条件は変えない。 */
    public List<ThreatObservation> scanThreats(ObservationOrigin origin, ClientWorld world) {
        double scanDistance = scanDistance();
        List<Entity> entities = world.getOtherEntities(
            origin.player(),
            origin.bounds().expand(scanDistance),
            this::isThreatScanCandidateEntity
        );

        List<ThreatObservation> threats = new ArrayList<>();
        for (Entity entity : entities) {
            if (origin.isSelf(entity) || !(entity instanceof LivingEntity living)) {
                continue;
            }
            UUID entityUuid = entity.getUuid();
            if (this.confirmedDeaths.containsKey(entityUuid)) continue;
            String trackedType = this.lastThreatTypes.get(entityUuid);
            boolean died = living.isDead()
                || living.getHealth() <= 0.0f
                || living.deathTime > 0
                || !living.isAlive();
            if (died) {
                if (
                    !this.serverOwnsDeaths && trackedType != null
                    && this.tickSource.getAsLong() - this.lastThreatSeenTicks.getOrDefault(entityUuid, -1000L) <= 100
                ) {
                    recordDefeatedHostile(living, trackedType);
                }
                continue;
            }
            if (!isThreatCandidateEntity(entity)) {
                continue;
            }
            MobDisposition disposition = this.mobClassifier.classifyMobDisposition(origin.threatTargetId(), living);
            if (!disposition.threatNow()) {
                continue;
            }
            double distance = origin.distanceTo(entity);
            if (!withinVisualRange(entityUuid, distance)) {
                continue;
            }

            Vec3d entityPosition = new Vec3d(entity.getX(), entity.getY(), entity.getZ());
            String horizontal = classifyHorizontal(origin, entityPosition);
            String cardinal = classifyCardinal(origin, entityPosition);
            String vertical = classifyVertical(origin, entityPosition);
            boolean approaching = isApproaching(entity.getUuid(), distance);
            boolean rearThreat = distance <= Math.min(this.settings.rearWarningDistance(), 3.0)
                && ("back".equals(horizontal) || "back_left".equals(horizontal) || "back_right".equals(horizontal));
            boolean lineOfSight = origin.visible(world,entity,acquisitionDistance());
            if (origin.body() && !lineOfSight) continue;
            float currentHealth = living.getHealth();
            Float previousHealth = this.lastThreatHealths.get(entity.getUuid());
            boolean recentlyHurt = previousHealth != null && currentHealth + 0.25f < previousHealth;
            boolean fuseActive = living instanceof CreeperEntity creeper
                && (creeper.getFuseSpeed() > 0 || creeper.isIgnited());

            ThreatObservation observation = new ThreatObservation(
                entity.getUuid(),
                disposition.type(),
                distance,
                horizontal,
                cardinal,
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
                recentlyHurt,
                fuseActive,
                mobIdentity(entity),
                MobEnvironmentObservation.capture(entity)
            );
            threats.add(observation);
            this.lastThreatDistances.put(entity.getUuid(), distance);
            this.lastThreatSeenTicks.put(entity.getUuid(), this.tickSource.getAsLong());
            this.lastThreatHealths.put(entity.getUuid(), currentHealth);
            this.lastThreatTypes.put(entity.getUuid(), disposition.type());
            this.lastThreatEntityIds.put(entity.getUuid(), entity.getId());
            this.lastThreatPositions.put(entity.getUuid(), entityPosition);
            if ("warden".equals(disposition.type())) {
                this.bossTracker.observeWarden(this.tickSource.getAsLong(), entity.getX(), entity.getY(), entity.getZ(), entity.getId());
            }
            if ("ender_dragon".equals(disposition.type())) {
                this.bossTracker.observeDragon(this.tickSource.getAsLong(), entity.getId());
            }
        }

        threats.sort(Comparator.comparingDouble(ThreatObservation::distance));
        return threats;
    }

    private boolean isThreatScanCandidateEntity(Entity entity) {
        if (!(entity instanceof LivingEntity) || entity instanceof PlayerEntity) {
            return false;
        }
        // 死亡アニメーション中は isAlive=false になる。直前まで追跡していた個体だけ
        // スキャンへ残し、「視界から消えた」と「死亡を観測した」を分離する。
        return this.lastThreatSeenTicks.containsKey(entity.getUuid())
            || isThreatCandidateEntity(entity);
    }

    public static boolean isThreatCandidateEntity(Entity entity) {
        return entity instanceof LivingEntity living && living.isAlive() && isCombatMob(entity);
    }

    public static boolean isCombatMob(Entity entity) {
        if (!(entity instanceof LivingEntity)) {
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

    private void recordDefeatedHostile(LivingEntity hostile, String hostileType) {
        UUID entityUuid = hostile.getUuid();
        if (this.hostileOutcomes.containsKey(entityUuid)) {
            return;
        }
        // リモートサーバーでは論理サーバーの死亡イベントを購読できない。
        // クライアント死亡状態だけで「プレイヤーが倒した」とは推測しない。
        recordHostileOutcome(
            entityUuid,
            hostileType,
            "other_death",
            "client_death_state"
        );
    }

    /** サーバー側の発生順を保つ。撃破後に別の敵が狙い始めた場合は、その戦闘を終了させない。 */
    public boolean applyServerObservations(ServerWorld world, UUID player) {
        this.serverOwnsDeaths = world != null;
        boolean confirmed = false;
        ServerSignal signal;
        while ((signal = this.serverSignals.poll()) != null) {
            if (signal.world() != world) continue;
            if (signal instanceof ServerTarget target) {
                if (!this.confirmedDeaths.containsKey(target.id())) {
                    observePlayerEngagement();
                    confirmed = false;
                }
                continue;
            }
            ServerDeath death = (ServerDeath) signal;
            this.playerAttacked.remove(death.id());
            if (this.confirmedDeaths.containsKey(death.id())) continue;
            boolean playerKill = player.equals(death.attacker());
            String type = this.lastThreatTypes.getOrDefault(death.id(), death.killedHostileType());
            if (type == null) continue;
            this.confirmedDeaths.put(death.id(), this.tickSource.getAsLong());
            String outcome = playerKill ? "player_kill" : death.explosion() ? "explosion_death" : "other_death";
            // クライアントの死亡描画が先に届いても、確定した撃破者と最後の一体を失わない。
            HostileOutcomeObservation prior = this.hostileOutcomes.get(death.id());
            if (prior != null && prior.evidence().equals("client_death_state")) {
                this.hostileOutcomes.remove(death.id());
            }
            if (recordHostileOutcome(death.id(), type, outcome, "server_death_event") && playerKill) {
                confirmed = death.noOtherAttacker();
            }
        }
        return confirmed && this.playerAttacked.isEmpty();
    }

    public Map<UUID, HostileOutcomeObservation> observeCreeperDetonations(ClientWorld world) {
        Map<UUID, HostileOutcomeObservation> newlyObserved = new LinkedHashMap<>();
        this.recentExplosionObservations.removeIf(
            explosion -> this.tickSource.getAsLong() - explosion.observedTick() > 12
        );
        if (this.recentExplosionObservations.isEmpty()) {
            return newlyObserved;
        }
        for (Map.Entry<UUID, String> entry : this.lastThreatTypes.entrySet()) {
            UUID entityUuid = entry.getKey();
            String type = entry.getValue();
            if (
                !("creeper".equals(type) || "charged_creeper".equals(type))
                || this.hostileOutcomes.containsKey(entityUuid)
            ) {
                continue;
            }
            Long seenAt = this.lastThreatSeenTicks.get(entityUuid);
            Integer entityId = this.lastThreatEntityIds.get(entityUuid);
            Vec3d position = this.lastThreatPositions.get(entityUuid);
            if (
                seenAt == null
                || entityId == null
                || position == null
                || this.tickSource.getAsLong() - seenAt > 12
                || world.getEntityById(entityId) != null
            ) {
                continue;
            }
            for (ExplosionObservation explosion : this.recentExplosionObservations) {
                double matchRadius = Math.max(4.0, explosion.radius() + 1.5);
                if (position.squaredDistanceTo(explosion.center()) > matchRadius * matchRadius) {
                    continue;
                }
                if (recordHostileOutcome(
                    entityUuid,
                    type,
                    "creeper_detonation",
                    "explosion_packet"
                )) {
                    newlyObserved.put(entityUuid, this.hostileOutcomes.get(entityUuid));
                }
                break;
            }
        }
        return newlyObserved;
    }

    boolean recordHostileOutcome(
        UUID entityUuid,
        String hostileType,
        String outcome,
        String evidence
    ) {
        if (this.hostileOutcomes.containsKey(entityUuid)) {
            return false;
        }
        this.hostileOutcomes.put(
            entityUuid,
            new HostileOutcomeObservation(
                entityUuid,
                hostileType,
                outcome,
                evidence,
                this.tickSource.getAsLong()
            )
        );
        if ("warden".equals(hostileType)) {
            this.bossTracker.recordWardenDefeat(this.tickSource.getAsLong());
        }
        if ("ender_dragon".equals(hostileType)) {
            this.bossTracker.recordDragonDefeat(this.tickSource.getAsLong());
        }
        return true;
    }

    public List<ThreatObservation> filterVisibleThreats(List<ThreatObservation> threats) {
        // 現在の走査から消えた個体は保持しない。再登場は内側から確認し直す。
        Set<UUID> observed = threats.stream().map(ThreatObservation::uuid).collect(Collectors.toSet());
        this.lineOfSightStartedTicks.keySet().retainAll(observed);
        this.confirmedVisibleTicks.keySet().retainAll(observed);
        List<ThreatObservation> visibleThreats = new ArrayList<>();
        int lineOfSightCount = 0;
        for (ThreatObservation threat : threats) {
            if (threat.lineOfSight() && withinVisualRange(threat.uuid(), threat.distance())) {
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
        if (!withinVisualRange(id, threat.distance())) {
            this.lineOfSightStartedTicks.remove(id);
            this.confirmedVisibleTicks.remove(id);
            return false;
        }
        if (threat.lineOfSight()) {
            this.lineOfSightStartedTicks.putIfAbsent(id, this.tickSource.getAsLong());
            long visibleFor = this.tickSource.getAsLong() - this.lineOfSightStartedTicks.getOrDefault(id, this.tickSource.getAsLong());
            boolean immediate = threat.distance() <= this.settings.panicDistance() || threat.isRearThreat();
            if (immediate || immediateForVisibleCluster || visibleFor >= this.settings.visualConfirmTicks()) {
                this.confirmedVisibleTicks.put(id, this.tickSource.getAsLong());
                return true;
            }
            return false;
        }

        this.lineOfSightStartedTicks.remove(id);
        Long confirmedTick = this.confirmedVisibleTicks.get(id);
        if (confirmedTick == null) {
            return false;
        }
        if (this.tickSource.getAsLong() - confirmedTick <= this.settings.visualHoldTicks()) {
            return true;
        }
        this.confirmedVisibleTicks.remove(id);
        return false;
    }

    public void updateCombatTracking(List<ThreatObservation> threats, List<AudioThreatObservation> audioThreats) {
        if (!threats.isEmpty()) {
            this.lastVisualThreatObservedTick = this.tickSource.getAsLong();
        }
        if (!audioThreats.isEmpty()) {
            this.markAudioObserved.run();
        }

        boolean recentDamage = this.lastDamageTick > this.resolvedDamageTick
            && this.tickSource.getAsLong() - this.lastDamageTick <= this.settings.combatEndedQuietTicks();
        if (!threats.isEmpty() || !audioThreats.isEmpty() || recentDamage) {
            this.lastCombatSignalTick = this.tickSource.getAsLong();
            this.combatActive = true;
        }
    }

    private boolean isApproaching(UUID entityId, double currentDistance) {
        Double previousDistance = this.lastThreatDistances.get(entityId);
        return previousDistance != null && currentDistance + 0.2 < previousDistance;
    }

    public void expireThreatMemory() {
        this.confirmedDeaths.values().removeIf(at -> this.tickSource.getAsLong() - at > 100);
        List<UUID> toRemove = new ArrayList<>();
        for (Map.Entry<UUID, Long> entry : this.lastThreatSeenTicks.entrySet()) {
            if (this.tickSource.getAsLong() - entry.getValue() > 100) {
                toRemove.add(entry.getKey());
            }
        }
        for (UUID uuid : toRemove) {
            this.lastThreatSeenTicks.remove(uuid);
            this.lastThreatDistances.remove(uuid);
            this.lastThreatHealths.remove(uuid);
            this.lastThreatTypes.remove(uuid);
            this.lastThreatEntityIds.remove(uuid);
            this.lastThreatPositions.remove(uuid);
            this.lineOfSightStartedTicks.remove(uuid);
            this.confirmedVisibleTicks.remove(uuid);
        }
    }

    public void clearCombatOutcomeObservations() {
        // 死亡アニメーションが残っても同じ個体を次の戦闘の撃破として数えない。
        for (UUID id : this.hostileOutcomes.keySet()) {
            this.lastThreatTypes.remove(id);
        }
        this.hostileOutcomes.clear();
        this.recentExplosionObservations.clear();
    }

    public void onExplosionPacket(ExplosionS2CPacket packet) {
        this.lastExplosionObservedTick = this.tickSource.getAsLong();
        this.recentExplosionObservations.addLast(
            new ExplosionObservation(packet.center(), packet.radius(), this.tickSource.getAsLong())
        );
    }

    public boolean shouldSendCombatEnded(
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
        if (this.tickSource.getAsLong() - this.lastDamageTick <= this.settings.combatEndedQuietTicks()) {
            return false;
        }
        return this.lastCombatSignalTick >= 0
            && this.tickSource.getAsLong() - this.lastCombatSignalTick >= this.settings.combatEndedQuietTicks();
    }

    private sealed interface ServerSignal {
        ServerWorld world();
    }
    private record ServerTarget(ServerWorld world, UUID id) implements ServerSignal {}
    private record ServerDeath(ServerWorld world, UUID id, UUID attacker, boolean explosion,
            String killedHostileType, boolean noOtherAttacker) implements ServerSignal {}

    private record ExplosionObservation(
        Vec3d center,
        float radius,
        long observedTick
    ) {
    }
}

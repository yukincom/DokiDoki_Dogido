package dogido.fabric.observation.sound;

import dogido.fabric.observation.MobIdentityObservation;
import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.mobs.MobClassifier.MobDisposition;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.observation.sound.WorldSoundFacts;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.sound.SoundInstance;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.network.packet.s2c.play.PlaySoundFromEntityS2CPacket;
import net.minecraft.network.packet.s2c.play.PlaySoundS2CPacket;
import net.minecraft.registry.entry.RegistryEntry;
import net.minecraft.sound.SoundCategory;
import net.minecraft.util.math.Vec3d;
import org.slf4j.Logger;

import static dogido.fabric.observation.MobIdentityObservation.entityTypeName;
import static dogido.fabric.observation.MobIdentityObservation.mobIdentity;
import static dogido.fabric.observation.ObservationGeometry.bucketDistance;
import static dogido.fabric.observation.ObservationGeometry.classifyHorizontal;
import static dogido.fabric.observation.ObservationGeometry.classifyVertical;
import static dogido.fabric.observation.ObservationGeometry.distanceBandRank;
import static dogido.fabric.observation.ObservationNumbers.round;
import static dogido.fabric.observation.ObservationVisibility.shouldObserveOccludedAudio;
import static dogido.fabric.observation.mobs.MobClassifier.isAmbientMobCandidate;
import static dogido.fabric.observation.sound.WorldSoundFacts.OMINOUS_SOUND_TTL_TICKS;

public final class SoundObserver {
    // 音 packet をクライアント側で保持する長さ。
    // 人間が「…？」「今の音なに？」と言うまでの猶予を確保する（旧5秒だと短い）。
    // 20 tick/s 想定で約15秒。サーバ hearing バッファ（約20s）以下に保つ。
    private static final int SOUND_OBSERVATION_TTL_TICKS = 300;
    private static final double GLOBAL_SOUND_SOURCE_MATCH_RADIUS = 4.0;
    // エンダーアイ投擲音: 鮮度 TTL（サーバ側 ender_eye_recent_ms=2000ms と同期）と投擲者判定の距離
    private static final double ENDER_EYE_LAUNCH_MATCH_RADIUS = 8.0;
    private static final String[][] HOSTILE_SOUND_LABEL_PATTERNS = {
        // 具体名を先に置く。entity 付き packet は実体から解決するが、座標だけの
        // hostile sound packet でも主要な敵を取りこぼさないための fallback。
        {"zombified_piglin", "zombified_piglin"},
        {"zombie_villager", "zombie_villager"},
        {"wither_skeleton", "wither_skeleton"},
        {"elder_guardian", "elder_guardian"},
        {"ender_dragon", "ender_dragon"},
        {"cave_spider", "cave_spider"},
        {"piglin_brute", "piglin_brute"},
        {"magma_cube", "magma_cube"},
        {"silverfish", "silverfish"},
        {"endermite", "endermite"},
        {"vindicator", "vindicator"},
        {"pillager", "pillager"},
        {"ravager", "ravager"},
        {"guardian", "guardian"},
        {"phantom", "phantom"},
        {"shulker", "shulker"},
        {"evoker", "evoker"},
        {"breeze", "breeze"},
        {"bogged", "bogged"},
        {"creaking", "creaking"},
        {"stray", "stray"},
        {"husk", "husk"},
        {"vex", "vex"},
        {"wither", "wither"},
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
    private final Deque<SoundObservation> recentSoundObservations = new ArrayDeque<>();
    /** 非敵対 Mob + 実再生されたブロック・天候・環境音。戦闘判定には使わない。 */
    private final Deque<SoundObservation> recentAmbientSoundObservations = new ArrayDeque<>();
    private long lastAudioThreatObservedTick = -1000;
    private long lastOminousSoundObservedTick = -1000;
    private long lastRainSoundObservedTick = -1000;
    private long lastThunderSoundObservedTick = -1000;
    private long lastEnderEyeLaunchObservedTick = -1000;
    private String lastOminousSoundKind = "";
    private boolean bodyHearingTruncated;
    private final BodySoundDeduplicator bodySoundDeduplicator = new BodySoundDeduplicator();

    private final double audioThreatDistance;
    private final MobClassifier mobClassifier;
    private final Logger logger;
    private final java.util.function.LongSupplier tickSource;

    public SoundObserver(double audioThreatDistance, MobClassifier mobClassifier,
            Logger logger, java.util.function.LongSupplier tickSource) {
        this.audioThreatDistance = audioThreatDistance;
        this.mobClassifier = mobClassifier;
        this.logger = logger;
        this.tickSource = tickSource;
    }

    public WorldSoundFacts worldFacts() {
        return new WorldSoundFacts(this.lastOminousSoundKind, this.lastOminousSoundObservedTick,
            this.lastRainSoundObservedTick, this.lastThunderSoundObservedTick, this.lastEnderEyeLaunchObservedTick);
    }

    public long lastThreatObservedTick() { return this.lastAudioThreatObservedTick; }
    public void markThreatObserved() { this.lastAudioThreatObservedTick = this.tickSource.getAsLong(); }

    public void resetObservationTimes() {
        this.lastAudioThreatObservedTick = -1000;
        this.lastOminousSoundObservedTick = -1000;
        this.lastRainSoundObservedTick = -1000;
        this.lastThunderSoundObservedTick = -1000;
        this.lastEnderEyeLaunchObservedTick = -1000;
        this.lastOminousSoundKind = "";
    }

    public boolean bodyHearingTruncated() { return bodyHearingTruncated; }
    public void resetRetainedSounds() {
        this.bodyHearingTruncated = false;
        this.bodySoundDeduplicator.reset();
        this.recentSoundObservations.clear();
        this.recentAmbientSoundObservations.clear();
    }

    public Map<UUID, SoundObservationPolicy.Point> currentSoundSources(ClientWorld world) {
        Set<UUID> heardEntities = new java.util.HashSet<>();
        for (SoundObservation sound : this.recentSoundObservations) {
            if (sound.entityUuid() != null) heardEntities.add(sound.entityUuid());
        }
        for (SoundObservation sound : this.recentAmbientSoundObservations) {
            if (sound.entityUuid() != null) heardEntities.add(sound.entityUuid());
        }
        Map<UUID, SoundObservationPolicy.Point> positions = new HashMap<>();
        if (heardEntities.isEmpty()) return positions;
        // 聞いた個体の現在位置だけを再確認する。新しい個体の観測や死亡判定は作らない。
        for (Entity entity : world.getEntities()) {
            if (heardEntities.contains(entity.getUuid())) {
                positions.put(entity.getUuid(), new SoundObservationPolicy.Point(entity.getX(), entity.getY(), entity.getZ()));
            }
        }
        return positions;
    }

    private Vec3d retainedSoundSource(
        ObservationOrigin origin,
        SoundObservation sound,
        Map<UUID, SoundObservationPolicy.Point> currentSoundSources
    ) {
        // 身体が聞いた音は発音時の座標を保持する。音の後のentity移動を新しい聴取位置にしない。
        if (origin.body()) return new Vec3d(sound.sourceX(),sound.sourceY(),sound.sourceZ());
        SoundObservationPolicy.Point source = SoundObservationPolicy.retainedPosition(
            sound.entityUuid(),
            new SoundObservationPolicy.Point(sound.sourceX(), sound.sourceY(), sound.sourceZ()),
            currentSoundSources,
            new SoundObservationPolicy.Point(origin.position().x, origin.position().y, origin.position().z),
            this.audioThreatDistance
        );
        return source == null ? null : new Vec3d(source.x(), source.y(), source.z());
    }

    public List<AudioThreatObservation> scanAuditoryThreats(
        ClientPlayerEntity player,
        Map<UUID, SoundObservationPolicy.Point> currentSoundSources
    ) {
        return scanAuditoryThreats(ObservationOrigin.player(player),currentSoundSources);
    }
    public List<AudioThreatObservation> scanAuditoryThreats(ObservationOrigin origin,
            Map<UUID,SoundObservationPolicy.Point> currentSoundSources) {
        Map<String, AudioThreatObservation> deduped = new LinkedHashMap<>();
        var observations = this.recentSoundObservations.iterator();
        while (observations.hasNext()) {
            SoundObservation observation = observations.next();
            if (origin.body() && SoundObservationPolicy.ageMillis(System.nanoTime(),observation.heardAtNanos()) > SOUND_OBSERVATION_TTL_TICKS*50L) {
                observations.remove(); continue;
            }
            Vec3d source = retainedSoundSource(origin, observation, currentSoundSources);
            if (source == null) {
                observations.remove();
                continue;
            }
            double distance = origin.distanceTo(source);

            String horizontal = classifyHorizontal(origin, source);
            String vertical = classifyVertical(origin, source);
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
                    observation.observedTick(),
                    SoundObservationPolicy.ageMillis(System.nanoTime(), observation.heardAtNanos()),
                    observation.identity()
                )
            );
        }

        List<AudioThreatObservation> audioThreats = new ArrayList<>(deduped.values());
        audioThreats.sort(Comparator.comparingInt(observation -> distanceBandRank(observation.distanceBand())));
        return audioThreats;
    }

    public void expireSoundObservations() {
        while (!this.recentSoundObservations.isEmpty()) {
            SoundObservation oldest = this.recentSoundObservations.peekFirst();
            if (oldest == null || this.tickSource.getAsLong() - oldest.observedTick() <= SOUND_OBSERVATION_TTL_TICKS) {
                break;
            }
            this.recentSoundObservations.removeFirst();
        }
        expireAmbientSoundObservations();
    }

    private void expireAmbientSoundObservations() {
        while (!this.recentAmbientSoundObservations.isEmpty()) {
            SoundObservation oldest = this.recentAmbientSoundObservations.peekFirst();
            if (oldest == null || this.tickSource.getAsLong() - oldest.observedTick() <= SOUND_OBSERVATION_TTL_TICKS) {
                break;
            }
            this.recentAmbientSoundObservations.removeFirst();
        }
    }

    public List<AudioThreatObservation> scanAmbientSounds(
        ClientPlayerEntity player,
        Map<UUID, SoundObservationPolicy.Point> currentSoundSources
    ) {
        return scanAmbientSounds(ObservationOrigin.player(player),currentSoundSources);
    }
    public List<AudioThreatObservation> scanAmbientSounds(ObservationOrigin origin,
            Map<UUID,SoundObservationPolicy.Point> currentSoundSources) {
        Map<String, AudioThreatObservation> deduped = new LinkedHashMap<>();
        var observations = this.recentAmbientSoundObservations.iterator();
        while (observations.hasNext()) {
            SoundObservation observation = observations.next();
            if (origin.body() && SoundObservationPolicy.ageMillis(System.nanoTime(),observation.heardAtNanos()) > SOUND_OBSERVATION_TTL_TICKS*50L) {
                observations.remove(); continue;
            }
            Vec3d source = retainedSoundSource(origin, observation, currentSoundSources);
            if (source == null) {
                observations.remove();
                continue;
            }
            double distance = origin.distanceTo(source);
            String horizontal = classifyHorizontal(origin, source);
            String vertical = classifyVertical(origin, source);
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
                    observation.observedTick(),
                    SoundObservationPolicy.ageMillis(System.nanoTime(), observation.heardAtNanos()),
                    observation.identity()
                )
            );
        }
        List<AudioThreatObservation> ambient = new ArrayList<>(deduped.values());
        ambient.sort((left, right) -> {
            int petPriority = Boolean.compare(
                left.identity() != null && left.identity().tamed(),
                right.identity() != null && right.identity().tamed()
            );
            if (petPriority != 0) return petPriority;
            int priority = Integer.compare(
                environmentSoundPriority(right.soundEvent()),
                environmentSoundPriority(left.soundEvent())
            );
            if (priority != 0) {
                return priority;
            }
            int recency = Long.compare(right.observedTick(), left.observedTick());
            if (recency != 0) {
                return recency;
            }
            return Integer.compare(
                distanceBandRank(left.distanceBand()),
                distanceBandRank(right.distanceBand())
            );
        });
        return ambient;
    }

    private int environmentSoundPriority(String soundEventId) {
        String id = String.valueOf(soundEventId).toLowerCase(Locale.ROOT);
        if (id.contains("thunder")
            || id.contains("lightning")
            || id.contains("shriek")
            || id.contains("sculk_sensor")) {
            return 3;
        }
        if (id.contains("ambient")
            || id.contains("crackle")
            || id.contains("chime")
            || id.contains("resonate")
            || id.contains("drip")
            || id.contains("note_block")
            || id.contains("bell")) {
            return 2;
        }
        if (id.endsWith(".step")
            || id.endsWith(".hit")
            || id.endsWith(".fall")
            || id.endsWith(".break")
            || id.endsWith(".place")) {
            return 0;
        }
        return 1;
    }

    public void onGlobalSoundPacket(PlaySoundS2CPacket packet) {
        MinecraftClient client = MinecraftClient.getInstance();
        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            return;
        }
        String soundEventId = soundEventId(packet.getSound());
        String ominousKind = classifyOminousSoundKind(soundEventId);
        if (ominousKind != null) {
            recordOminousSoundObservation(ominousKind);
        }
        String weatherSoundKind = classifyWeatherSoundKind(soundEventId);
        if (weatherSoundKind != null) {
            recordWeatherSoundObservation(weatherSoundKind);
        }
        if (soundEventId.contains("ender_eye.launch")) {
            // プレイヤー近傍の投擲音のみ拾う（マルチで他人の投擲を実況しない）
            double launchDx = packet.getX() - player.getX();
            double launchDy = packet.getY() - player.getY();
            double launchDz = packet.getZ() - player.getZ();
            double launchDistanceSquared = (launchDx * launchDx) + (launchDy * launchDy) + (launchDz * launchDz);
            if (launchDistanceSquared <= ENDER_EYE_LAUNCH_MATCH_RADIUS * ENDER_EYE_LAUNCH_MATCH_RADIUS) {
                this.lastEnderEyeLaunchObservedTick = this.tickSource.getAsLong();
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
        if (!resolution.threatNow()) {
            // 蜘蛛・エンダーマン等はHOSTILEカテゴリの音を出しても、まだプレイヤーを
            // 狙っていないことがある。脅威扱いにはせず、実際に聞いた音として残す。
            recordAmbientSoundObservation(
                player,
                soundEventId,
                resolution.label(),
                source,
                resolution.sourceId()
            );
            return;
        }
        recordSoundObservation(
            player,
            world,
            soundEventId,
            resolution.label(),
            source,
            resolution.spokenNameAllowed(),
            resolution.sourceId()
        );
    }

    /**
     * 発音時に身体へ届いたserver音または位置付きclient実音を、既存の音分類へ渡す。
     * ageは原発音からの経過ms。再配送のsequence判定は呼出元が所有し、ここで時刻を現在へ刷新しない。
     */
    public void observeBodySound(ObservationOrigin origin, ClientWorld world, String rawSoundId,
            SoundCategory category, Vec3d source, UUID entityId, long ageMs, boolean server) {
        if (!origin.body() || ageMs < 0 || ageMs > SOUND_OBSERVATION_TTL_TICKS*50L) return;
        String soundId = rawSoundId.contains(":") ? rawSoundId.substring(rawSoundId.indexOf(':')+1) : rawSoundId;
        LivingEntity entity = null;
        if (entityId != null) for (Entity candidate : world.getEntities()) {
            if (entityId.equals(candidate.getUuid()) && candidate instanceof LivingEntity living && living.isAlive()) {
                entity = living; break;
            }
        }
        String label = category == SoundCategory.HOSTILE ? hostileLabelFromSoundEvent(soundId) : null;
        boolean threat = false;
        if (label != null) {
            threat = entity != null ? this.mobClassifier.classifyMobDisposition(origin.threatTargetId(),entity).threatNow()
                : SoundObservationPolicy.unresolvedSoundIsThreat(label);
        } else {
            label = ambientLabelFromSoundEvent(soundId);
            if (label == null) label = environmentTypeFromPlayedSound(soundId,category);
            if (label == null && soundId.equals("entity.generic.explode")) label = "environment:explosion";
        }
        if (label == null) return;
        String id = entityId == null ? unresolvedSoundSourceId(source,soundId) : entityId.toString();
        long now = System.nanoTime();
        long incomingHeardAt = now-ageMs*1_000_000L;
        var stamp = this.bodySoundDeduplicator.reconcile(soundId,source,incomingHeardAt,id,server,now);
        long latestPairTime = Math.max(incomingHeardAt,stamp.previousHeardAt());
        // 一致候補の後に同じ地点で別の実音が鳴っていたら、その新しい観測を旧配送で削除しない。
        if (java.util.stream.Stream.concat(this.recentSoundObservations.stream(),this.recentAmbientSoundObservations.stream())
                .anyMatch(old -> (old.sourceId().equals(id) || old.sourceId().equals(stamp.previousSourceId()))
                    && old.heardAtNanos() > latestPairTime+100_000_000L)) return;
        long heardAt = stamp.heardAt();
        long heardTick = this.tickSource.getAsLong()-(now-heardAt)/50_000_000L;
        long replacedTick = Long.MIN_VALUE;
        if (stamp.previousSourceId() != null) {
            for (var old : this.recentSoundObservations) if (old.sourceId().equals(stamp.previousSourceId())) replacedTick = Math.max(replacedTick,old.observedTick());
            for (var old : this.recentAmbientSoundObservations) if (old.sourceId().equals(stamp.previousSourceId())) replacedTick = Math.max(replacedTick,old.observedTick());
            this.recentSoundObservations.removeIf(old -> old.sourceId().equals(stamp.previousSourceId()));
            this.recentAmbientSoundObservations.removeIf(old -> old.sourceId().equals(stamp.previousSourceId()));
        }
        var observation = new SoundObservation(heardTick,heardAt,entityId,id,label,soundId,
            source.x,source.y,source.z,true,entity == null ? null : mobIdentity(entity));
        Deque<SoundObservation> target = threat ? this.recentSoundObservations : this.recentAmbientSoundObservations;
        // 遅れて届いた同じ音で、既に新しい実音の記録を巻き戻さない。
        if (target.stream().anyMatch(old -> old.sourceId().equals(id) && old.heardAtNanos() > heardAt+100_000_000L)) return;
        target.removeIf(old -> old.sourceId().equals(id));
        target.addLast(observation);
        while (target.size() > 64) { target.removeFirst(); bodyHearingTruncated = true; }
        if (threat) this.lastAudioThreatObservedTick = Math.max(this.lastAudioThreatObservedTick,heardTick);
        String weather = classifyWeatherSoundKind(soundId);
        if ("rain".equals(weather)) this.lastRainSoundObservedTick = correctedBodySoundTick(this.lastRainSoundObservedTick,replacedTick,heardTick);
        if ("thunder".equals(weather)) this.lastThunderSoundObservedTick = correctedBodySoundTick(this.lastThunderSoundObservedTick,replacedTick,heardTick);
        String ominous = classifyOminousSoundKind(soundId);
        if (ominous != null && ominous.equals(this.lastOminousSoundKind) && this.lastOminousSoundObservedTick == replacedTick)
            this.lastOminousSoundObservedTick = heardTick;
        if (ominous != null && heardTick >= this.lastOminousSoundObservedTick
                && SoundObservationPolicy.replaceOminous(this.lastOminousSoundKind,
                    heardTick-this.lastOminousSoundObservedTick,ominous,OMINOUS_SOUND_TTL_TICKS)) {
            this.lastOminousSoundKind = ominous; this.lastOminousSoundObservedTick = heardTick;
        }
        if (soundId.contains("ender_eye.launch")) this.lastEnderEyeLaunchObservedTick = correctedBodySoundTick(this.lastEnderEyeLaunchObservedTick,replacedTick,heardTick);
        expireSoundObservations();
    }

    private static long correctedBodySoundTick(long previous, long replaced, long incoming) {
        return previous == replaced ? incoming : Math.max(previous,incoming);
    }

    /** clientだけで発生する音の候補条件。相対音・UI・音楽を身体位置へ置き換えない。 */
    public static boolean positionedBodySoundCategory(SoundCategory category, boolean relative) {
        return !relative && category != SoundCategory.HOSTILE && category != SoundCategory.NEUTRAL
            && category != SoundCategory.PLAYERS && category != SoundCategory.VOICE
            && category != SoundCategory.UI && category != SoundCategory.MUSIC;
    }

    private SoundSourceResolution resolveGlobalHostileSoundSource(
        ClientPlayerEntity player,
        ClientWorld world,
        Vec3d source,
        String defaultHostileLabel,
        String soundEventId
    ) {
        LivingEntity nearest = null;
        MobDisposition nearestDisposition = null;
        double nearestDistance = GLOBAL_SOUND_SOURCE_MATCH_RADIUS;
        for (Entity entity : world.getOtherEntities(null, new net.minecraft.util.math.Box(
            source.x - GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.y - GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.z - GLOBAL_SOUND_SOURCE_MATCH_RADIUS,
            source.x + GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.y + GLOBAL_SOUND_SOURCE_MATCH_RADIUS, source.z + GLOBAL_SOUND_SOURCE_MATCH_RADIUS
        ))) {
            if (!(entity instanceof LivingEntity living) || !living.isAlive()) {
                continue;
            }
            MobDisposition disposition = this.mobClassifier.classifyMobDisposition(player, living);
            // 音と違う種類の敵が近くにいても、その個体の音へすり替えない。
            if (!SoundObservationPolicy.matchesMobSound(defaultHostileLabel, disposition.type())) {
                continue;
            }
            double distance = Math.sqrt(entity.squaredDistanceTo(source));
            if (distance < nearestDistance) {
                nearest = living;
                nearestDisposition = disposition;
                nearestDistance = distance;
            }
        }
        if (nearest != null) {
            return new SoundSourceResolution(
                nearestDisposition.type(),
                nearest.getUuid().toString(),
                true,
                nearestDisposition.threatNow()
            );
        }
        // HOSTILEは音の分類で、エンダーマン等の現在の敵対を保証しない。
        // 実体未照合の中立種は聞いた音としてだけ残す。
        return new SoundSourceResolution(
            defaultHostileLabel,
            unresolvedSoundSourceId(source, soundEventId),
            true,
            SoundObservationPolicy.unresolvedSoundIsThreat(defaultHostileLabel)
        );
    }

    public void onEntitySoundPacket(PlaySoundFromEntityS2CPacket packet) {
        MinecraftClient client = MinecraftClient.getInstance();
        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            return;
        }
        String soundEventId = soundEventId(packet.getSound());
        String ominousKind = classifyOminousSoundKind(soundEventId);
        if (ominousKind != null) {
            recordOminousSoundObservation(ominousKind);
        }
        Entity entity = world.getEntityById(packet.getEntityId());
        if (packet.getCategory() != SoundCategory.HOSTILE) {
            if (entity instanceof LivingEntity living
                && living.isAlive()
                && !(entity instanceof PlayerEntity)
                && !this.mobClassifier.classifyMobDisposition(player, living).threatNow()
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
                        living.getUuid().toString(),
                        mobIdentity(living)
                    );
                }
            }
            return;
        }

        if (!(entity instanceof LivingEntity hostileEntity) || !hostileEntity.isAlive()) {
            return;
        }
        if (!this.mobClassifier.classifyMobDisposition(player, hostileEntity).threatNow()) {
            // 敵対モブ音カテゴリでも、いまは中立の個体は ambient 側へ
            String type = entityTypeName(hostileEntity);
            if (type != null && !type.isBlank()) {
                recordAmbientSoundObservation(
                    player,
                    soundEventId,
                    type,
                    new Vec3d(hostileEntity.getX(), hostileEntity.getY(), hostileEntity.getZ()),
                    hostileEntity.getUuid().toString(),
                    mobIdentity(hostileEntity)
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

    /**
     * SoundManager まで届いた実再生音を観測する。
     *
     * サーバ packet だけでは、焚き火・ポータルなどクライアントの
     * randomDisplayTick が直接鳴らす音を拾えない。敵対／中立 Mob は従来の
     * packet 経路で entity と結び付け、ここでは world の環境音だけを補完する。
     */
    public void onPlayedSound(SoundInstance sound) {
        MinecraftClient client = MinecraftClient.getInstance();
        ClientPlayerEntity player = client.player;
        if (player == null || client.world == null || sound.getId() == null) {
            return;
        }
        SoundCategory category = sound.getCategory();
        if (category == SoundCategory.HOSTILE
            || category == SoundCategory.NEUTRAL
            || category == SoundCategory.PLAYERS
            || category == SoundCategory.VOICE
            || category == SoundCategory.UI
            || category == SoundCategory.MUSIC) {
            return;
        }

        String soundEventId = sound.getId().getPath();
        String weatherKind = classifyWeatherSoundKind(soundEventId);
        if (weatherKind != null) {
            recordWeatherSoundObservation(weatherKind);
        }
        String ominousKind = classifyOminousSoundKind(soundEventId);
        if (ominousKind != null) {
            recordOminousSoundObservation(ominousKind);
        }

        String environmentType = environmentTypeFromPlayedSound(soundEventId, category);
        if (environmentType == null) {
            return;
        }
        Vec3d source = sound.isRelative()
            ? new Vec3d(player.getX(), player.getY(), player.getZ())
            : new Vec3d(sound.getX(), sound.getY(), sound.getZ());
        long bucketX = Math.round(source.x / 2.0);
        long bucketY = Math.round(source.y / 2.0);
        long bucketZ = Math.round(source.z / 2.0);
        String sourceId = "played:" + environmentType + ":" + bucketX + ":" + bucketY + ":" + bucketZ;
        recordAmbientSoundObservation(player, soundEventId, environmentType, source, sourceId);
    }

    /**
     * 実際の sound event から音源種別を機械的に得る。
     * ブロック名やカタログ説明から「鳴るはず」と推測はしない。
     */
    private String environmentTypeFromPlayedSound(String soundEventId, SoundCategory category) {
        if (soundEventId == null || soundEventId.isBlank()) {
            return null;
        }
        String id = soundEventId.toLowerCase(Locale.ROOT);
        String weather = classifyWeatherSoundKind(id);
        if (weather != null) {
            return "weather:" + weather;
        }
        if (category == SoundCategory.RECORDS) {
            return "block:jukebox";
        }
        if (id.startsWith("ambient.")) {
            String[] parts = id.split("\\.");
            if (parts.length >= 2 && !parts[1].isBlank()) {
                return "environment:" + parts[1];
            }
        }
        if (!id.startsWith("block.")) {
            return null;
        }
        String[] parts = id.split("\\.");
        if (parts.length < 3 || parts[1].isBlank()) {
            return null;
        }
        String source = switch (parts[1]) {
            case "blastfurnace" -> "blast_furnace";
            case "portal" -> "nether_portal";
            default -> parts[1];
        };
        return "block:" + source;
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
        if (distance > this.audioThreatDistance) {
            return;
        }
        if (!shouldObserveOccludedAudio(player, world, source)) {
            return;
        }

        boolean refreshed = this.recentSoundObservations.removeIf(
            existing -> existing.sourceId().equals(sourceId)
        );
        this.recentSoundObservations.addLast(
            new SoundObservation(
                this.tickSource.getAsLong(),
                System.nanoTime(),
                SoundObservationPolicy.entityUuid(sourceId),
                sourceId,
                hostileLabel,
                soundEventId,
                source.x,
                source.y,
                source.z,
                spokenNameAllowed,
                null
            )
        );
        expireSoundObservations();
        this.lastAudioThreatObservedTick = this.tickSource.getAsLong();
        if (!refreshed) {
            this.logger.info(
                "Dogido sound observed: route=hostile label={} sound={} distance={}",
                hostileLabel,
                soundEventId,
                round(distance)
            );
        }
    }

    private boolean tryRecordAmbientFromGlobalSound(
        ClientPlayerEntity player,
        ClientWorld world,
        String soundEventId,
        Vec3d source
    ) {
        String typeHint = ambientLabelFromSoundEvent(soundEventId);
        if (typeHint == null) return false;
        List<SoundObservationPolicy.MobSource> candidates = new ArrayList<>();
        Map<UUID, LivingEntity> entities = new HashMap<>();
        for (Entity entity : world.getOtherEntities(null, new net.minecraft.util.math.Box(
            source.x - 0.5, source.y - 0.5, source.z - 0.5,
            source.x + 0.5, source.y + 0.5, source.z + 0.5
        ))) {
            if (!(entity instanceof LivingEntity living) || !living.isAlive() || entity instanceof PlayerEntity) continue;
            MobDisposition disposition = this.mobClassifier.classifyMobDisposition(player, living);
            if (disposition.threatNow()) continue;
            candidates.add(new SoundObservationPolicy.MobSource(entity.getUuid(), disposition.type(),
                new SoundObservationPolicy.Point(entity.getX(), entity.getY(), entity.getZ())));
            entities.put(entity.getUuid(), living);
        }
        UUID matched = SoundObservationPolicy.uniqueAmbientSource(typeHint,
            new SoundObservationPolicy.Point(source.x, source.y, source.z), candidates);
        LivingEntity entity = matched == null ? null : entities.get(matched);
        recordAmbientSoundObservation(player, soundEventId, typeHint, source,
            matched == null ? null : matched.toString(), entity == null ? null : mobIdentity(entity));
        return true;
    }

    private String ambientLabelFromSoundEvent(String soundEventId) {
        String emitter = SoundObservationPolicy.mobSoundEmitter(soundEventId);
        if (emitter == null) return null;
        return switch (emitter) {
            case "villager", "wandering_trader", "iron_golem", "snow_golem", "allay", "axolotl",
                "cat", "ocelot", "wolf", "parrot", "chicken", "cow", "pig", "sheep",
                "horse", "donkey", "mule", "llama", "trader_llama", "skeleton_horse", "zombie_horse",
                "camel", "frog", "goat", "bee", "fox", "rabbit", "panda", "sniffer", "armadillo",
                "turtle", "dolphin", "squid", "glow_squid", "bat", "strider", "nautilus", "zombie_nautilus"
                -> emitter;
            default -> null;
        };
    }

    private String unresolvedSoundSourceId(Vec3d source, String soundEventId) {
        long bucketX = Math.round(source.x / 4.0);
        long bucketY = Math.round(source.y / 4.0);
        long bucketZ = Math.round(source.z / 4.0);
        return "pos:" + soundEventId + ":" + bucketX + ":" + bucketY + ":" + bucketZ;
    }

    private String soundEventId(net.minecraft.registry.entry.RegistryEntry<net.minecraft.sound.SoundEvent> sound) {
        return sound.getKey().map(key -> key.getValue().getPath()).orElse("unknown");
    }

    private String hostileLabelFromSoundEvent(String soundEventId) {
        // Match specific identifiers before generic ones like "zombie".
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
        String sculkKind = SoundObservationPolicy.sculkActivationKind(soundEventId);
        if (sculkKind != null) {
            return sculkKind;
        }
        if (soundEventId.contains("sonic_boom") || soundEventId.contains("sonic_charge")) {
            // ビームは専用 kind で送る（チャージ音=予兆の段階で送れば悲鳴が間に合う）。
            // サーバ側はこの kind を受けると即座に悲鳴を上げる。
            return "warden_sonic_boom";
        }
        if (soundEventId.contains("warden")) {
            if (soundEventId.contains("death")) {
                // 死亡音は presence にも討伐確認にも使わない。
                // 実際の死亡状態／論理サーバーの死亡イベントだけを根拠にする。
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

    private void recordOminousSoundObservation(String kind) {
        if (!SoundObservationPolicy.replaceOminous(this.lastOminousSoundKind,
                this.tickSource.getAsLong() - this.lastOminousSoundObservedTick, kind, OMINOUS_SOUND_TTL_TICKS)) {
            return;
        }
        this.lastOminousSoundObservedTick = this.tickSource.getAsLong();
        this.lastOminousSoundKind = kind;
    }

    private void recordWeatherSoundObservation(String kind) {
        if ("rain".equals(kind)) {
            this.lastRainSoundObservedTick = this.tickSource.getAsLong();
            return;
        }
        if ("thunder".equals(kind)) {
            this.lastThunderSoundObservedTick = this.tickSource.getAsLong();
        }
    }

    private record SoundSourceResolution(
        String label,
        String sourceId,
        boolean spokenNameAllowed,
        boolean threatNow
    ) {
    }

    private record SoundObservation(
        long observedTick,
        long heardAtNanos,
        UUID entityUuid,
        String sourceId,
        String label,
        String soundEvent,
        double sourceX,
        double sourceY,
        double sourceZ,
        boolean spokenNameAllowed,
        MobIdentityObservation identity
    ) {
    }

    private void recordAmbientSoundObservation(
        ClientPlayerEntity player,
        String soundEventId,
        String entityType,
        Vec3d source,
        String sourceId
    ) {
        recordAmbientSoundObservation(player, soundEventId, entityType, source, sourceId, null);
    }

    private void recordAmbientSoundObservation(
        ClientPlayerEntity player,
        String soundEventId,
        String entityType,
        Vec3d source,
        String sourceId,
        MobIdentityObservation identity
    ) {
        double distance = Math.sqrt(player.squaredDistanceTo(source));
        if (distance > this.audioThreatDistance) {
            return;
        }
        String id = sourceId == null || sourceId.isBlank()
            ? "ambient:" + entityType + ":" + Math.round(source.x) + ":" + Math.round(source.z)
            : sourceId;
        boolean refreshed = this.recentAmbientSoundObservations.removeIf(
            existing -> existing.sourceId().equals(id)
        );
        this.recentAmbientSoundObservations.addLast(
            new SoundObservation(
                this.tickSource.getAsLong(),
                System.nanoTime(),
                SoundObservationPolicy.entityUuid(id),
                id,
                entityType,
                soundEventId,
                source.x,
                source.y,
                source.z,
                true,
                identity
            )
        );
        expireAmbientSoundObservations();
        if (!refreshed) {
            this.logger.info(
                "Dogido sound observed: route=ambient label={} sound={} distance={}",
                entityType,
                soundEventId,
                round(distance)
            );
        }
    }
}

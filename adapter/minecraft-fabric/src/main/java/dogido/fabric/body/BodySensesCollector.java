package dogido.fabric.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.mobs.AmbientMobObserver;
import dogido.fabric.observation.mobs.BossTracker;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.observation.mobs.ThreatObserver;
import dogido.fabric.observation.player.PlayerObserver;
import dogido.fabric.observation.smell.SmellObserver;
import dogido.fabric.observation.sound.SoundObserver;
import dogido.fabric.observation.world.WorldObserver;
import dogido.fabric.protocol.EventJsonBuilder;
import java.util.Map;
import java.util.UUID;
import java.util.function.LongSupplier;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.sound.SoundInstance;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.registry.Registries;
import net.minecraft.sound.SoundCategory;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.slf4j.Logger;

/**
 * 検証済み身体snapshotを受け、既存collectorを身体の位置・眼・向きで呼ぶ。
 * 可変履歴はplayer用と完全に別所有。同じcontext/tick/revisionの感覚だけを再利用し、別姿勢へ貼り付けない。
 * 結果は既存body_observationへ添える。操作・戦闘・所持品の所有者、会話履歴、HTTP経路は変更しない。
 */
public final class BodySensesCollector {
    private final Logger logger;
    private final LongSupplier ticks;
    private final int snapshotInterval;
    private final MobClassifier classifier = new MobClassifier();
    private final AmbientMobObserver mobs;
    private final ThreatObserver threats;
    private final SoundObserver sounds;
    private final SmellObserver smell = new SmellObserver();
    private final WorldObserver worldObserver;
    private final BodyAttention attention = new BodyAttention();
    private String contextKey, frameKey;
    private ClientWorld contextWorld;
    private Vec3d previousPosition;
    private JsonObject retainedSenses;
    private JsonArray retainedCandidates = new JsonArray();
    private java.util.List<BodySpeechGaze.Target> speechVisuals = java.util.List.of();
    private long lastSoundSequence = -1;

    public BodySensesCollector(Logger logger, LongSupplier ticks, int snapshotInterval,
            double hearingDistance, ThreatObserver.Settings threatSettings) {
        this.logger = logger;
        this.ticks = ticks;
        this.snapshotInterval = snapshotInterval;
        this.mobs = new AmbientMobObserver(classifier,logger);
        this.sounds = new SoundObserver(hearingDistance,classifier,logger,ticks);
        this.threats = new ThreatObserver(threatSettings,classifier,new BossTracker(),sounds::markThreatObserved,ticks);
        this.worldObserver = new WorldObserver(snapshotInterval,new PlayerObserver(""),sounds::worldFacts);
    }

    /** 同じ身体frameは一度だけ全走査する。間のeventでも種類表は元frameと一緒に保持し、ageだけ進める。 */
    public JsonObject capture(BodyConnection.Frame frame, MinecraftClient client) {
        JsonObject observation = frame.observation().deepCopy();
        if (!"observed".equals(string(observation,"status")) || client.player == null || client.world == null) {
            reset(); return observation;
        }
        long started = System.nanoTime();
        JsonObject sample = observation.getAsJsonObject("sample");
        long serverTick = sample.get("server_tick").getAsLong();
        long outerAge = observation.get("age_ms").getAsLong();
        try {
            establishContext(sample,client.world);
            if (attention.observe(frame.autonomy())) frameKey=null;
            String key = frameKey(sample);
            if (key.equals(frameKey) && retainedSenses != null) {
                sample.add("senses",retainedSenses.deepCopy());
                return observation;
            }
            String invalid = unavailableReason(sample);
            if (invalid != null) {
                clearCollectors();
                return unavailable(observation,serverTick,invalid,key);
            }
            BodyOriginVision vision = inspectionVision(sample,frame.autonomy());
            BodyOriginVision safetyVision = vision.withoutAttention();
            ObservationOrigin origin = origin(sample,client.world,vision);
            if (origin == null || !BodyOriginVision.loaded(client.world,origin.bounds().expand(31,24,31))) {
                clearCollectors();
                return unavailable(observation,serverTick,"area_unloaded",key);
            }
            if (!frame.hearing().observed() || frame.hearing().serverTick() != serverTick) {
                clearCollectors();
                return unavailable(observation,serverTick,"not_sampled",key);
            }
            observeServerSounds(frame,origin,client.world,serverTick,outerAge);
            sounds.expireSoundObservations();
            worldObserver.probeStructureAtOrigin(client,origin,client.world,ticks.getAsLong());
            worldObserver.observeNearbyLightning(origin,client.world,ticks.getAsLong());
            // 毎fresh snapshotで種類表を実視認し直す。player側のcaptureNearbyKindsや前の種類表で補完しない。
            var passive = mobs.scanAmbientMobs(origin,client.world,true);
            // 注意を絞っても敵の取得範囲は狭めない。同じ身体姿勢・既存FOVで危険だけを別に照合する。
            var hostile = threats.scanThreats(origin(sample,client.world,safetyVision),client.world);
            JsonObject blocks = worldObserver.buildNearbyBlocks(origin,client.world,true);
            JsonArray dropped = worldObserver.buildDroppedItems(origin,client.world);
            JsonObject localWorld = worldObserver.buildWorld(origin,client.world,ticks.getAsLong());
            localWorld.addProperty("visible_villager_count",passive.stream().filter(m -> "villager".equals(m.type())).count());
            smell.observe(origin,client.player,client.world,ticks.getAsLong(),snapshotInterval);
            JsonArray heardThreats = EventJsonBuilder.buildAuditoryThreats(sounds.scanAuditoryThreats(origin,Map.of()).stream().limit(64).toList());
            JsonArray ambient = EventJsonBuilder.buildAmbientSounds(sounds.scanAmbientSounds(origin,Map.of()));
            if (vision.unavailable() || vision.exhausted() || safetyVision.unavailable() || safetyVision.exhausted()) {
                clearCollectors();
                return unavailable(observation,serverTick,vision.unavailable() || safetyVision.unavailable() ? "area_unloaded" : "budget_exhausted",key);
            }
            long ageAtCapture = outerAge+Math.max(0,(System.nanoTime()-started)/1_000_000L);
            // 内側の音年齢はsource_server_tick時点。Rustが外側ageを一度足すので、ここで二重加算しない。
            referenceSoundAges(heardThreats,ageAtCapture);
            referenceSoundAges(ambient,ageAtCapture);
            referenceWorldSoundAges(localWorld,ageAtCapture);
            JsonObject senses = new JsonObject();
            senses.addProperty("version",1);
            senses.addProperty("status","observed");
            senses.addProperty("basis","body_pose_client_world");
            senses.addProperty("source_server_tick",serverTick);
            senses.add("world",localWorld);
            senses.add("environment",origin.environment(client.world));
            senses.add("passive_mobs",EventJsonBuilder.buildPassiveMobs(passive.stream().limit(64).toList()));
            senses.add("visual_threats",EventJsonBuilder.buildVisualThreats(hostile.stream().limit(64).toList()));
            senses.add("nearby_resources",blocks.get("resources"));
            senses.add("dropped_items",dropped);
            senses.add("nearby_composition",composition(blocks,mobs.copyNearbyComposition()));
            senses.add("smell_observation",EventJsonBuilder.buildSmellObservation(smell.current()));
            senses.add("auditory_threats",heardThreats);
            senses.add("ambient_sounds",ambient);
            senses.addProperty("hearing_basis","body_server_and_positioned_client_sounds");
            senses.addProperty("hearing_truncated",frame.hearing().truncated() || sounds.bodyHearingTruncated());
            if (senses.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 65_536) {
                clearCollectors();
                return unavailable(observation,serverTick,"budget_exhausted",key);
            }
            retainedCandidates = candidates(client,frame.autonomy());
            var gaze = new java.util.ArrayList<BodySpeechGaze.Target>();
            for (var mob : mobs.bodyVisibleMobs()) gaze.add(new BodySpeechGaze.Target("body","entity",mob.entityId().toString(),mob.position()));
            for (var mob : hostile) if (mob.lineOfSight()) gaze.add(new BodySpeechGaze.Target("body","entity",mob.uuid().toString(),new Vec3d(mob.x(),mob.y(),mob.z())));
            speechVisuals=java.util.List.copyOf(gaze);
            retainedSenses = senses.deepCopy(); frameKey = key;
            sample.add("senses",senses);
            observation.addProperty("age_ms",ageAtCapture);
            return observation;
        } catch (RuntimeException | LinkageError error) {
            logger.warn("Body sensory capture unavailable: {}",error.toString());
            clearCollectors();
            return unavailable(observation,serverTick,"invalid_pose",frameKey(sample));
        }
    }

    /** 同じframeの視認候補だけを独立した制御封筒へ添える。感覚が取れないframeへ古い座標は持ち越さない。 */
    public JsonObject autonomy(BodyConnection.Frame frame, JsonObject observation) {
        JsonObject value = frame.autonomy().deepCopy();
        if (!"observed".equals(string(value,"status"))) return value;
        value.add("attention",attention.observation(value));
        value.add("candidates",new JsonArray());
        if (!"observed".equals(string(observation,"status"))) return BodyInspectProtocol.unavailable("stale_or_missing");
        value.add("age_ms",observation.get("age_ms").deepCopy());
        if (value.get("age_ms").getAsLong() >= 3_000) return BodyInspectProtocol.unavailable("stale_or_missing");
        JsonObject sample = observation.getAsJsonObject("sample");
        if (value.get("enabled").getAsBoolean() && "follow".equals(string(value,"mode"))
                && frameKey(sample).equals(frameKey) && retainedSenses != null
                && "observed".equals(string(retainedSenses,"status"))) value.add("candidates",retainedCandidates.deepCopy());
        return value;
    }
    private JsonArray candidates(MinecraftClient client,JsonObject autonomy) {
        if (!"observed".equals(string(autonomy,"status"))) return new JsonArray();
        double followDistance=autonomy.get("follow_distance").getAsDouble();
        if (!Double.isFinite(followDistance) || followDistance <= 0) return new JsonArray();
        var rows = new java.util.ArrayList<BodyInspectProtocol.Target>();
        Vec3d owner = client.player.getEntityPos();
        for (var mob : mobs.bodyVisibleMobs()) {
            if (mob.position().distanceTo(owner) > followDistance || !wireType(mob.typeId()) || boundedLabel(mob.label()).isBlank()) continue;
            rows.add(new BodyInspectProtocol.Target("e:"+mob.entityId(),"entity",mob.typeId(),boundedLabel(mob.label()),
                new BodyInspectProtocol.Point(mob.position().x,mob.position().y,mob.position().z),mob.entityId()));
        }
        for (var block : worldObserver.bodyVisibleBlocks()) {
            Vec3d position = Vec3d.ofCenter(block.position());
            if (position.distanceTo(owner) > followDistance || !wireType(block.typeId()) || boundedLabel(block.label()).isBlank()) continue;
            var point = new BodyInspectProtocol.Point(position.x,position.y,position.z);
            rows.add(new BodyInspectProtocol.Target(BodyInspectProtocol.blockId(point,block.typeId()),"block",block.typeId(),boundedLabel(block.label()),point,null));
        }
        var ownerPoint = new BodyInspectProtocol.Point(owner.x,owner.y,owner.z);
        rows.sort(java.util.Comparator.comparingDouble(row -> row.position().distance(ownerPoint)));
        JsonArray result = new JsonArray();
        rows.stream().limit(BodyInspectProtocol.MAX_CANDIDATES).forEach(row -> result.add(row.json())); return result;
    }
    private static String boundedLabel(String value) {
        StringBuilder label=new StringBuilder();
        value.codePoints().filter(code -> !Character.isISOControl(code)).limit(80).forEach(label::appendCodePoint);
        return label.toString();
    }
    private static boolean wireType(String type) { return type.length() <= 128 && type.matches("[a-z0-9_.-]+:[a-z0-9_./-]+"); }

    /** 発話用にも同じ視認済みframe・現在の実音だけを返す。新たなworld走査は行わない。 */
    public java.util.List<BodySpeechGaze.Target> speechTargets() {
        var result=new java.util.ArrayList<>(speechVisuals);
        for (var sound : sounds.spatialSounds()) result.add(new BodySpeechGaze.Target("body","sound",sound.sourceId(),sound.position()));
        return result;
    }

    /** SoundManagerの実発音候補を発生時に検証する。相対音にplayer座標を代入せず、身体までの到達範囲を見る。 */
    public void onPlayedSound(SoundInstance sound, BodyConnection.Frame frame, MinecraftClient client) {
        if (client.world == null || client.player == null || sound == null || sound.getId() == null
                || !SoundObserver.positionedBodySoundCategory(sound.getCategory(),sound.isRelative())
                || !"observed".equals(string(frame.observation(),"status"))) return;
        JsonObject sample = frame.observation().getAsJsonObject("sample");
        try {
            establishContext(sample,client.world);
            if (unavailableReason(sample) != null) return;
            ObservationOrigin origin = origin(sample,client.world,vision(sample,null));
            if (origin == null) return;
            Vec3d source = new Vec3d(sound.getX(),sound.getY(),sound.getZ());
            var event = Registries.SOUND_EVENT.get(sound.getId());
            if (event == null || !Double.isFinite(source.x) || !Double.isFinite(source.y) || !Double.isFinite(source.z)
                    || !Float.isFinite(sound.getVolume()) || sound.getVolume() <= 0) return;
            double range = event.getDistanceToTravel(sound.getVolume());
            if (!BodyHearingPolicy.arrives(origin.position(),source,range)) return;
            sounds.observeBodySound(origin,client.world,sound.getId().toString(),sound.getCategory(),source,null,0,false);
        } catch (RuntimeException | LinkageError ignored) {
            // 音取得失敗はplayer音へのfallbackを作らず、その発音だけ未取得にする。
        }
    }

    private void observeServerSounds(BodyConnection.Frame frame, ObservationOrigin origin, ClientWorld world,
            long serverTick, long outerAge) {
        for (var sound : frame.hearing().sounds().stream().sorted(java.util.Comparator.comparingLong(BodyConnection.HeardSound::sequence)).toList()) {
            if (sound.sequence() <= lastSoundSequence) continue;
            lastSoundSequence = sound.sequence();
            SoundCategory category = category(sound.category());
            if (category == null) continue;
            long age = BodyHearingPolicy.serverSoundAge(serverTick,sound.serverTick(),outerAge);
            if (age < 0) continue;
            sounds.observeBodySound(origin,world,sound.soundId(),category,
                new Vec3d(sound.x(),sound.y(),sound.z()),sound.sourceEntityId(),age,true);
        }
    }

    private static SoundCategory category(String name) {
        for (var category : SoundCategory.values()) if (category.getName().equals(name)) return category;
        return null;
    }
    private static JsonObject composition(JsonObject blocks, JsonObject mobs) {
        JsonObject composition = new JsonObject();
        composition.add("mobs",mobs.get("entries"));
        composition.add("blocks",blocks.getAsJsonObject("types").get("ids"));
        composition.add("mobs_truncated",mobs.get("truncated"));
        composition.add("blocks_truncated",blocks.getAsJsonObject("types").get("truncated"));
        return composition;
    }
    private static void referenceSoundAges(JsonArray sounds, long outerAge) {
        for (var sound : sounds) {
            var row = sound.getAsJsonObject();
            row.addProperty("heard_ago_ms",BodyHearingPolicy.referenceAge(row.get("heard_ago_ms").getAsLong(),outerAge));
        }
    }
    private static void referenceWorldSoundAges(JsonObject world, long outerAge) {
        for (String field : java.util.List.of("ominous_sound_recent_ms","rain_sound_recent_ms","thunder_sound_recent_ms","ender_eye_launch_recent_ms","nearby_lightning_strike_recent_ms"))
            if (world.has(field)) world.addProperty(field,BodyHearingPolicy.referenceAge(world.get(field).getAsLong(),outerAge));
    }
    private static String unavailableReason(JsonObject sample) {
        JsonObject vision = sample.getAsJsonObject("vision");
        return "body_downed".equals(string(vision,"reason")) ? "body_downed" : null;
    }
    private static BodyOriginVision vision(JsonObject sample, BodyAttention.View attention) {
        JsonObject pose = sample.getAsJsonObject("self_pose");
        return new BodyOriginVision(sample.get("server_tick").getAsLong(),point(pose.getAsJsonObject("eye_position")),
            pose.get("view_yaw").getAsDouble(),pose.get("view_pitch").getAsDouble(),attention);
    }
    /** 候補採取と実行前再照合が同じ範囲を使う。対象の到着や取消をここで推測しない。 */
    BodyOriginVision inspectionVision(JsonObject sample, JsonObject autonomy) {
        JsonObject pose=sample.getAsJsonObject("self_pose");
        return vision(sample,attention.view(point(pose.getAsJsonObject("eye_position")),pose.get("view_yaw").getAsDouble(),autonomy));
    }
    /** 配送受付直後の同tick再読でも全候補へ戻さないよう、frameキャッシュを無効化する。 */
    void focus(BodyInspectProtocol.Command command, long tick, long sequence, java.util.function.BooleanSupplier currentSession) {
        attention.focus(command,tick,sequence,currentSession); frameKey=null;
    }
    void inspectionResult(JsonObject result) { if (attention.result(result)) frameKey=null; }
    void clearFocus() { attention.clear(); frameKey=null; }
    private static ObservationOrigin origin(JsonObject sample, ClientWorld world, BodyOriginVision vision) {
        JsonObject context = sample.getAsJsonObject("context"), pose = sample.getAsJsonObject("self_pose");
        UUID id = UUID.fromString(context.get("body_id").getAsString());
        Vec3d position = point(pose.getAsJsonObject("position"));
        Entity body = null;
        for (Entity entity : world.getEntities()) if (entity.getUuid().equals(id)) { body = entity; break; }
        if (body == null || !body.isAlive()) return null;
        // client補間後の位置をserver実測へ混ぜず、身体の実寸だけを同じsnapshot位置へ移す。
        Box bounds = body.getBoundingBox().offset(position.subtract(body.getEntityPos()));
        return new ObservationOrigin(id,UUID.fromString(context.get("owner_id").getAsString()),position,
            point(pose.getAsJsonObject("eye_position")),pose.get("view_yaw").getAsFloat(),pose.get("view_pitch").getAsFloat(),bounds,null,vision);
    }
    private void establishContext(JsonObject sample, ClientWorld world) {
        String next = sample.getAsJsonObject("context").toString();
        Vec3d position = point(sample.getAsJsonObject("self_pose").getAsJsonObject("position"));
        if (!next.equals(contextKey) || world != contextWorld || previousPosition != null && previousPosition.distanceTo(position) > 48) {
            reset(); contextKey = next; contextWorld = world;
        }
        previousPosition = position;
    }
    private static String frameKey(JsonObject sample) {
        return sample.getAsJsonObject("context")+":"+sample.get("server_tick")+":"+sample.get("snapshot_revision");
    }
    private JsonObject unavailable(JsonObject observation, long tick, String reason, String key) {
        retainedCandidates = new JsonArray();
        retainedSenses = unavailableSenses(tick,reason); frameKey = key;
        observation.getAsJsonObject("sample").add("senses",retainedSenses.deepCopy());
        return observation;
    }
    /** API未導入でもロードできるよう、未取得JSONはBody APIを参照せず構成する。 */
    static JsonObject unavailableSenses(long tick, String reason) {
        JsonObject senses = new JsonObject();
        senses.addProperty("version",1); senses.addProperty("status","unavailable");
        senses.addProperty("basis","body_pose_client_world"); senses.addProperty("source_server_tick",tick);
        senses.addProperty("reason",reason); return senses;
    }
    static Vec3d point(JsonObject point) { return new Vec3d(point.get("x").getAsDouble(),point.get("y").getAsDouble(),point.get("z").getAsDouble()); }
    private static String string(JsonObject json, String field) { return json != null && json.has(field) ? json.get(field).getAsString() : ""; }
    private void clearCollectors() {
        mobs.resetRangeTracking(); classifier.reset(); threats.resetTracking(); threats.resetObservationTimes();
        worldObserver.resetRangeTracking(); worldObserver.resetPortalTracking(); worldObserver.resetEnvironmentHistory();
        sounds.resetObservationTimes(); sounds.resetRetainedSounds(); smell.reset();
        lastSoundSequence = -1; retainedCandidates = new JsonArray();
        speechVisuals = java.util.List.of();
    }
    public void reset() {
        attention.clear();
        clearCollectors(); contextKey = null; frameKey = null; contextWorld = null;
        previousPosition = null; retainedSenses = null;
    }
}

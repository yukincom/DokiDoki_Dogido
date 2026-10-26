package dogido.fabric;

import com.google.gson.JsonObject;
import dogido.fabric.commands.HotbarCommandExecutor;
import dogido.fabric.horse_recall.HorseRecallParticles;
import dogido.fabric.horse_recall.HorseRecallRuntime;
import dogido.fabric.display.DogidoCharacterHud;
import dogido.fabric.display.DogidoDialogueChat;
import dogido.fabric.display.DogidoWorkshopHud;
import dogido.fabric.observation.EventObservationCapture;
import dogido.fabric.observation.WorldObservationFrame;
import dogido.fabric.observation.mobs.AmbientMobObservation;
import dogido.fabric.observation.mobs.AmbientMobObserver;
import dogido.fabric.observation.mobs.BossObserver;
import dogido.fabric.observation.mobs.BossTracker;
import dogido.fabric.observation.mobs.CombatObserver;
import dogido.fabric.observation.mobs.HostileOutcomeObservation;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.observation.mobs.ThreatObservation;
import dogido.fabric.observation.mobs.ThreatObserver;
import dogido.fabric.observation.player.PlayerObserver;
import dogido.fabric.observation.smell.SmellObserver;
import dogido.fabric.observation.sound.AudioThreatObservation;
import dogido.fabric.observation.sound.SoundObservationPolicy;
import dogido.fabric.observation.sound.SoundObserver;
import dogido.fabric.observation.world.WorldObserver;
import dogido.fabric.protocol.DogidoEventClient;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.event.client.player.ClientPlayerBlockBreakEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.sound.SoundInstance;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.network.packet.s2c.play.ExplosionS2CPacket;
import net.minecraft.network.packet.s2c.play.PlaySoundFromEntityS2CPacket;
import net.minecraft.network.packet.s2c.play.PlaySoundS2CPacket;
import net.minecraft.registry.tag.DamageTypeTags;
import net.minecraft.server.network.ServerPlayerEntity;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import static dogido.fabric.observation.ObservationGeometry.bucketDistance;
import static dogido.fabric.observation.world.ShelterObservation.countNearbyBeds;

/**
 * Minecraft側の観測を、本体へ送るイベントへまとめるクライアント入口。
 * tick・音packet・実再生音・ブロック破壊・取得できた死亡通知を集め、根拠と鮮度を保って送る。
 * 発話内容と句の相談・保存はRust本体が決める。ゲーム操作は受信した型付きhotbar命令を
 * 専用経路で検査して実行し、観測を組み立てる処理から自発的に操作しない。
 */
public final class DogidoClientAdapter implements ClientModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("dogido-client-adapter");
    private static final double LARGE_POSITION_JUMP_BLOCKS = 48.0;
    private static DogidoClientAdapter INSTANCE;

    private DogidoConfig config;
    private DogidoEventClient eventClient;
    // client tickで一度採る身体実測。各イベントは同じ不変コピーを送信する。
    private final dogido.fabric.body.BodyConnection bodyConnection = new dogido.fabric.body.BodyConnection();
    private com.google.gson.JsonObject bodyObservation;
    private com.google.gson.JsonObject bodyAutonomy;
    private dogido.fabric.body.BodyInspectRuntime bodyInspect;
    private dogido.fabric.body.BodyRequestRuntime bodyRequests;
    private dogido.fabric.body.BodySensesCollector bodySenses;
    private final dogido.fabric.body.BodySpeechGaze bodySpeechGaze = new dogido.fabric.body.BodySpeechGaze(bodyConnection);
    private final SmellObserver smellObserver = new SmellObserver();

    private long tickCounter = 0;
    private long lastSnapshotTick = -1;
    private long lastThreatTick = -1;
    private long lastAudioEventTick = -1;
    private long lastAmbientMobEventTick = -1;
    private long lastAudioDispatchObservationTick = -1;
    private final MobClassifier mobClassifier = new MobClassifier();
    private final AmbientMobObserver ambientMobObserver = new AmbientMobObserver(this.mobClassifier, LOGGER);
    private String lastThreatSignature = "";
    private String lastAudioSignature = "";
    private String lastAmbientMobSignature = "";
    private DogidoWorkshopHud workshopHud;
    private DogidoCharacterHud characterHud;
    private ClientWorld displayWorld;
    private Object displayConnection;
    private String displayThreatSignature = "";
    private boolean wasDead = false;
    private PlayerObserver playerObserver;
    private WorldObserver worldObserver;
    private SoundObserver soundObserver;
    private ThreatObserver threatObserver;
    private BossObserver bossObserver;
    private CombatObserver combatObserver;
    private EventObservationCapture eventCapture;
    private HorseRecallRuntime horseRecall;
    private final BossTracker bossTracker = new BossTracker();
    private String lastDimensionId = null;
    private DogidoDialogueChat dialogueChat;
    private double lastPlayerX = Double.NaN;
    private double lastPlayerY = Double.NaN;
    private double lastPlayerZ = Double.NaN;

    @Override
    public void onInitializeClient() {
        INSTANCE = this;
        dogido.fabric.screenshot.BodyViewCapture.register(this.bodyConnection);
        this.workshopHud = DogidoWorkshopHud.register();
        this.characterHud = DogidoCharacterHud.register();
        this.config = DogidoConfig.load();
        this.playerObserver = new PlayerObserver(this.config.playerNameOverride);
        this.bodySenses = new dogido.fabric.body.BodySensesCollector(LOGGER,() -> this.tickCounter,
            this.config.snapshotIntervalTicks,this.config.audioThreatDistance,new ThreatObserver.Settings(
                this.config.maxThreatDistance,this.config.visibleThreatDistance,this.config.rearWarningDistance,
                this.config.panicDistance,this.config.visualConfirmTicks,this.config.visualHoldTicks,this.config.combatEndedQuietTicks));
        this.soundObserver = new SoundObserver(this.config.audioThreatDistance, this.mobClassifier,
            LOGGER, () -> this.tickCounter);
        this.threatObserver = new ThreatObserver(new ThreatObserver.Settings(
            this.config.maxThreatDistance, this.config.visibleThreatDistance, this.config.rearWarningDistance,
            this.config.panicDistance, this.config.visualConfirmTicks, this.config.visualHoldTicks,
            this.config.combatEndedQuietTicks), this.mobClassifier, this.bossTracker,
            this.soundObserver::markThreatObserved, () -> this.tickCounter);
        this.bossObserver = new BossObserver(this.bossTracker, () -> this.tickCounter,
            this.threatObserver::lastExplosionObservedTick);
        this.combatObserver = new CombatObserver(this.threatObserver, this.soundObserver, this.bossObserver,
            this.config.maxThreatDistance, this.config.visibleThreatDistance, () -> this.tickCounter);
        this.worldObserver = new WorldObserver(this.config.snapshotIntervalTicks, this.playerObserver,
            this.soundObserver::worldFacts);
        HotbarCommandExecutor hotbarCommands = new HotbarCommandExecutor(
            LOGGER, result -> this.eventClient.rememberCommandResult(result));
        HorseRecallParticles.registerClient();
        this.horseRecall = new HorseRecallRuntime(LOGGER, result -> this.eventClient.rememberCommandResult(result),
            ref -> this.eventClient != null && this.eventClient.isCurrent(ref));
        this.bodyInspect = new dogido.fabric.body.BodyInspectRuntime(this.bodyConnection,this.bodySenses,
            result -> this.eventClient.rememberCommandResult(result),ref -> this.eventClient != null && this.eventClient.isCurrent(ref),
            (ref,command,request) -> this.eventClient.submitBodyInspect(ref,command,request,this.bodyConnection::moveTo),
            (ref,command,request) -> this.eventClient.submitBodyInspect(ref,command,request,this.bodyConnection::lookAt));
        this.bodyRequests = new dogido.fabric.body.BodyRequestRuntime(this.bodyConnection,
            (ref,generation) -> this.eventClient != null && this.eventClient.isCurrentBodyRequest(ref,generation),
            (ref,command,sender) -> this.eventClient.submitBodyRequest(ref,command,sender),
            (ref,result) -> this.eventClient.rememberBodyRequestResult(ref,result));
        var screenshots = new dogido.fabric.screenshot.ScreenshotRuntime();
        this.eventClient = new DogidoEventClient(LOGGER,
            new DogidoEventClient.ConnectionSettings(this.config.enabled, this.config.serverBaseUrl, this.config.authToken),
            hotbarCommands::handleSelectHotbarCommand, this.horseRecall::handle, this.horseRecall::available, this.bodyInspect::handle,
            (command,ref) -> screenshots.execute(command,ref,this.eventClient),
            (command,ref) -> dogido.fabric.screenshot.BodyViewCapture.request(command,
                () -> this.eventClient.isCurrent(ref) && (command.afterLook()==null
                    || this.eventClient.isCurrentBodyRequest(ref,command.afterLook().inputGeneration())),
                () -> this.bodyRequests.lookCapture(command,ref),
                result -> this.eventClient.rememberBodyViewResult(ref,result)),
            dogido.fabric.screenshot.BodyViewCapture::acknowledged,this.bodyRequests::handle);
        this.eventClient.rememberVoicePreference(this.config.voiceEnabled());
        this.dialogueChat = DogidoDialogueChat.register(this.eventClient, this.config::saveVoiceEnabled, snapshot -> {
            var ref=this.eventClient.currentSession();
            this.bodySpeechGaze.receive(snapshot,() -> this.eventClient.isCurrent(ref));
        });
        this.eventCapture = new EventObservationCapture(this.playerObserver, this.worldObserver, this.smellObserver,
            this.ambientMobObserver, this.combatObserver, this.eventClient::nextSequence,
            () -> this.tickCounter, () -> this.bodyObservation, () -> this.bodyAutonomy);
        ClientTickEvents.END_CLIENT_TICK.register(this::onClientTick);
        ClientPlayerBlockBreakEvents.AFTER.register((world, player, pos, state) -> {
            this.worldObserver.rememberBlockBreak(state, this.tickCounter);
        });
        ServerLivingEntityEvents.AFTER_DEATH.register((entity, damageSource) -> {
            Entity attacker = damageSource.getAttacker();
            UUID playerAttackerUuid = attacker instanceof ServerPlayerEntity
                ? attacker.getUuid()
                : null;
            this.threatObserver.recordServerDeath(
                entity.getUuid(), playerAttackerUuid, damageSource.isIn(DamageTypeTags.IS_EXPLOSION));
        });
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
        instance.soundObserver.onGlobalSoundPacket(packet);
    }

    public static void recordEntitySoundPacket(PlaySoundFromEntityS2CPacket packet) {
        DogidoClientAdapter instance = INSTANCE;
        if (instance == null) {
            return;
        }
        instance.soundObserver.onEntitySoundPacket(packet);
    }

    public static void recordExplosionPacket(ExplosionS2CPacket packet) {
        DogidoClientAdapter instance = INSTANCE;
        if (instance == null) {
            return;
        }
        instance.threatObserver.onExplosionPacket(packet);
    }

    public static void recordPlayedSound(SoundInstance sound) {
        DogidoClientAdapter instance = INSTANCE;
        if (instance == null || sound == null) {
            return;
        }
        instance.soundObserver.onPlayedSound(sound);
        MinecraftClient client = MinecraftClient.getInstance();
        if (instance.bodySenses != null && client.player != null && client.world != null) {
            var frame = instance.bodyConnection.captureFrame(client.player.getUuid(),client.world.getRegistryKey().getValue().toString());
            instance.bodySenses.onPlayedSound(sound,frame,client);
        }
    }

    /**
     * このtickの観測を集め、周期snapshotと変化イベントを送る。
     * 接続・次元・大きな移動に伴う古い観測の破棄を先に行い、実死亡・爆発の通知を
     * 反映してから視覚・聴覚・匂いを組み立てる。HUDの危険表示も同じ観測で更新し、
     * 本体から返る古い表示結果が新しい危険観測を上書きしないようにする。
     */
    private void onClientTick(MinecraftClient client) {
        if (!this.config.enabled) {
            this.horseRecall.clear();
            this.bodyInspect.clear();
            this.bodyRequests.clear();
            this.bodyConnection.clearActions();
            this.bodySpeechGaze.clear();
            this.characterHud.reset();
            return;
        }

        this.horseRecall.tick(client);
        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (this.displayConnection != null && this.displayConnection != client.getNetworkHandler()) {
            // A new Minecraft connection must not inherit another world's workshop pin.
            // Dimension changes on the same connection retain the existing session.
            this.eventClient.disconnected();
            this.dialogueChat.reset();
            this.workshopHud.reset();
            this.characterHud.reset();
            this.displayConnection = null;
            this.worldObserver.resetPortalTracking();
        }
        if (player == null || world == null) {
            if (this.displayWorld != null) this.eventClient.invalidateDisplayRequests();
            this.workshopHud.reset();
            this.displayWorld = null;
            this.characterHud.reset();
            this.resetTransientState();
            return;
        }
        if (this.displayWorld != world) {
            this.ambientMobObserver.resetRangeTracking();
            this.worldObserver.resetRangeTracking();
            this.worldObserver.resetPortalTracking();
            this.eventClient.invalidateDisplayRequests();
            this.workshopHud.reset();
            this.workshopHud.synchronizeAfter(this.eventClient.currentSequence() + 1);
            this.characterHud.reset();
            this.characterHud.synchronizeAfter(this.eventClient.currentSequence() + 1);
            this.displayWorld = world;
        }
        this.displayConnection = client.getNetworkHandler();
        long displaySequenceBefore = this.eventClient.currentSequence();
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
        var bodyFrame = this.bodyConnection.captureFrame(player.getUuid(),currentDimensionId);
        if (this.tickCounter % 5 == 0) this.bodyConnection.observeOwnerWork(PlayerObserver.blockBreakingActive(client));
        this.bodyObservation = this.bodySenses.capture(bodyFrame,client);
        this.bodyAutonomy = this.bodySenses.autonomy(bodyFrame,this.bodyObservation);
        this.bodyInspect.tick();
        this.bodyRequests.tick();
        this.eventClient.ensureSession(this.playerObserver.resolvePlayerName(player),player.getUuid(),this.bodyConnection.supported());
        this.soundObserver.expireSoundObservations();
        this.worldObserver.expireBlockBreakObservations(this.tickCounter);
        this.worldObserver.observeNearbyLightning(player, world, this.tickCounter);
        this.worldObserver.probeStructureAtPlayer(client, player, world, this.tickCounter);

        this.playerObserver.observeSleep(player, world, () -> countNearbyBeds(world, player.getBlockPos()));

        this.threatObserver.observeHealth(player);

        this.threatObserver.applyServerDeathObservations(player);
        Map<UUID, HostileOutcomeObservation> newCreeperDetonations = this.threatObserver.observeCreeperDetonations(world);
        List<ThreatObservation> threats = this.threatObserver.scanThreats(player, world);
        List<HostileOutcomeObservation> newHostileDefeats = new ArrayList<>();
        for (HostileOutcomeObservation outcome : this.threatObserver.outcomes()) {
            if (
                outcome.observedTick() == this.tickCounter
                && !"creeper_detonation".equals(outcome.outcome())
            ) {
                newHostileDefeats.add(outcome);
            }
        }
        boolean captureNearbyKinds = this.eventClient.consumeFreshSnapshotRequest() || shouldSendSnapshot();
        WorldObservationFrame worldFrame = this.eventCapture.frame(player, world, captureNearbyKinds);
        this.bossObserver.observeWardenSpecialSetups(player, worldFrame, threats);
        this.bossObserver.observeTrackedWardenDefeat(world);
        this.bossObserver.observeTrackedDragonDefeat(world);
        List<ThreatObservation> visibleThreats = this.threatObserver.filterVisibleThreats(threats);
        Map<UUID, SoundObservationPolicy.Point> currentSoundSources = this.soundObserver.currentSoundSources(world);
        List<AudioThreatObservation> audioThreats = this.soundObserver.scanAuditoryThreats(player, currentSoundSources);
        List<AudioThreatObservation> ambientSounds = this.soundObserver.scanAmbientSounds(player, currentSoundSources);
        List<AudioThreatObservation> unseenAudioThreats = filterUnseenAudioThreats(visibleThreats, audioThreats);
        this.smellObserver.observe(player, world, this.tickCounter, this.config.snapshotIntervalTicks);
        List<AmbientMobObservation> ambientMobs = this.ambientMobObserver.scanAmbientMobs(player, world, captureNearbyKinds);
        this.threatObserver.updateCombatTracking(visibleThreats, audioThreats);
        boolean deadNow = this.playerObserver.isPlayerDead(player);

        if (captureNearbyKinds) {
            JsonObject snapshot = this.eventCapture.buildStatusSnapshot(player, world, worldFrame, visibleThreats, audioThreats, ambientSounds, ambientMobs);
            JsonObject ownedHorses = this.horseRecall.snapshotJson();
            if (ownedHorses != null) snapshot.add("owned_horses", ownedHorses);
            this.eventClient.postEvent(snapshot);
            this.lastSnapshotTick = this.tickCounter;
        }

        if (shouldSendThreatEvent(visibleThreats)) {
            JsonObject threatEvent = this.eventCapture.buildThreatApproaching(player, world, worldFrame, visibleThreats, audioThreats, ambientMobs);
            this.eventClient.postEvent(threatEvent);
            this.lastThreatTick = this.tickCounter;
            this.lastThreatSignature = threatSignature(visibleThreats);
        }

        if (shouldSendAudioThreatEvent(unseenAudioThreats)) {
            JsonObject audioEvent = this.eventCapture.buildHostileAudioDetected(player, world, worldFrame, threats, unseenAudioThreats, ambientMobs);
            this.eventClient.postEvent(audioEvent);
            this.lastAudioEventTick = this.tickCounter;
            this.lastAudioDispatchObservationTick = latestAudioObservationTick(unseenAudioThreats);
            this.lastAudioSignature = audioThreatSignature(unseenAudioThreats);
        }

        if (shouldSendAmbientMobEvent(ambientMobs, visibleThreats, unseenAudioThreats, deadNow)) {
            JsonObject ambientEvent = this.eventCapture.buildAmbientMobDetected(player, world, worldFrame, ambientMobs);
            this.eventClient.postEvent(ambientEvent);
            this.lastAmbientMobEventTick = this.tickCounter;
            this.lastAmbientMobSignature = ambientMobSignature(ambientMobs);
        }

        if (!newCreeperDetonations.isEmpty()) {
            JsonObject detonationEvent = this.eventCapture.buildCreeperDetonated(
                player,
                world,
                worldFrame,
                visibleThreats,
                audioThreats,
                ambientMobs,
                newCreeperDetonations.values()
            );
            this.eventClient.postEvent(detonationEvent);
        }

        if (!newHostileDefeats.isEmpty()) {
            JsonObject defeatedEvent = this.eventCapture.buildHostileDefeated(
                player,
                world,
                worldFrame,
                visibleThreats,
                audioThreats,
                ambientMobs,
                newHostileDefeats
            );
            this.eventClient.postEvent(defeatedEvent);
        }

        if (deadNow && !this.wasDead) {
            JsonObject deathEvent = this.eventCapture.buildPlayerDied(player, world, worldFrame, visibleThreats, audioThreats, ambientMobs);
            this.eventClient.postEvent(deathEvent);
            this.threatObserver.endCombat();
            this.threatObserver.clearCombatOutcomeObservations();
        } else if (this.threatObserver.shouldSendCombatEnded(visibleThreats, audioThreats, deadNow)) {
            // combat_ended 自体にも終了後の状態を載せる。旧順序では
            // this.combatObserver.buildCombat() が combat_active_hint=true を書いてしまっていた。
            this.threatObserver.endCombat();
            JsonObject combatEndedEvent = this.eventCapture.buildCombatEnded(player, world, worldFrame, ambientMobs);
            this.eventClient.postEvent(combatEndedEvent);
            this.threatObserver.clearCombatOutcomeObservations();
        }

        this.wasDead = deadNow;
        this.threatObserver.expireThreatMemory();
        // Presentation-only occlusion. Never changes combat, workshop, or world state.
        String displaySignature = threatSignature(visibleThreats) + audioThreatSignature(audioThreats)
            + visibleThreats.stream().map(threat -> threat.uuid() + ":" + threat.approaching()).toList();
        boolean displayDangerChanged = !displaySignature.equals(this.displayThreatSignature)
            || this.threatObserver.lastDamageTick() == this.tickCounter || deadNow;
        this.displayThreatSignature = displaySignature;
        long displayBarrier = this.eventClient.currentSequence() > displaySequenceBefore
            ? this.eventClient.currentSequence() : this.eventClient.currentSequence() + 1;
        boolean displayDanger = deadNow || this.threatObserver.combatActive() || !visibleThreats.isEmpty() || !audioThreats.isEmpty()
            || this.tickCounter - this.threatObserver.lastDamageTick() <= this.config.combatEndedQuietTicks;
        this.workshopHud.danger(displayDanger, displayDangerChanged, displayBarrier);
        this.characterHud.danger(displayDanger, displayDangerChanged, displayBarrier);
        // 発話の対象IDを現在の同じcollectorで照合する。固定警告にはplayer側で実視認した敵も使う。
        var gazeTargets=new java.util.ArrayList<>(this.bodySenses.speechTargets());
        for (var threat : visibleThreats) if (threat.lineOfSight())
            gazeTargets.add(new dogido.fabric.body.BodySpeechGaze.Target("player","entity",threat.uuid().toString(),new net.minecraft.util.math.Vec3d(threat.x(),threat.y(),threat.z())));
        for (var sound : this.soundObserver.spatialSounds())
            gazeTargets.add(new dogido.fabric.body.BodySpeechGaze.Target("player","sound",sound.sourceId(),sound.position()));
        this.bodySpeechGaze.tick(bodyFrame,gazeTargets);
        if (this.tickCounter % 8 == 0) this.dialogueChat.poll();
        if (this.tickCounter % 8 == 0) this.eventClient.pollWorkshopDisplay(snapshot -> {
            this.workshopHud.receive(snapshot);
            this.characterHud.receive(snapshot);
        });
    }

    private void resetTransientState() {
        if (this.bodySenses != null) this.bodySenses.reset();
        this.bodyObservation = null;
        this.bodyAutonomy = null;
        this.bodyInspect.clear();
        this.bodyRequests.clear();
        this.bodyConnection.clearActions();
        this.bodySpeechGaze.clear();
        this.ambientMobObserver.resetRangeTracking();
        this.worldObserver.resetRangeTracking();
        this.worldObserver.resetPortalTracking();
        this.tickCounter = 0;
        this.lastSnapshotTick = -1;
        this.lastThreatTick = -1;
        this.lastAudioEventTick = -1;
        this.lastAmbientMobEventTick = -1;
        this.lastAudioDispatchObservationTick = -1;
        this.threatObserver.resetObservationTimes();
        this.soundObserver.resetObservationTimes();
        this.worldObserver.resetEnvironmentHistory();
        this.bossTracker.reset();
        this.mobClassifier.reset();
        this.threatObserver.resetHealth();
        this.lastThreatSignature = "";
        this.lastAudioSignature = "";
        this.lastAmbientMobSignature = "";
        this.threatObserver.endCombat();
        this.wasDead = false;
        this.playerObserver.reset();
        this.lastDimensionId = null;
        this.lastPlayerX = Double.NaN;
        this.lastPlayerY = Double.NaN;
        this.lastPlayerZ = Double.NaN;
        this.threatObserver.resetTracking();
        this.soundObserver.resetRetainedSounds();
        this.worldObserver.resetBlockBreaks();
        this.smellObserver.reset();
    }

    private void resetThreatStateForDimensionChange() {
        this.ambientMobObserver.resetRangeTracking();
        this.worldObserver.resetRangeTracking();
        this.worldObserver.resetPortalTracking();
        this.lastThreatTick = -1;
        this.lastAudioEventTick = -1;
        this.lastAmbientMobEventTick = -1;
        this.lastAudioDispatchObservationTick = -1;
        this.threatObserver.resetObservationTimes();
        this.soundObserver.resetObservationTimes();
        this.worldObserver.resetEnvironmentHistory();
        this.bossTracker.reset();
        this.mobClassifier.reset();
        this.lastThreatSignature = "";
        this.lastAudioSignature = "";
        this.lastAmbientMobSignature = "";
        this.threatObserver.endCombat();
        this.lastPlayerX = Double.NaN;
        this.lastPlayerY = Double.NaN;
        this.lastPlayerZ = Double.NaN;
        this.threatObserver.resetTracking();
        this.soundObserver.resetRetainedSounds();
        this.worldObserver.resetBlockBreaks();
        this.smellObserver.reset();
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
            || nearest.isRearThreat()
            || threats.stream().anyMatch(ThreatObservation::fuseActive);
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
        if (ambientMobs.stream().allMatch(mob -> mob.identity().tamed()) || deadNow) {
            return false;
        }
        if (!visibleThreats.isEmpty() || !unseenAudioThreats.isEmpty()) {
            return false;
        }
        if (this.threatObserver.combatActive() || this.tickCounter - this.threatObserver.lastDamageTick() <= this.config.combatEndedQuietTicks) {
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

    private long latestAudioObservationTick(List<AudioThreatObservation> audioThreats) {
        long latest = -1;
        for (AudioThreatObservation observation : audioThreats) {
            latest = Math.max(latest, observation.observedTick());
        }
        return latest;
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
                .append(':')
                .append(threat.fuseActive() ? "fuse" : "idle")
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

}

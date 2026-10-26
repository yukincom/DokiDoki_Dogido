package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.api.BodyProtocol;
import dogido.lab.control.BodyController;
import dogido.lab.control.BodyMotion;
import dogido.lab.control.BodySafety;
import dogido.lab.control.BodyTuning;
import dogido.lab.control.CallPlacement;
import dogido.lab.control.ControlLease;
import dogido.lab.control.BodyVitals;
import dogido.lab.control.BodyReassurance;
import dogido.lab.control.DirectedMotion;
import dogido.lab.control.IdleRecovery;
import dogido.lab.control.BodyRequests;
import dogido.lab.control.OwnerWorkState;
import dogido.lab.control.BodyRecallPolicy;
import java.util.List;
import java.util.UUID;
import net.minecraft.entity.Entity;
import net.minecraft.entity.EntityType;
import net.minecraft.entity.EntityPose;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.MovementType;
import net.minecraft.entity.attribute.DefaultAttributeContainer;
import net.minecraft.entity.attribute.EntityAttributes;
import net.minecraft.entity.damage.DamageSource;
import net.minecraft.entity.damage.DamageTypes;
import net.minecraft.entity.mob.PathAwareEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.storage.ReadView;
import net.minecraft.storage.WriteView;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.Hand;
import net.minecraft.world.TeleportTarget;
import net.minecraft.world.World;

/**
 * 身体の移動・HP・消費・復活を論理サーバーで実行する。DIRECTでは行動先/休息/食事を外部が選ぶ。
 * owner UUID・世代・期限を照合し、不変snapshotだけをclient窓口へ渡す。会話判断は本線が所有する。
 */
public final class DogidoEntity extends PathAwareEntity {
    private UUID ownerId;
    private final ControlLease control = new ControlLease();
    private final BodyController controller = new BodyController(random.nextLong());
    private final BodyTuning tuning = new BodyTuning();
    private final BodyVitals vitals = new BodyVitals();
    private final BodyReassurance reassurance = new BodyReassurance();
    private final DirectedMotion directed = new DirectedMotion();
    private final IdleRecovery idleRecovery = new IdleRecovery();
    private final BodyFood food = new BodyFood(this);
    private final BodyHearing hearing = new BodyHearing();
    private final OwnerWorkState ownerWork = new OwnerWorkState();
    private final dogido.lab.control.DirectedLook directedLook = new dogido.lab.control.DirectedLook();
    private final dogido.lab.control.SpeechGaze speechGaze = new dogido.lab.control.SpeechGaze();
    private final IdleMotion.FloatCycle floatingMotion = new IdleMotion.FloatCycle();
    private BodyController.CallContinuation transferringCall;
    private boolean transferringFollow;
    private ServerPlayerEntity lastOwner;
    private String lastOwnerDimension;
    private boolean lastOwnerAvailable;
    private Vec3d motion = Vec3d.ZERO;
    private Activity activity = Activity.UNBOUND;
    private String reason = "bind_existing_body";
    private boolean localControl;
    private boolean administrativeRemoval;
    private long snapshotRevision, lastWarpTick = -100, lifecycleTick = -1, lastBodyStep = -1;

    public DogidoEntity(EntityType<? extends DogidoEntity> type, World world) {
        super(type, world);
        // 身体のyaw/pitchは下のupdateOrientationが所有する。標準LookControlの水平復帰が
        // 毎tick pitchを0へ戻すと、上下の注視が4度から進まず完了できない。
        lookControl = new net.minecraft.entity.ai.control.LookControl(this) {
            @Override public void tick() { /* 身体controllerの実角度を次tickへ保持する。 */ }
        };
        setNoGravity(true); setPersistent(); noClip = true;
    }
    public static DefaultAttributeContainer.Builder attributes() {
        return createMobAttributes().add(EntityAttributes.MAX_HEALTH,20).add(EntityAttributes.MOVEMENT_SPEED,.1);
    }
    @Override public boolean isCollidable(Entity other) { return isAlive() && other instanceof LivingEntity && !other.isSpectator(); }
    @Override public boolean isPushable() { return false; }
    @Override public boolean isInsideWall() { return false; }
    @Override public boolean canBreatheInWater() { return true; }
    @Override public boolean isFireImmune() { return false; }
    @Override public boolean canUsePortals(boolean allowVehicles) { return false; }
    @Override public boolean isInvulnerableTo(ServerWorld world, DamageSource source) {
        if (source.isOf(DamageTypes.GENERIC_KILL)) return false;
        return !damageEnabled() || vitals.downed() || source.isOf(DamageTypes.FALL) || source.isOf(DamageTypes.DROWN);
    }

    boolean downed() { return vitals.downed(); }
    private boolean damageEnabled() {
        ServerPlayerEntity player = registeredOwner();
        return player != null && player.isAlive() && player.interactionManager.getGameMode().isSurvivalLike();
    }
    @Override public boolean damage(ServerWorld world, DamageSource source, float amount) {
        if (!Float.isFinite(amount) || amount <= 0) return false;
        float before = getHealth();
        boolean applied = super.damage(world,source,amount);
        if (applied) vitals.recordDamage(serverTick(),before-getHealth(),source.getType().msgId());
        return applied;
    }
    @Override public void onDeath(DamageSource source) {
        if (source.isOf(DamageTypes.GENERIC_KILL) || ownerId == null) {
            administrativeRemoval = true; super.onDeath(source); return;
        }
        // 通常の撃破で個体・所有関係を削除しない。HP0のまま復活待ちへ移し、loot/XPを発生させない。
        vitals.collapse(tuning); directed.clear(); directedLook.clear(); idleRecovery.clear(); controller.reset(); hearing.clear();
        control.interruptAll("body_downed"); control.pause();
        activity = Activity.DOWNED; reason = "respawn_wait"; motion = Vec3d.ZERO; setVelocity(Vec3d.ZERO);
        setPose(EntityPose.DYING); snapshotRevision++;
    }
    @Override protected void updatePostDeath() {
        if (administrativeRemoval) super.updatePostDeath();
        else deathTime = Math.min(deathTime+1,20);
    }
    @Override public void kill(ServerWorld world) {
        administrativeRemoval = true;
        if (vitals.downed()) remove(RemovalReason.KILLED); else super.kill(world);
    }
    @Override public void tick() {
        super.tick();
        if (getHealth() > 0 && !administrativeRemoval) { deathTime = 0; dead = false; }
    }
    @Override protected net.minecraft.util.ActionResult interactMob(PlayerEntity player, Hand hand) {
        ItemStack stack = player.getStackInHand(hand);
        if (getEntityWorld().isClient()) return BodyFood.edible(stack) && getHealth() > 0 && getHealth() < getMaxHealth()
            ? net.minecraft.util.ActionResult.SUCCESS : net.minecraft.util.ActionResult.PASS;
        if (!player.getUuid().equals(ownerId) || !BodyFood.edible(stack) || vitals.downed() || getHealth() >= getMaxHealth())
            return net.minecraft.util.ActionResult.PASS;
        if (!(player instanceof ServerPlayerEntity serverPlayer) || serverPlayer != owner()) return net.minecraft.util.ActionResult.PASS;
        ItemStack serving = stack.copyWithCount(1);
        stack.decrementUnlessCreative(1,player);
        food.consumeServing(serving); snapshotRevision++;
        return net.minecraft.util.ActionResult.SUCCESS;
    }

    public UUID ownerId() { return ownerId; }
    BodyTuning tuning() { return tuning; }
    private void requireServerThread() {
        if (!(getEntityWorld() instanceof ServerWorld world) || !world.getServer().isOnThread())
            throw new IllegalStateException("logical_server_thread_required");
    }
    private long serverTick() { return ((ServerWorld)getEntityWorld()).getServer().getTicks(); }
    private ServerPlayerEntity registeredOwner() {
        if (ownerId == null || !(getEntityWorld() instanceof ServerWorld world)) return null;
        if (!getUuid().equals(BodyOwners.get(world.getServer()).body(ownerId))) return null;
        return world.getServer().getPlayerManager().getPlayer(ownerId);
    }
    private ServerPlayerEntity owner() {
        ServerPlayerEntity player = registeredOwner();
        return player != null && player.isAlive() && !player.isSpectator() ? player : null;
    }
    private BodyController.Owner pose(ServerPlayerEntity player) {
        return new BodyController.Owner(player.getEntityPos(),player.getYaw(),
            BodyProtocol.followBodyHeight(player.getEyeY()-player.getY(),BodyTuning.BODY_SIZE),ownerWork.avoidFront(serverTick()));
    }
    private void newEpoch() {
        Mode mode = control.mode(); boolean retreat = control.retreat();
        control.reset(ownerId == null ? null : new Context(ownerId,getUuid(),UUID.randomUUID(),
            getEntityWorld().getRegistryKey().getValue().toString()));
        // ローカルのFOLLOW等は接続し直しても維持する。外部の期限付き要求は新しい世代で再照合する。
        if (localControl) control.local(mode,retreat);
        controller.reset(); directed.clear(); directedLook.clear(); idleRecovery.clear(); food.clear(); hearing.clear(); ownerWork.clear(); speechGaze.clear(); motion = Vec3d.ZERO;
        floatingMotion.reset(); reassurance.reset();
        transferringCall = null; transferringFollow = false;
        snapshotRevision++;
    }
    public String bind(ServerPlayerEntity player) {
        requireServerThread();
        if (!player.isAlive() || player.isSpectator()) return "owner_unavailable";
        if (player.getEntityWorld() != getEntityWorld() || squaredDistanceTo(player) > 64) return "body_must_be_within_8_blocks";
        if (ownerId != null && !ownerId.equals(player.getUuid())) return "owned_by_another_player";
        if (!BodyOwners.get(player.getEntityWorld().getServer()).claim(player.getUuid(),getUuid())) return "owner_already_has_body";
        ownerId = player.getUuid(); lastOwner = player; localControl = true; newEpoch(); localMode(Mode.FOLLOW,false);
        return "bound";
    }
    public void unbind(ServerPlayerEntity player) {
        requireOwner(player);
        BodyOwners.get(player.getEntityWorld().getServer()).release(ownerId,getUuid());
        ownerId = null; lastOwner = null; localControl = false; newEpoch(); activity = Activity.UNBOUND; reason = "unbound";
    }
    private void requireOwner(ServerPlayerEntity player) {
        requireServerThread();
        if (player != owner()) throw new IllegalArgumentException("owner_required");
    }
    /** 同じ所有者・身体世代の現在作業観測だけを受ける。行動の有効化や保存は行わない。 */
    void observeOwnerWork(ServerPlayerEntity player, OwnerWorkObservation observation) {
        requireOwner(player); prepareLifecycle();
        ownerWork.observe(observation,control.context(),serverTick());
    }
    /** 発話中の点を現在の身体世代へ照合する。移動目標やaction結果は変更しない。 */
    void observeSpeechGaze(ServerPlayerEntity player, SpeechGazeObservation observation) {
        requireOwner(player); prepareLifecycle();
        speechGaze.observe(observation,control.context(),serverTick(),getEyePos());
    }
    public StateResult applyState(ServerPlayerEntity player, StateRequest request) {
        requireOwner(player); prepareLifecycle();
        Mode before = control.mode();
        String problem = BodyRequests.state(request,vitals.downed());
        StateResult result = control.apply(request,serverTick(),problem);
        if (result.outcome() == Outcome.ACCEPTED) {
            localControl = false;
            transferringCall = null; transferringFollow = false;
            if (before != control.mode()) {
                directed.clear(); idleRecovery.clear(); controller.reset(); control.completeAction(Outcome.INTERRUPTED,"control_mode_changed");
            }
            if (!control.enabled() || control.retreat()) {
                directed.clear(); idleRecovery.clear();
                controller.cancelCall(control.retreat() ? "retreat_has_priority" : "control_inactive");
                control.completeAction(Outcome.INTERRUPTED,control.retreat() ? "retreat_has_priority" : "control_inactive");
            }
        }
        snapshotRevision++; return result;
    }
    public ActionResult requestAction(ServerPlayerEntity player, ActionRequest request) {
        requireOwner(player); prepareLifecycle();
        // 同じserver呼出しの実測から一度だけ目的地を作り、受付検査と実行へ同じ位置を渡す。
        Vec3d destination = actionDestination(request,player);
        var admission = control.submit(request,serverTick(),actionAdmission(request,destination));
        ActionResult result = admission.result();
        if (admission.execute()) {
            if (request.action() == Action.LOOK_AT) {
                // 既存LOOK_ATの向きだけを更新し、移動・慣性・呼び寄せ・追従の状態は続ける。
                directedLook.begin(result.actionId(),request.destination(),serverTick());
            } else {
                idleRecovery.clear();
                transferringCall=null;transferringFollow=false;
                if (request.action() == Action.CALL) {
                    directed.clear(); controller.call(result.actionId(),pose(player),serverTick());
                } else if (request.action() == Action.MOVE_TO && control.mode() == Mode.FOLLOW) {
                    directed.clear();
                    controller.inspect(result.actionId(),destination,request.movePurpose(),pose(player),
                        serverTick()+BodyController.INSPECTION_MAX_TICKS);
                } else {
                    controller.reset();
                    Position p = request.destination();
                    directed.begin(result.actionId(),request.action(),p == null ? null : new Vec3d(p.x(),p.y(),p.z()));
                    if (request.action() == Action.EAT) {
                        String outcome = food.consume(request.foodId());
                        control.completeAction(outcome.equals("food_consumed") ? Outcome.SUCCEEDED : Outcome.REJECTED,outcome);
                    }
                }
            }
        }
        snapshotRevision++; return result;
    }
    /** ownerへの位置は外部から受けず、現物の身体・owner・敵・空間を照合した有界候補から選ぶ。 */
    private Vec3d actionDestination(ActionRequest request, ServerPlayerEntity player) {
        if (request == null || request.action() != Action.MOVE_TO) return null;
        if (request.movePurpose() == MovePurpose.INSPECT) {
            Position p = request.destination();
            return p == null ? null : new Vec3d(p.x(),p.y(),p.z());
        }
        if (request.movePurpose() == null || control.mode() != Mode.FOLLOW || player.getEntityWorld() != getEntityWorld()) return null;
        BodySpace space = new BodySpace((ServerWorld)getEntityWorld(),this);
        return BodyController.ownerDestination(pose(player),getEntityPos(),request.movePurpose(),
            (p,walls) -> space.target(p,walls,player,false),space.safety(player,getEntityPos()));
    }
    private String actionAdmission(ActionRequest request, Vec3d target) {
        String problem = BodyRequests.action(request,control.mode(),vitals.downed(),new Position(getX(),getY(),getZ()));
        if (problem != null) return problem;
        if (request == null || request.action() == null) return null; // 外形はControlLeaseで検査する。
        if (request.action() == Action.MOVE_TO) {
            BodySpace space = new BodySpace((ServerWorld)getEntityWorld(),this);
            if (control.mode() == Mode.FOLLOW) {
                ServerPlayerEntity player = owner();
                if (player == null || player.getEntityWorld() != getEntityWorld()) return "owner_unavailable";
                // 転送直後はactivityの更新前でも、未到着の追従を観察命令で横取りさせない。
                if (controller.following() || transferringFollow) return "distance_follow_has_priority";
                boolean approachingOwner = request.movePurpose() != MovePurpose.INSPECT;
                String inspectionProblem = approachingOwner
                    ? BodyRequests.ownerApproach(controller.calling(),control.hasActiveAction(),activity,
                        getEntityPos().distanceTo(player.getEntityPos()),tuning.emergencyFollowDistance(),request.movePurpose(),pose(player).working())
                    : BodyRequests.inspection(controller.calling(),control.hasActiveAction(),activity,
                        getEntityPos().distanceTo(player.getEntityPos()),target.distanceTo(player.getEntityPos()),tuning.followDistance);
                if (inspectionProblem != null) return inspectionProblem;
                if (target == null) return "owner_approach_target_unsafe";
                if (!approachingOwner && !BodyProtocol.inspectionHeightAllowed(target.y-player.getY()-pose(player).bodyHeight())) return "inspection_outside_follow_height";
                // 実帰還から顔へ進む一手だけ到着後の間を越えられる。作業・優先行動は上で、安全は下で再照合する。
                String pause = controller.inspectionPauseReason(request.movePurpose(),serverTick());
                if (pause != null) return pause;
                // 見回しだけでは観察を拒否しない。現在視認・安全性をadapterと上記で再照合し、inspect受付で見回しを止める。
                BodySafety safety = space.safety(player,getEntityPos());
                if (!space.target(target,false,player,false) || !safety.safeTarget(target)) return "inspection_target_unsafe";
                if (safety.threatened(getEntityPos())) return "danger_has_priority";
            }
            if (!space.loaded(BodyMotion.box(target))) return "destination_not_loaded";
            // DIRECTの目的地は従来どおり。FOLLOWの観察とowner接近に上記の安全・優先判定を適用する。
        }
        return request.action() == Action.EAT ? food.admission(request.foodId()) : null;
    }
    void localMode(Mode mode, boolean retreat) {
        requireServerThread(); localControl = true; control.local(mode,retreat);
        directed.clear(); idleRecovery.clear(); controller.reset(); control.completeAction(Outcome.INTERRUPTED,"local_control_changed");
        transferringCall = null; transferringFollow = false;
        if (retreat || mode == Mode.HOLD) {
            controller.cancelCall(retreat ? "retreat_has_priority" : "local_hold");
            control.completeAction(Outcome.INTERRUPTED,retreat ? "retreat_has_priority" : "local_hold");
        }
        snapshotRevision++;
    }
    void localSafe() { localMode(control.mode(),false); }
    void localRetreat() { localMode(control.mode(),true); }
    ActionResult localCall(ServerPlayerEntity player) { return localCall(player,UUID.randomUUID()); }
    ActionResult localCall(ServerPlayerEntity player, UUID actionId) {
        requireOwner(player); prepareLifecycle();
        localMode(Mode.FOLLOW,control.retreat());
        long sequence = Math.max(serverTick(),control.lastActionSequence()+1);
        return requestAction(player,new ActionRequest(control.context(),sequence,actionId,serverTick(),100,Action.CALL));
    }
    ActionResult recalledResult(ServerPlayerEntity player, UUID actionId) {
        requireOwner(player); prepareLifecycle();
        return control.actionResult(actionId);
    }
    ActionResult localAction(ServerPlayerEntity player, Action action, Position destination, String foodId) {
        requireOwner(player); prepareLifecycle();
        if (vitals.downed()) return new ActionResult(null,-1,Outcome.REJECTED,"body_downed");
        localMode(Mode.DIRECT,false);
        long sequence = Math.max(serverTick(),control.lastActionSequence()+1);
        return requestAction(player,new ActionRequest(control.context(),sequence,UUID.randomUUID(),serverTick(),100,action,destination,foodId));
    }
    FoodObservation foodObservation() { requireServerThread(); return food.observe(); }
    /** 実音が発生した瞬間の身体足元で到達を判定する。観測取得時の移動先へ過去音を付け替えない。 */
    void hearSound(long tick, Position source, String soundId, String category,
            float volume, Float pitch, double range, UUID sourceEntityId) {
        requireServerThread(); prepareLifecycle();
        ServerPlayerEntity player = owner();
        if (player == null || player.getEntityWorld() != getEntityWorld() || vitals.downed() || isRemoved()) {
            hearing.clear(); return;
        }
        if (hearing.hear(control.context(),tick,new Position(getX(),getY(),getZ()),source,soundId,category,
                volume,pitch,range,sourceEntityId)) snapshotRevision++;
    }
    /** ENTITY_UNLOAD時に未配送の音も破棄し、同じ個体の再読込みへ保持を残さない。 */
    void clearHearing() { hearing.clear(); }
    public Snapshot snapshot() {
        requireServerThread(); prepareLifecycle();
        Vec3d p = getEntityPos();
        ServerPlayerEntity player = registeredOwner();
        ServerPlayerEntity presentOwner = owner();
        long tick = serverTick();
        PoseObservation pose = BodyPoseObservation.capture(this,presentOwner);
        VisionObservation vision = BodyVision.capture(this,presentOwner,pose,tick);
        boolean visible = vision.playerVisibility() == Visibility.VISIBLE;
        boolean sameWorld = presentOwner != null && presentOwner.getEntityWorld() == getEntityWorld();
        Reassurance calm = reassurance.reading(sameWorld ? p.distanceTo(presentOwner.getEntityPos()) : 0,
            visible,visible && BodyVision.faceVisible(this,presentOwner,pose,tick),sameWorld && ownerWork.avoidFront(tick));
        // 姿勢と視覚を同じserver呼出しで採り、旧視界へ新しいsnapshot時刻を付けない。
        return new Snapshot(BodyProtocol.VERSION,presentOwner == null ? Presence.OWNER_UNAVAILABLE : Presence.PRESENT,control.context(),player == null ? null : player.getEntityWorld().getRegistryKey().getValue().toString(),tick,snapshotRevision,
            new Position(p.x,p.y,p.z),getBodyYaw(),activity,reason,control.revision(),control.stateResult(),control.actionResult(),Sensors.BODY_VISION_AND_HEARING,
            vitals.snapshot(getHealth(),getMaxHealth(),damageEnabled(),player == null ? null : player.interactionManager.getGameMode().getId()),
            presentOwner == null || vitals.downed() ? new FoodObservation(ObservationStatus.UNAVAILABLE,tick,List.of(),false) : food.observe(),
            pose,vision,
            hearing.observe(control.context(),tick,new Position(p.x,p.y,p.z),
                presentOwner != null && presentOwner.getEntityWorld() == getEntityWorld() && !vitals.downed()),
            new ControlObservation(control.enabled(),control.mode(),tuning.followDistance,tuning.warpDistance,calm),control.lookResults());
    }

    private void prepareLifecycle() {
        ServerPlayerEntity player = registeredOwner();
        String dimension = player == null ? null : player.getEntityWorld().getRegistryKey().getValue().toString();
        boolean available = player != null && player.isAlive() && !player.isSpectator();
        // Dimension transfer retains the player object. Check identity before the same-tick fast path.
        if (lastOwner != player || !java.util.Objects.equals(lastOwnerDimension,dimension) || lastOwnerAvailable != available
                || ownerId != null && control.context() == null) {
            BodyController.CallContinuation continuing = null;
            boolean continuingFollow = false;
            if (BodyRecallPolicy.continueAcrossDimension(localControl,lastOwner == player,
                    lastOwnerAvailable,available,lastOwnerDimension,dimension)) {
                continuing = controller.continuation();
                if (continuing == null) continuing = transferringCall;
                continuingFollow = control.mode() == Mode.FOLLOW && !control.retreat()
                    && (controller.following() || transferringFollow);
            }
            newEpoch(); lastOwner = player; lastOwnerDimension = dimension; lastOwnerAvailable = available;
            transferringCall = continuing;
            transferringFollow = continuingFollow;
            if (!available) { activity = Activity.OWNER_ABSENT; reason = "owner_unavailable"; }
        }
        if (lifecycleTick == serverTick()) return;
        lifecycleTick = serverTick();
        control.expire(serverTick());
        if (!control.enabled()) {
            directed.clear();
            if (!IdleRecovery.controlAllowsRecovery(control,localControl)) idleRecovery.clear();
        }
    }

    /** loaded registryから毎server tickに一度だけ実行する。モデル通信が止まっても身体の時間は進む。 */
    void serverStep() {
        requireServerThread(); prepareLifecycle();
        if (lastBodyStep == serverTick()) return;
        lastBodyStep = serverTick();
        if (administrativeRemoval) return;
        // DOWNED中もowner位置と復活待ち時間は変わる。早期returnより先に観測revisionを進める。
        snapshotRevision++;
        ServerPlayerEntity player = owner();
        BodySpace space = new BodySpace((ServerWorld)getEntityWorld(),this);
        boolean insideSolid = space.insideSolid(getBoundingBox());
        var step = vitals.step(player != null,damageEnabled(),insideSolid,
            control.enabled() && control.mode() == Mode.DIRECT && directed.resting() && motion.length() < .03,
            getHealth(),getMaxHealth(),tuning);
        if (step.respawnReady()) { respawnAtOwner(player); return; }
        if (vitals.downed()) {
            activity = Activity.DOWNED; reason = player == null ? "respawn_owner_unavailable" : "respawn_wait";
            updateReassurance(player,space,false); return;
        }
        if (step.wallDamage() > 0) damage((ServerWorld)getEntityWorld(),getDamageSources().inWall(),step.wallDamage());
        if (!vitals.downed() && step.recovery() > 0) heal(step.recovery());
        // noClipでは通常のblock接触tickが走らない。接触は毎tick観測し、連続被害はMinecraftの無敵時間に従う。
        if (damageEnabled() && !vitals.downed()) {
            String contact = space.contactHazard(getBoundingBox());
            if (contact != null) switch (contact) {
                case "lava" -> damage((ServerWorld)getEntityWorld(),getDamageSources().lava(),4);
                case "fire" -> damage((ServerWorld)getEntityWorld(),getDamageSources().inFire(),1);
                case "campfire" -> damage((ServerWorld)getEntityWorld(),getDamageSources().campfire(),2);
                default -> { }
            }
        }
        if (!damageEnabled()) extinguish();
        // HP判定の壁内実測を共有し、平常の待機では追加のワールド走査を行わない。
        idleRecovery.update(getEntityPos(),idleRecoveryAllowed(player),insideSolid,serverTick(),
            box -> space.target(box,false,player,false));
        updateReassurance(player,space,!insideSolid && !vitals.downed());
        if (!vitals.downed()) followOwnerWorld();
    }
    /** 安心度の時計はserverStepだけが進める。snapshot再読・要求受理・実行成功で加点しない。 */
    private void updateReassurance(ServerPlayerEntity player, BodySpace space, boolean unobstructed) {
        long tick = serverTick();
        boolean available = player != null && player.getEntityWorld() == getEntityWorld();
        PoseObservation pose = BodyPoseObservation.capture(this,available ? player : null);
        VisionObservation vision = BodyVision.capture(this,available ? player : null,pose,tick);
        boolean visible = vision.playerVisibility() == Visibility.VISIBLE;
        boolean safe = available && unobstructed && !control.retreat() && activity != Activity.RETREATING
            && !vitals.recentlyDamaged(tick,40) && !space.safety(player,getEntityPos()).threatened(getEntityPos());
        reassurance.step(tick,getEntityPos(),available ? player.getEntityPos() : null,visible,
            visible && BodyVision.faceVisible(this,player,pose,tick),available && ownerWork.avoidFront(tick),safe);
    }
    private boolean idleRecoveryAllowed(ServerPlayerEntity player) {
        return IdleRecovery.eligible(control,localControl,activity)
            && player != null && player.getEntityWorld() == getEntityWorld()
            && !vitals.downed() && !directed.moving() && !controller.calling();
    }
    private void respawnAtOwner(ServerPlayerEntity player) {
        if (player == null) { reason = "respawn_owner_unavailable"; return; }
        Vec3d destination = player.getEntityPos();
        if (!new BodySpace(player.getEntityWorld(),this).loaded(BodyMotion.box(destination))) {
            reason = "respawn_destination_not_loaded"; return;
        }
        // HP0個体を転送できる状態へ戻す。転送不成立ならHP0/復活待ちへ戻し、回数を増やさない。
        setHealth(getMaxHealth()); dead = false; deathTime = 0; setPose(EntityPose.STANDING);
        Entity moved = teleportTo(new TeleportTarget(player.getEntityWorld(),destination,Vec3d.ZERO,player.getYaw(),0,TeleportTarget.NO_OP));
        if (!(moved instanceof DogidoEntity next)) { setHealth(0); setPose(EntityPose.DYING); reason = "respawn_transfer_failed"; return; }
        next.vitals.revived(); next.administrativeRemoval = false; next.setHealth(next.getMaxHealth());
        next.dead = false; next.deathTime = 0; next.hurtTime = 0; next.timeUntilRegen = 0;
        next.setPose(EntityPose.STANDING); next.extinguish(); next.clearStatusEffects(); next.localControl = true;
        next.newEpoch(); next.localMode(Mode.FOLLOW,false); next.activity = Activity.HOLDING; next.reason = "respawned_at_owner";
        next.lastBodyStep = serverTick(); next.lastWarpTick = serverTick();
    }

    /** Runs on server ticks as well as entity ticks, so a loaded non-ticking old chunk can transfer. */
    void followOwnerWorld() {
        requireServerThread(); prepareLifecycle();
        ServerPlayerEntity player = owner();
        if (player == null || !control.enabled() || control.mode() == Mode.HOLD && !control.retreat() && !controller.calling()) return;
        boolean otherWorld = player.getEntityWorld() != getEntityWorld();
        if (!otherWorld && transferringCall != null) {
            // 転送待ち中にownerが元の世界へ戻った場合も、距離の早期returnより先に再接近を再開する。
            BodyController.CallContinuation continuing = transferringCall;
            transferringCall = null;
            resumeLocalCall(player,continuing);
        }
        if (!otherWorld && transferringFollow) {
            // 次元移動待ちの間にownerが戻っても、16block以内というだけでは未到着の追従を消さない。
            transferringFollow = false;
            if (localControl && control.mode() == Mode.FOLLOW && !control.retreat() && !controller.calling()) controller.resumeFollowing();
        }
        if (!otherWorld && control.mode() == Mode.DIRECT) return;
        if (!otherWorld && squaredDistanceTo(player) <= tuning.warpDistance*tuning.warpDistance) return;
        if (serverTick()-lastWarpTick < 40) return;
        BodySpace space = new BodySpace(player.getEntityWorld(),this);
        BodySafety safety = control.mode() == Mode.FOLLOW ? space.safety(player,player.getEntityPos()) : BodySafety.EMPTY;
        Vec3d destination = CallPlacement.warpCandidates(pose(player),control.retreat()).stream()
            .filter(p -> !BodyMotion.nearFront(p,player.getEntityPos(),player.getYaw())
                && space.target(p,false,player,control.retreat()) && safety.safeTarget(p))
            .findFirst().orElse(null);
        if (destination == null) { reason = "no_safe_warp_destination"; return; }
        boolean wasLocal = localControl; Mode mode = control.mode(); boolean retreat = control.retreat();
        BodyController.CallContinuation continuing = wasLocal ? controller.continuation() : null;
        if (continuing == null && wasLocal) continuing = transferringCall;
        long now = serverTick();
        Entity moved = teleportTo(new TeleportTarget(player.getEntityWorld(),destination,Vec3d.ZERO,getYaw(),0,TeleportTarget.NO_OP));
        if (moved instanceof DogidoEntity next) {
            next.localControl = wasLocal; next.lastOwner = player; next.newEpoch();
            if (wasLocal) next.localMode(mode,retreat);
            // 転送先のowner次元・生存状態も揃えてからcontextを採る。要求作成後の再世代化を防ぐ。
            next.prepareLifecycle();
            next.resumeLocalCall(player,continuing);
            // warpは安全位置へ着地するだけ。全世代初期化が終わってから通常の接近と到着確認を再開する。
            if (wasLocal && mode == Mode.FOLLOW && !retreat && !next.controller.calling()) next.controller.resumeFollowing();
            next.lastWarpTick = now; next.reason = otherWorld ? "followed_owner_dimension" : "caught_up";
        }
    }

    /** 転送後の実体・確定contextへ同一CALLを再照合する。元の開始tickを保ち、ワープで期限を延長しない。 */
    private void resumeLocalCall(ServerPlayerEntity player, BodyController.CallContinuation continuing) {
        long now = serverTick();
        if (continuing == null || !localControl || now-continuing.started() >= BodyTuning.CALL_TIMEOUT_TICKS) return;
        long sequence = Math.max(now,control.lastActionSequence()+1);
        requestAction(player,new ActionRequest(control.context(),sequence,continuing.id(),now,100,Action.CALL));
        controller.retainCallDeadline(continuing);
    }

    @Override public void travel(Vec3d input) {
        noClip = true; setNoGravity(true);
        if (!(getEntityWorld() instanceof ServerWorld world) || !isAlive()) return;
        prepareLifecycle();
        ServerPlayerEntity player = owner();
        if (player != null && player.getEntityWorld() != world) player = null;
        ServerPlayerEntity currentOwner = player;
        Vec3d position = getEntityPos();
        BodySpace space = new BodySpace(world,this);
        boolean direct = control.mode() == Mode.DIRECT;
        boolean safeFollow = control.mode() == Mode.FOLLOW && control.enabled() && player != null;
        BodySafety safety = safeFollow ? space.safety(player,position) : BodySafety.EMPTY;
        BodyController.Plan plan = direct && player != null && control.enabled() && !controller.calling()
            ? directed.plan(position) : controller.plan(position,player == null ? null : pose(player),serverTick(),control.enabled(),
                control.mode(),control.retreat(),tuning,(p,walls) -> direct ? space.loaded(BodyMotion.box(p))
                    : space.target(p,walls,currentOwner,control.retreat()) && safety.safeTarget(p),safety);
        // 向き指定は移動計画を置き換えない。コードによる追従・呼び寄せ・退避と同じtickで進める。
        directedLook.tick(serverTick());
        activity = ownerId == null ? Activity.UNBOUND : plan.activity();
        reason = ownerId == null ? "bind_existing_body" : control.expired() ? "control_lease_expired" : plan.reason();
        if (controller.takeArrived()) control.completeAction(Outcome.SUCCEEDED,"arrived");
        if (directed.takeArrival()) control.completeAction(Outcome.SUCCEEDED,"arrived");
        BodyController.InspectionResult inspection = controller.takeInspectionResult();
        if (inspection != null) control.completeAction(inspection.id(),inspection.outcome(),inspection.reason());
        String callEnd = controller.takeCallEnd();
        if (callEnd != null) control.completeAction(callEnd.equals("call_timeout") ? Outcome.EXPIRED : Outcome.INTERRUPTED,callEnd);
        double speed = tuning.speedFor(activity,position.distanceTo(plan.target()),player == null ? 0 : position.distanceTo(player.getEntityPos()));
        boolean active = activity == Activity.FOLLOWING || activity == Activity.EXPLORING || activity == Activity.CALLING || activity == Activity.RETREATING || activity == Activity.MOVING;
        if (!idleRecoveryAllowed(player)) idleRecovery.clear();
        Vec3d recoveryTarget = idleRecovery.target(position);
        if (recoveryTarget != null) reason = "idle_block_recovery";
        boolean floating = recoveryTarget == null && activity != Activity.RETREATING && !plan.reason().equals("no_safe_target");
        Vec3d baseTarget = recoveryTarget != null ? recoveryTarget : active && !plan.reason().equals("no_safe_target")
            ? plan.target() : position;
        java.util.function.Predicate<Vec3d> floatingPositionAllowed = p ->
            space.target(p,false,currentOwner,false) && safety.safeTarget(p);
        // 現在の位相へ目標も合わせるが、通常目標が安全でも低い天井では補正先が埋まるため再照合する。
        Vec3d target = baseTarget;
        if (floating && active) {
            var shifted = BodyMotion.hoverTarget(baseTarget,floatingMotion.offset(),floatingPositionAllowed);
            target = shifted.position(); floating = shifted.applied();
        }
        Box path = getBoundingBox().expand(speed+.1);
        List<Box> obstacles = direct ? space.physicalObstacles(path) : space.motionObstacles(path,currentOwner,control.retreat());
        // 普段の追従と呼び寄せは、既存の作業観測が有効なら作業正面を横切らず迂回する。
        boolean workAvoidFront = (activity == Activity.CALLING || activity == Activity.FOLLOWING
                || activity == Activity.MOVING && controller.approachingOwner())
            && currentOwner != null && ownerWork.avoidFront(serverTick());
        boolean allowFront = !workAvoidFront && (safeFollow || direct || !active || activity == Activity.CALLING || currentOwner == null);
        java.util.function.Predicate<Vec3d> stepAllowed = delta ->
            space.loaded(getBoundingBox().union(getBoundingBox().offset(delta)))
                && safety.allowsStep(position,delta)
                && (allowFront || BodyMotion.frontClear(position,delta,currentOwner.getEntityPos(),currentOwner.getYaw()));
        java.util.function.Function<Vec3d,Vec3d> steerTarget = destination -> BodyMotion.steer(position,
            allowFront ? destination : BodyMotion.aroundFront(position,destination,currentOwner.getEntityPos(),currentOwner.getYaw()),
            speed,motion,obstacles,stepAllowed);
        Vec3d locomotion = safeFollow && plan.reason().equals("no_safe_target") ? Vec3d.ZERO
            : steerTarget.apply(target);
        if (floating && active && !floatingPositionAllowed.test(position.add(locomotion))) {
            // 波で補正した目標への途中も空間を検査する。入れない場合は補正前の経路へ戻し、
            // 元からある壁抜け移動へ浮遊由来の上下変化を混ぜない。
            target = baseTarget; locomotion = steerTarget.apply(baseTarget); floating = false;
        }
        Vec3d step = locomotion;
        if (floating) {
            var hover = BodyMotion.withHover(position,locomotion,floatingMotion.nextStep(),obstacles,
                delta -> stepAllowed.test(delta) && floatingPositionAllowed.test(position.add(delta)));
            step = hover.movement();
            if (hover.applied()) floatingMotion.advance();
        }
        move(MovementType.SELF,step);
        // 慣性には基本移動だけを戻す。上下波を次tickへ二重加算して振幅を増やさない。
        motion = locomotion; setVelocity(Vec3d.ZERO); fallDistance = 0;
        if (direct && !directedLook.active() && step.length() < .03 && (activity == Activity.RESTING || activity == Activity.HOLDING))
            control.completeAction(Outcome.SUCCEEDED,activity == Activity.RESTING ? "resting" : "waiting");
        if (step.lengthSquared() < 1e-9 && active && position.distanceTo(target) > .4) reason = "path_blocked";
        Vec3d facing = activity == Activity.CALLING && player != null ? player.getEntityPos().subtract(getEntityPos()) : step;
        if (directedLook.active()) {
            applyDirectedLook(directedLook.target());
            UUID looked=directedLook.applied(serverTick());
            if(looked!=null)control.completeAction(looked,Outcome.SUCCEEDED,"facing_target");
        } else {
            Vec3d gaze=control.enabled() && activity!=Activity.CALLING && activity!=Activity.DOWNED
                && activity!=Activity.RESTING?speechGaze.target(serverTick()):null;
            if(gaze==null && safeFollow && controller.lookingAtOwner(serverTick()))gaze=player.getEyePos();
            updateOrientation(gaze,facing);
        }
        snapshotRevision++;
    }

    /** Minecraft内の指定方向を同じtickで適用する。位置・速度・移動計画は変更しない。 */
    void applyDirectedLook(Vec3d target) {
        var angles=dogido.lab.api.perception.BodyVisionGeometry.facing(target.subtract(getEyePos()),getYaw(),getPitch());
        setYaw((float)angles.yaw());setPitch((float)angles.pitch());setBodyYaw(getYaw());setHeadYaw(getYaw());
    }

    /**
     * 目標は世界座標、移動量はblock/tick。注視はyaw最大6度・pitch最大4度ずつ近づける。
     * 目標なしで実際に水平移動する時だけ進行方向へ向く。待機と上下の浮遊は直前の向きを保つ。
     */
    void updateOrientation(Vec3d gaze, Vec3d facing) {
        if (gaze != null) {
            var angles = dogido.lab.api.perception.BodyVisionGeometry.facing(gaze.subtract(getEyePos()),getYaw(),getPitch());
            setYaw(MathHelper.stepUnwrappedAngleTowards(getYaw(),(float)angles.yaw(),6));
            setPitch(MathHelper.stepTowards(getPitch(),(float)angles.pitch(),4));
        } else if (facing.horizontalLengthSquared() > 1e-6) {
            float yaw = (float)Math.toDegrees(Math.atan2(-facing.x,facing.z));
            setYaw(MathHelper.stepUnwrappedAngleTowards(getYaw(),yaw,6));
            setPitch(MathHelper.stepTowards(getPitch(),0,4));
        }
        setBodyYaw(getYaw()); setHeadYaw(getYaw());
    }

    @Override protected void writeCustomData(WriteView view) {
        super.writeCustomData(view);
        if (ownerId != null) view.putString("DogidoOwner",ownerId.toString());
        if (ownerId != null && localControl) view.putString("DogidoLocalMode",control.mode().name());
        view.putBoolean("DogidoDowned",vitals.downed()); view.putInt("DogidoWallTicks",vitals.wallTicks());
        view.putInt("DogidoRespawnRemaining",vitals.remaining()); view.putLong("DogidoRespawnCount",vitals.respawnCount());
        view.putLong("DogidoDamageSequence",vitals.damageSequence());
    }
    @Override protected void readCustomData(ReadView view) {
        super.readCustomData(view);
        // 召喚時のNBTにはPersistenceRequiredがなく、親の読込みが初期値をfalseへ戻す。
        // 身体は所有登録の前後を問わず維持し、距離による自然消滅で登録台帳を失わないようにする。
        setPersistent();
        try { ownerId = UUID.fromString(view.getString("DogidoOwner","")); }
        catch (IllegalArgumentException e) { ownerId = null; }
        vitals.restore(view.getBoolean("DogidoDowned",false),view.getInt("DogidoWallTicks",0),
            view.getInt("DogidoRespawnRemaining",0),view.getLong("DogidoRespawnCount",0),view.getLong("DogidoDamageSequence",0));
        if (ownerId != null && getHealth() <= 0 && !vitals.downed()) vitals.collapse(tuning);
        // 旧保存にもFOLLOWを既定適用。明示したローカルHOLD/DIRECT等は保存値で維持し、古い外部leaseは復元しない。
        localControl = ownerId != null;
        if (localControl) control.local(BodyRecallPolicy.savedLocalMode(view.getString("DogidoLocalMode","FOLLOW")),false);
        lastOwner = null; lifecycleTick = -1; newEpoch();
        noClip = true; setNoGravity(true);
    }
}

package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.api.BodyProtocol;
import dogido.lab.control.BodyController;
import dogido.lab.control.BodyMotion;
import dogido.lab.control.BodyTuning;
import dogido.lab.control.ControlLease;
import dogido.lab.control.BodyVitals;
import dogido.lab.control.DirectedMotion;
import dogido.lab.control.BodyRequests;
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
    private final DirectedMotion directed = new DirectedMotion();
    private final BodyFood food = new BodyFood(this);
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
        vitals.collapse(tuning); directed.clear(); controller.reset();
        control.completeAction(Outcome.INTERRUPTED,"body_downed"); control.pause();
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
            Math.max(.1,player.getEyeY()-player.getY()-.2-BodyTuning.BODY_SIZE/2));
    }
    private void newEpoch() {
        Mode mode = control.mode(); boolean retreat = control.retreat();
        control.reset(ownerId == null ? null : new Context(ownerId,getUuid(),UUID.randomUUID(),
            getEntityWorld().getRegistryKey().getValue().toString()));
        // Only explicit local test controls survive. Remote commands need a fresh context.
        if (localControl) control.local(mode,retreat);
        controller.reset(); directed.clear(); food.clear(); motion = Vec3d.ZERO;
        snapshotRevision++;
    }
    public String bind(ServerPlayerEntity player) {
        requireServerThread();
        if (!player.isAlive() || player.isSpectator()) return "owner_unavailable";
        if (player.getEntityWorld() != getEntityWorld() || squaredDistanceTo(player) > 64) return "body_must_be_within_8_blocks";
        if (ownerId != null && !ownerId.equals(player.getUuid())) return "owned_by_another_player";
        if (!BodyOwners.get(player.getEntityWorld().getServer()).claim(player.getUuid(),getUuid())) return "owner_already_has_body";
        ownerId = player.getUuid(); lastOwner = player; localControl = true; newEpoch(); localMode(Mode.DIRECT,false);
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
    public StateResult applyState(ServerPlayerEntity player, StateRequest request) {
        requireOwner(player); prepareLifecycle();
        Mode before = control.mode();
        String problem = BodyRequests.state(request,vitals.downed());
        StateResult result = control.apply(request,serverTick(),problem);
        if (result.outcome() == Outcome.ACCEPTED) {
            localControl = false;
            if (before != control.mode()) {
                directed.clear(); controller.reset(); control.completeAction(Outcome.INTERRUPTED,"control_mode_changed");
            }
            if (!control.enabled() || control.retreat()) {
                directed.clear();
                controller.cancelCall(control.retreat() ? "retreat_has_priority" : "control_inactive");
                control.completeAction(Outcome.INTERRUPTED,control.retreat() ? "retreat_has_priority" : "control_inactive");
            }
        }
        snapshotRevision++; return result;
    }
    public ActionResult requestAction(ServerPlayerEntity player, ActionRequest request) {
        requireOwner(player); prepareLifecycle();
        var admission = control.submit(request,serverTick(),actionAdmission(request));
        ActionResult result = admission.result();
        if (admission.execute()) {
            if (request.action() == Action.CALL) {
                directed.clear(); controller.call(result.actionId(),pose(player),serverTick());
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
        snapshotRevision++; return result;
    }
    private String actionAdmission(ActionRequest request) {
        String problem = BodyRequests.action(request,control.mode(),vitals.downed(),new Position(getX(),getY(),getZ()));
        if (problem != null) return problem;
        if (request == null || request.action() == null) return null; // 外形はControlLeaseで検査する。
        if (request.action() == Action.MOVE_TO) {
            Position p = request.destination();
            Vec3d target = new Vec3d(p.x(),p.y(),p.z());
            BodySpace space = new BodySpace((ServerWorld)getEntityWorld(),this);
            if (!space.loaded(BodyMotion.box(target))) return "destination_not_loaded";
            // 火/壁/敵との距離はここでは拒まない。選んだ行動の結果として身体に被害が起こり得る。
        }
        return request.action() == Action.EAT ? food.admission(request.foodId()) : null;
    }
    void localMode(Mode mode, boolean retreat) {
        requireServerThread(); localControl = true; control.local(mode,retreat);
        directed.clear(); controller.reset(); control.completeAction(Outcome.INTERRUPTED,"local_control_changed");
        if (retreat || mode == Mode.HOLD) {
            controller.cancelCall(retreat ? "retreat_has_priority" : "local_hold");
            control.completeAction(Outcome.INTERRUPTED,retreat ? "retreat_has_priority" : "local_hold");
        }
        snapshotRevision++;
    }
    void localSafe() { localMode(control.mode(),false); }
    void localRetreat() { localMode(control.mode(),true); }
    ActionResult localCall(ServerPlayerEntity player) {
        requireOwner(player); prepareLifecycle();
        localMode(control.enabled() ? control.mode() : Mode.FOLLOW,control.retreat());
        long sequence = Math.max(serverTick(),control.actionResult() == null ? 0 : control.actionResult().sequence()+1);
        return requestAction(player,new ActionRequest(control.context(),sequence,UUID.randomUUID(),serverTick(),100,Action.CALL));
    }
    ActionResult localAction(ServerPlayerEntity player, Action action, Position destination, String foodId) {
        requireOwner(player); prepareLifecycle();
        if (vitals.downed()) return new ActionResult(null,-1,Outcome.REJECTED,"body_downed");
        localMode(Mode.DIRECT,false);
        long sequence = Math.max(serverTick(),control.actionResult() == null ? 0 : control.actionResult().sequence()+1);
        return requestAction(player,new ActionRequest(control.context(),sequence,UUID.randomUUID(),serverTick(),100,action,destination,foodId));
    }
    FoodObservation foodObservation() { requireServerThread(); return food.observe(); }
    public Snapshot snapshot() {
        requireServerThread(); prepareLifecycle();
        Vec3d p = getEntityPos();
        ServerPlayerEntity player = registeredOwner();
        return new Snapshot(BodyProtocol.VERSION,owner() == null ? Presence.OWNER_UNAVAILABLE : Presence.PRESENT,control.context(),player == null ? null : player.getEntityWorld().getRegistryKey().getValue().toString(),serverTick(),snapshotRevision,
            new Position(p.x,p.y,p.z),getYaw(),activity,reason,control.revision(),control.stateResult(),control.actionResult(),Sensors.NOT_IMPLEMENTED,
            vitals.snapshot(getHealth(),getMaxHealth(),damageEnabled(),player == null ? null : player.interactionManager.getGameMode().getId()),
            owner() == null || vitals.downed() ? new FoodObservation(ObservationStatus.UNAVAILABLE,serverTick(),List.of(),false) : food.observe(), BodyPoseObservation.capture(this,owner()));
    }

    private void prepareLifecycle() {
        ServerPlayerEntity player = registeredOwner();
        String dimension = player == null ? null : player.getEntityWorld().getRegistryKey().getValue().toString();
        boolean available = player != null && player.isAlive() && !player.isSpectator();
        // Dimension transfer retains the player object. Check identity before the same-tick fast path.
        if (lastOwner != player || !java.util.Objects.equals(lastOwnerDimension,dimension) || lastOwnerAvailable != available
                || ownerId != null && control.context() == null) {
            newEpoch(); lastOwner = player; lastOwnerDimension = dimension; lastOwnerAvailable = available;
            if (!available) { activity = Activity.OWNER_ABSENT; reason = "owner_unavailable"; }
        }
        if (lifecycleTick == serverTick()) return;
        lifecycleTick = serverTick();
        control.expire(serverTick());
        if (!control.enabled()) directed.clear();
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
        var step = vitals.step(player != null,damageEnabled(),space.insideSolid(getBoundingBox()),
            control.enabled() && control.mode() == Mode.DIRECT && directed.resting() && motion.length() < .03,
            getHealth(),getMaxHealth(),tuning);
        if (step.respawnReady()) { respawnAtOwner(player); return; }
        if (vitals.downed()) { activity = Activity.DOWNED; reason = player == null ? "respawn_owner_unavailable" : "respawn_wait"; return; }
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
        if (!vitals.downed()) followOwnerWorld();
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
        next.setPose(EntityPose.STANDING); next.extinguish(); next.clearStatusEffects(); next.localControl = false;
        next.newEpoch(); next.activity = Activity.HOLDING; next.reason = "respawned_at_owner";
        next.lastBodyStep = serverTick(); next.lastWarpTick = serverTick();
    }

    /** Runs on server ticks as well as entity ticks, so a loaded non-ticking old chunk can transfer. */
    void followOwnerWorld() {
        requireServerThread(); prepareLifecycle();
        ServerPlayerEntity player = owner();
        if (player == null || !control.enabled() || control.mode() == Mode.HOLD && !control.retreat() && !controller.calling()) return;
        boolean otherWorld = player.getEntityWorld() != getEntityWorld();
        if (!otherWorld && control.mode() == Mode.DIRECT) return;
        if (!otherWorld && squaredDistanceTo(player) <= tuning.warpDistance*tuning.warpDistance) return;
        if (serverTick()-lastWarpTick < 40) return;
        BodySpace space = new BodySpace(player.getEntityWorld(),this);
        Vec3d destination = null;
        for (double radius : new double[]{tuning.followDistance,5,8,11}) {
            for (float turn : control.retreat() ? new float[]{180,135,-135} : new float[]{45,-45,90,-90,135,-135,180}) {
                Vec3d p = BodyMotion.offset(player.getEntityPos(),player.getYaw()+turn,radius,0,pose(player).bodyHeight());
                if (!BodyMotion.nearFront(p,player.getEntityPos(),player.getYaw()) && space.target(p,false,player,control.retreat())) {
                    destination = p; break;
                }
            }
            if (destination != null) break;
        }
        if (destination == null) { reason = "no_safe_warp_destination"; return; }
        boolean wasLocal = localControl; Mode mode = control.mode(); boolean retreat = control.retreat();
        long now = serverTick();
        Entity moved = teleportTo(new TeleportTarget(player.getEntityWorld(),destination,Vec3d.ZERO,getYaw(),0,TeleportTarget.NO_OP));
        if (moved instanceof DogidoEntity next) {
            next.localControl = wasLocal; next.lastOwner = player; next.newEpoch();
            if (wasLocal) next.localMode(mode,retreat);
            next.lastWarpTick = now; next.reason = otherWorld ? "followed_owner_dimension" : "caught_up";
        }
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
        BodyController.Plan plan = direct && player != null && control.enabled() && !controller.calling()
            ? directed.plan(position) : controller.plan(position,player == null ? null : pose(player),serverTick(),control.enabled(),
                control.mode(),control.retreat(),tuning,(p,walls) -> direct ? space.loaded(BodyMotion.box(p)) : space.target(p,walls,currentOwner,control.retreat()));
        activity = ownerId == null ? Activity.UNBOUND : plan.activity();
        reason = ownerId == null ? "bind_existing_body" : control.expired() ? "control_lease_expired" : plan.reason();
        if (controller.takeArrived()) control.completeAction(Outcome.SUCCEEDED,"arrived");
        if (directed.takeArrival()) control.completeAction(Outcome.SUCCEEDED,"arrived");
        String callEnd = controller.takeCallEnd();
        if (callEnd != null) control.completeAction(callEnd.equals("call_timeout") ? Outcome.EXPIRED : Outcome.INTERRUPTED,callEnd);
        double speed = tuning.speedFor(activity,position.distanceTo(plan.target()),player == null ? 0 : position.distanceTo(player.getEntityPos()));
        boolean active = activity == Activity.FOLLOWING || activity == Activity.EXPLORING || activity == Activity.CALLING || activity == Activity.RETREATING || activity == Activity.MOVING;
        Vec3d target = active && !plan.reason().equals("no_safe_target")
            ? plan.target().add(0,.06*Math.sin(age*Math.PI*2/80),0) : position.add(0,IdleMotion.hoverStep(age),0);
        Box path = getBoundingBox().expand(speed+.1);
        List<Box> obstacles = direct ? space.physicalObstacles(path) : space.motionObstacles(path,currentOwner,control.retreat());
        boolean allowFront = direct || !active || activity == Activity.CALLING || currentOwner == null;
        Vec3d motionTarget = allowFront ? target : BodyMotion.aroundFront(position,target,currentOwner.getEntityPos(),currentOwner.getYaw());
        Vec3d step = BodyMotion.steer(position,motionTarget,speed,motion,obstacles,delta ->
            space.loaded(getBoundingBox().union(getBoundingBox().offset(delta)))
                && (allowFront || BodyMotion.frontClear(position,delta,currentOwner.getEntityPos(),currentOwner.getYaw())));
        move(MovementType.SELF,step);
        motion = step; setVelocity(Vec3d.ZERO); fallDistance = 0;
        if (direct && step.length() < .03 && (activity == Activity.RESTING || activity == Activity.HOLDING))
            control.completeAction(Outcome.SUCCEEDED,activity == Activity.RESTING ? "resting" : "waiting");
        if (step.lengthSquared() < 1e-9 && active && position.distanceTo(target) > .4) reason = "path_blocked";
        Vec3d facing = activity == Activity.CALLING && player != null ? player.getEntityPos().subtract(getEntityPos()) : step;
        if (facing.horizontalLengthSquared() > 1e-6) {
            float yaw = (float)Math.toDegrees(Math.atan2(-facing.x,facing.z));
            setYaw(MathHelper.stepUnwrappedAngleTowards(getYaw(),yaw,6)); setBodyYaw(getYaw()); setHeadYaw(getYaw());
        }
        snapshotRevision++;
    }

    @Override protected void writeCustomData(WriteView view) {
        super.writeCustomData(view);
        if (ownerId != null) view.putString("DogidoOwner",ownerId.toString());
        view.putBoolean("DogidoDowned",vitals.downed()); view.putInt("DogidoWallTicks",vitals.wallTicks());
        view.putInt("DogidoRespawnRemaining",vitals.remaining()); view.putLong("DogidoRespawnCount",vitals.respawnCount());
        view.putLong("DogidoDamageSequence",vitals.damageSequence());
    }
    @Override protected void readCustomData(ReadView view) {
        super.readCustomData(view);
        try { ownerId = UUID.fromString(view.getString("DogidoOwner","")); }
        catch (IllegalArgumentException e) { ownerId = null; }
        vitals.restore(view.getBoolean("DogidoDowned",false),view.getInt("DogidoWallTicks",0),
            view.getInt("DogidoRespawnRemaining",0),view.getLong("DogidoRespawnCount",0),view.getLong("DogidoDamageSequence",0));
        if (ownerId != null && getHealth() <= 0 && !vitals.downed()) vitals.collapse(tuning);
        localControl = false; lastOwner = null; lifecycleTick = -1; newEpoch();
        noClip = true; setNoGravity(true);
    }
}

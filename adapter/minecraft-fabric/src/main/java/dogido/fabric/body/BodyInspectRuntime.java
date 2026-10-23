package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.fabric.observation.mobs.MobClassifier;
import dogido.fabric.protocol.DogidoEventClient.SessionRef;
import java.time.Instant;
import java.util.function.Consumer;
import java.util.function.Predicate;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import static dogido.fabric.body.BodyInspectProtocol.*;

/** HTTP callbackをclient threadへ渡し、候補の現物を検査して既存BodyBridgeのMOVE_TOへ接続する。 */
public final class BodyInspectRuntime {
    private final BodyInspectController controller;
    private final BodySensesCollector senses;
    private final MobClassifier classifier=new MobClassifier();
    private BodyConnection.Frame currentFrame;
    private Entity currentBody;
    /** Body MOVE_TOだけの配送境界。実装はEventClientの既存session monitorを使って最終照合する。 */
    @FunctionalInterface public interface Submission {
        String moveTo(SessionRef session,Command command,BodyConnection.MoveRequest request);
    }
    public BodyInspectRuntime(BodyConnection connection,BodySensesCollector senses,
            Consumer<JsonObject> resultSink,Predicate<SessionRef> current,Submission submission) {
        this.senses=senses;
        this.controller=new BodyInspectController(new BodyInspectController.Access() {
            public long nextSequence(Context context,long prior,long tick,long last) { return connection.nextActionSequence(context,prior,tick); }
            public BodyInspectController.Live capture() {
                MinecraftClient client=MinecraftClient.getInstance();
                if (client.player == null || client.world == null) return null;
                currentFrame=connection.captureFrame(client.player.getUuid(),client.world.getRegistryKey().getValue().toString());
                JsonObject observation=senses.capture(currentFrame,client);
                if (!"observed".equals(string(observation,"status"))) return null;
                JsonObject sample=observation.getAsJsonObject("sample");
                return new BodyInspectController.Live(senses.autonomy(currentFrame,observation),
                    point(sample.getAsJsonObject("self_pose").getAsJsonObject("position")),
                    new Point(client.player.getX(),client.player.getY(),client.player.getZ()),client.player.getEyeY()-client.player.getY());
            }
            public BodyInspectController.ResolvedTarget resolve(Target target,BodyInspectController.Live live) { return resolveCurrent(target,live,false); }
            public Target imageTarget(Command command,SessionRef session,BodyInspectController.Live live) {
                return dogido.fabric.screenshot.BodyViewTargets.resolve(command.imageTarget(),command.context(),session.sessionId());
            }
            public BodyInspectController.ResolvedTarget resolveImage(Target target,BodyInspectController.Live live) { return resolveCurrent(target,live,true); }
            public boolean collisionFree(BodyApproachGeometry.Bounds destination) { return clearSpace(destination); }
            public String moveTo(SessionRef session,Command command,BodyConnection.MoveRequest request) {
                String result=submission.moveTo(session,command,request);
                if ("queued".equals(result)) senses.focus(command,request.observedServerTick(),request.sequence(),() -> current.test(session));
                return result;
            }
        },current,result -> { senses.inspectionResult(result); resultSink.accept(result); });
    }
    public void handle(Command command,SessionRef session) {
        MinecraftClient.getInstance().execute(() -> controller.handle(command,session,Instant.now()));
    }
    public void tick() { controller.tick(Instant.now()); }
    public void clear() { controller.clear(); senses.clearFocus(); classifier.reset(); currentFrame=null; currentBody=null; }
    private BodyInspectController.ResolvedTarget resolveCurrent(Target target,BodyInspectController.Live live,boolean fromImage) {
        MinecraftClient client=MinecraftClient.getInstance();
        if (client.player == null || client.world == null || currentFrame == null) return null;
        JsonObject sample=currentFrame.observation().getAsJsonObject("sample");
        var vision=senses.inspectionVision(sample,currentFrame.autonomy());
        if(fromImage) {
            // 画像で指した対象は通常の注意箱・候補上限に縛らない。現在の身体視野・遮蔽・距離は同じ実測で再照合する。
            var pose=sample.getAsJsonObject("self_pose");var eye=point(pose.getAsJsonObject("eye_position"));
            vision=new BodyOriginVision(sample.get("server_tick").getAsLong(),new Vec3d(eye.x(),eye.y(),eye.z()),
                pose.get("view_yaw").getAsDouble(),pose.get("view_pitch").getAsDouble());
        }
        currentBody=null;
        var bodyId=context(live.autonomy().getAsJsonObject("context")).bodyId();
        for (Entity entity : client.world.getEntities()) if (entity.getUuid().equals(bodyId) && entity.isAlive()) { currentBody=entity; break; }
        if (currentBody == null) return null;
        // 補間中の身体座標は使わず、現物の寸法だけをserver姿勢に合わせる。
        Box bodyBox=currentBody.getBoundingBox().offset(currentBody.getEntityPos().negate());
        var shape=bounds(bodyBox);
        if ("entity".equals(target.kind())) {
            // 新しい候補を探さず、選択済みUUIDの現物だけを照合する。
            for (Entity entity : client.world.getEntities()) {
                if (!entity.getUuid().equals(target.entityId())) continue;
                if (!(entity instanceof LivingEntity living) || !living.isAlive()
                        || !target.typeId().equals(Registries.ENTITY_TYPE.getId(entity.getType()).toString())
                        || !classifier.classifyMobDisposition(client.player.getUuid(),living).ambientEligible()
                        || !vision.entity(client.world,entity,16)) return null;
                return resolved(new Point(entity.getX(),entity.getY(),entity.getZ()),bounds(entity.getBoundingBox()),shape);
            }
            return null;
        }
        Point p=target.position(); BlockPos pos=BlockPos.ofFloored(p.x(),p.y(),p.z());
        if (!client.world.getChunkManager().isChunkLoaded(pos.getX()>>4,pos.getZ()>>4)) return null;
        var state=client.world.getBlockState(pos);
        if (state.isAir() || !target.typeId().equals(Registries.BLOCK.getId(state.getBlock()).toString())
                || !vision.point(client.world,Vec3d.ofCenter(pos),pos,21)) return null;
        return resolved(new Point(pos.getX()+.5,pos.getY()+.5,pos.getZ()+.5),
            new BodyApproachGeometry.Bounds(pos.getX(),pos.getY(),pos.getZ(),pos.getX()+1,pos.getY()+1,pos.getZ()+1),shape);
    }
    private BodyInspectController.ResolvedTarget resolved(Point position,BodyApproachGeometry.Bounds target,
            BodyApproachGeometry.Bounds shape) {
        boolean ceiling=!clearSpace(shape.at(BodyApproachGeometry.above(target,shape)));
        return new BodyInspectController.ResolvedTarget(position,target,shape,ceiling);
    }
    private boolean clearSpace(BodyApproachGeometry.Bounds bounds) {
        MinecraftClient client=MinecraftClient.getInstance();
        if (client.world == null || currentBody == null) return false;
        Box box=new Box(bounds.minX(),bounds.minY(),bounds.minZ(),bounds.maxX(),bounds.maxY(),bounds.maxZ());
        return BodyOriginVision.loaded(client.world,box) && client.world.isSpaceEmpty(currentBody,box);
    }
    private static BodyApproachGeometry.Bounds bounds(Box box) {
        return new BodyApproachGeometry.Bounds(box.minX,box.minY,box.minZ,box.maxX,box.maxY,box.maxZ);
    }
}

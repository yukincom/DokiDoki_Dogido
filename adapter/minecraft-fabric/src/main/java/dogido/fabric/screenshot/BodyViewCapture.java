package dogido.fabric.screenshot;

import com.google.gson.*;
import dogido.fabric.DogidoBuildInfo;
import dogido.fabric.body.BodyConnection;
import dogido.lab.api.perception.BodyVisibility;
import java.nio.file.*;
import java.time.Instant;
import java.time.Duration;
import java.util.UUID;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import javax.imageio.ImageIO;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.block.ShapeContext;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.render.Camera;
import net.minecraft.client.render.RenderTickCounter;
import net.minecraft.client.render.entity.state.EntityRenderState;
import net.minecraft.client.texture.NativeImage;
import net.minecraft.client.util.ScreenshotRecorder;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.text.Text;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;
import org.joml.Matrix4f;
import org.slf4j.LoggerFactory;
import static net.fabricmc.fabric.api.client.command.v2.ClientCommandManager.literal;

/**
 * 手動診断・通常会話・句集用の単発身体視点撮影を共有する。身体・playerを変更せず、画像の意味判定も行わない。
 * 予約→描画frameのcamera/owner/行列を固定→GPU読取→IO保存→既存結果queueの順。CameraはMixinのfinallyが復帰する。
 */
public final class BodyViewCapture {
    private static final org.slf4j.Logger LOG=LoggerFactory.getLogger("dogido-body-view");
    private static final String BOOT_ID=UUID.randomUUID().toString();
    private static final ExecutorService IO=Executors.newSingleThreadExecutor(r->{ Thread t=new Thread(r,"dogido-body-view-io");t.setDaemon(true);return t; });
    private static final AtomicBoolean STATUS_PENDING=new AtomicBoolean();
    private static BodyConnection connection;
    private static Path root;
    private static long armedUntil,nextStatus,nextCleanup;
    private static Request pending,stopRequest,poemRequest;
    private static final BodyViewMotion MOTION=new BodyViewMotion();
    private static final BodyViewChange CHANGES=new BodyViewChange();
    private static Frame active;
    private static Request currentRequest;
    private static long nextUseUpdate;
    private static final ConcurrentHashMap<String,Instant> ACK_USES=new ConcurrentHashMap<>();
    private static final CaptureDeadline DEADLINE=new CaptureDeadline();
    private static volatile String lastResult="none";
    private record Live(ClientWorld world,Object network,Entity owner,LivingEntity body,JsonObject sample,long age) {}
    private record Request(String id,Live live,long deadline,BodyViewProtocol.Command command,ScreenshotProtocol.Command screenshot,
                           BooleanSupplier sessionCurrent,Consumer<JsonObject> result,AtomicBoolean completed,long motionSequence,long translation) {}
    private record Owner(Vec3d position,Box box,float delta,boolean invisible,int clearRays,String pose) {}

    /** 一frame内の可変組立だけを保持し、GPU callbackへ渡す時点でJSONへ固定する。 */
    public static final class Frame {
        final Request request;
        final BodyViewCamera camera;
        final CompletableFuture<Boolean> restored=new CompletableFuture<>();
        Owner owner;
        BodyViewTargets.Sample targets;
        Matrix4f projection,view;
        int width,height;
        boolean scheduled;
        final Instant capturedAt=Instant.now();
        Frame(Request request,BodyViewCamera camera) { this.request=request;this.camera=camera; }
        public Camera camera() { return camera; }
    }
    private BodyViewCapture() {}

    /** 同じBodyConnectionを再利用し、独立HTTPやbody制御経路を作らずローカルcommandだけを追加する。 */
    public static void register(BodyConnection bodyConnection) {
        connection=bodyConnection;
        root=MinecraftClient.getInstance().runDirectory.toPath().resolve("dogido-vision");
        ClientCommandRegistrationCallback.EVENT.register((dispatcher,registry)->dispatcher.register(literal("dogido-view")
            .then(literal("prepare").executes(ctx->prepare()))
            .then(literal("capture").executes(ctx->request()))
            .then(literal("status").executes(ctx->{message("状態: "+(DEADLINE.busy()?"撮影処理中":live()!=null?"身体を取得済み":"身体待ち")+" / 前回: "+lastResult);return 1;}))));
        ClientTickEvents.END_CLIENT_TICK.register(client->tick());
        publishStatus("installed",null);
    }
    private static int prepare() {
        armedUntil=System.nanoTime()+Duration.ofMinutes(10).toNanos(); nextStatus=0;
        message("撮影待機を有効にしました。記録側の準備確認後に /dogido-view capture で1枚撮影します。");
        tick(); return 1;
    }
    private static int request() {
        if (System.nanoTime()>=armedUntil) { message("先に /dogido-view prepare で撮影待機を有効にしてください。");return 0; }
        if (DEADLINE.busy() || pending!=null) { message("前の撮影が完了するまで待ってください。");return 0; }
        Live live=live();
        if (live==null) { message("身体の取得待ちです。ゲーム画面に戻り少し待ってから実行してください。");return 0; }
        pending=new Request("view_"+UUID.randomUUID(),live,System.nanoTime()+Duration.ofSeconds(10).toNanos(),null,null,()->true,null,new AtomicBoolean(),0,0);
        DEADLINE.start(pending.id,pending.deadline); currentRequest=pending;lastResult="requested";
        message("撮影を予約しました。次の描画frameで1枚保存します。"); return 1;
    }
    /** 既存event応答の型付き命令をclient threadへ渡す。手動prepareを要求せず、同じ一枚撮影枠で予約する。 */
    public static void request(BodyViewProtocol.Command command,BooleanSupplier sessionCurrent,Consumer<JsonObject> result) {
        MinecraftClient.getInstance().execute(()->{
            String error=command.timeError(Instant.now());Live current=live();
            if (!sessionCurrent.getAsBoolean()) return;
            if(error==null && (current==null || !command.matches(current.sample.getAsJsonObject("context"))))error="body_unavailable";
            if(error!=null) {result.accept(BodyViewProtocol.result(command,"failed",error,Instant.now()));return;}
            if(stopRequest!=null)complete(stopRequest,null,"superseded");
            long remaining=Duration.between(Instant.now(),command.expiresAt()).toNanos();
            stopRequest=new Request("view_"+UUID.randomUUID(),current,System.nanoTime()+Math.max(0,remaining),command,null,
                sessionCurrent,result,new AtomicBoolean(),0,0);
        });
    }
    /** 句集用も同じframe予約に合流する。撮影前はworld世代まで照合し、保存後の結果は元sessionの台帳へ返す。 */
    public static void request(ScreenshotProtocol.Command command,BooleanSupplier sessionCurrent,Consumer<JsonObject> result) {
        MinecraftClient.getInstance().execute(()->{
            if(!sessionCurrent.getAsBoolean())return;
            String error=command.timeError(Instant.now());Live current=live();
            if(error==null && (current==null || !command.matches(current.sample.getAsJsonObject("context"))))error="body_unavailable";
            if(error==null && poemRequest!=null)error="capture_busy";
            if(error!=null) {
                result.accept(ScreenshotProtocol.result(command,"expired".equals(error)?"expired":"rejected",error,Instant.now()));return;
            }
            long remaining=Duration.between(Instant.now(),command.expiresAt()).toNanos();
            poemRequest=new Request("view_"+UUID.randomUUID(),current,System.nanoTime()+Math.max(0,remaining),null,command,
                sessionCurrent,result,new AtomicBoolean(),0,0);
        });
    }
    /** ACKが画像利用を確定した時刻を共有retentionへ反映する。別readerの排他中なら次tick以降に再試行する。 */
    public static void acknowledged(String captureId) {
        if(BodyViewProtocol.captureId(captureId))ACK_USES.merge(captureId,Instant.now(),(a,b)->a.isAfter(b)?a:b);
    }
    private static boolean valid(Request request,Live current) {
        return request.sessionCurrent.getAsBoolean() && current!=null && current.world==request.live.world
            && current.network==request.live.network && current.owner==request.live.owner && current.body==request.live.body
            && current.sample.get("context").equals(request.live.sample.get("context"))
            && (request.command==null || request.command.matches(current.sample.getAsJsonObject("context")))
            && (request.screenshot==null || (request.screenshot.timeError(Instant.now())==null
                && request.screenshot.matches(current.sample.getAsJsonObject("context"))));
    }
    /**
     * 会話画像は配送直前も現在観測へ再照合する。句集画像は撮影済みframeを正とし、保存中のportal移動で帰属を変えない。
     * 句集結果のsession一致確認は既存Screenshot結果台帳が所有し、退出後の別sessionへは渡さない。
     */
    private static void complete(Request request,JsonObject image,String failure) {
        MinecraftClient.getInstance().execute(()->{
            if(currentRequest!=null && currentRequest.id.equals(request.id))currentRequest=null;
            if((request.command==null && request.screenshot==null) || !request.completed.compareAndSet(false,true))return;
            if(request.screenshot!=null) {
                request.result.accept(failure==null && image!=null?image:ScreenshotProtocol.result(request.screenshot,
                    "expired".equals(failure)?"expired":"failed",failure==null?"capture_failed":failure,Instant.now()));return;
            }
            if(!request.sessionCurrent.getAsBoolean())return;
            String error=failure;
            if(error==null && (System.nanoTime()>=request.deadline || request.command.timeError(Instant.now())!=null))error="expired";
            if(error==null && !valid(request,live()))error="stale_body";
            JsonObject result=BodyViewProtocol.result(request.command,error==null?"succeeded":"failed",error==null?"captured":error,Instant.now());
            if(error==null)result.add("body_view",image);request.result.accept(result);
        });
    }
    private static Live live() {
        var client=MinecraftClient.getInstance();
        if (connection==null || client.world==null || client.player==null || client.getNetworkHandler()==null) return null;
        JsonObject observation=connection.capture(client.player.getUuid(),client.world.getRegistryKey().getValue().toString());
        if (!observation.has("status") || !"observed".equals(observation.get("status").getAsString())
                || observation.get("age_ms").getAsLong()>=1000) return null;
        JsonObject sample=observation.getAsJsonObject("sample");
        UUID bodyId=UUID.fromString(sample.getAsJsonObject("context").get("body_id").getAsString());
        for (Entity entity:client.world.getEntities()) {
            if (entity.getUuid().equals(bodyId) && entity instanceof LivingEntity body && body.isAlive() && !body.hasVehicle() && !body.isSleeping())
                return new Live(client.world,client.getNetworkHandler(),client.player,body,sample.deepCopy(),observation.get("age_ms").getAsLong());
        }
        return null;
    }
    private static BodyViewMotion.Pose pose(Live live) {
        return new BodyViewMotion.Pose(live.body.getX(),live.body.getY(),live.body.getZ(),live.body.bodyYaw,live.body.getPitch());
    }
    private static void tick() {
        long now=System.nanoTime();
        Live observed=live();
        if(observed==null) {MOTION.clear();BodyViewTargets.clear();}
        else MOTION.observe(java.util.List.of(observed.world,observed.network,observed.sample.get("context")),pose(observed));
        if(poemRequest!=null) {
            Request request=poemRequest;
            if(now>=request.deadline || !valid(request,observed)) {
                poemRequest=null;complete(request,null,now>=request.deadline?"expired":"stale_body");
            } else if(!DEADLINE.busy() && pending==null) {
                pending=poemRequest;poemRequest=null;
                DEADLINE.start(pending.id,pending.deadline);currentRequest=pending;lastResult="poem_requested";
            }
        }
        if(stopRequest!=null) {
            Request request=stopRequest;
            if(now>=request.deadline || !valid(request,observed)) {
                stopRequest=null;complete(request,null,now>=request.deadline?"expired":"stale_body");
            } else if(!DEADLINE.busy() && pending==null && MOTION.still(pose(observed))
                    && MOTION.sequence()>request.command.afterMotionSequence()) {
                stopRequest=null;
                pending=new Request(request.id,observed,request.deadline,request.command,null,request.sessionCurrent,
                    request.result,request.completed,MOTION.sequence(),MOTION.translation());
                DEADLINE.start(pending.id,pending.deadline);currentRequest=pending;lastResult="stopped";
            }
        }
        String expired=DEADLINE.expire(now);
        if (expired!=null) {
            Request request=currentRequest;
            if(pending!=null && pending.id.equals(expired))pending=null;
            lastResult="expired";
            if(request!=null && request.id.equals(expired)) {
                complete(request,null,"expired");
                if(request.command==null && request.screenshot==null)message("撮影待機が期限切れになりました。画像は保存していません。");
            }
        }
        if(now>=nextUseUpdate && !ACK_USES.isEmpty()) {
            nextUseUpdate=now+Duration.ofSeconds(1).toNanos();
            IO.execute(()->ACK_USES.forEach((id,at)->{
                try {if(BodyViewFiles.markUsed(root.resolve("captures").resolve(id),at))ACK_USES.remove(id,at);}
                catch(Exception error) {ACK_USES.remove(id,at);LOG.warn("Capture retention update failed: {}",id);}
            }));
        }
        if (now>=nextCleanup) {
            nextCleanup=now+Duration.ofMinutes(1).toNanos();
            IO.execute(()->{try { Files.createDirectories(root);BodyViewFiles.cleanup(root.resolve("captures"),Instant.now()); }
                catch (Exception e) { LOG.warn("Temporary image cleanup failed",e); }});
        }
        if (now<armedUntil && now>=nextStatus) {
            nextStatus=now+Duration.ofSeconds(1).toNanos();
            Live live=live();publishStatus(live==null?"waiting_for_body":"ready",live);
        }
    }
    private static void publishStatus(String status,Live live) {
        if (!STATUS_PENDING.compareAndSet(false,true)) return;
        JsonObject data=new JsonObject();data.addProperty("schema_version",1);data.addProperty("boot_id",BOOT_ID);
        data.addProperty("adapter_build",DogidoBuildInfo.ADAPTER_BUILD);data.addProperty("status",status);
        data.addProperty("updated_at",Instant.now().toString());data.addProperty("busy",DEADLINE.busy());data.addProperty("last_result",lastResult);
        if (live!=null) data.add("context",live.sample.get("context").deepCopy());
        IO.execute(()->{try { Files.createDirectories(root);BodyViewFiles.json(root.resolve("ready.json"),data); }
            catch (Exception e) {LOG.warn("Capture readiness write failed",e);} finally {STATUS_PENDING.set(false);} });
    }

    /** 描画の入口。予約した世界・接続・本人・身体世代を再照合し、古い予約を別世界で実行しない。 */
    public static Frame begin(RenderTickCounter counter,boolean renderingWorld) {
        var client=MinecraftClient.getInstance();
        if (pending==null || !renderingWorld || client.currentScreen!=null || client.skipGameRender) return null;
        Request request=pending;
        Live current=live();
        // tick後に再び動き始めていたら、停止を取り直すまで描画へ入らない。
        if(request.command!=null && current!=null && !MOTION.still(pose(current)))return null;
        pending=null;
        if (System.nanoTime()>=request.deadline || !valid(request,current)
                || client.player.nauseaIntensity>0 || client.player.lastNauseaIntensity>0) {
            DEADLINE.finish(request.id);lastResult="stale_or_unavailable";complete(request,null,"stale_or_unavailable");
            if(request.command==null && request.screenshot==null)message("撮影条件が変わったため中止しました。");return null;
        }
        Request fresh=new Request(request.id,current,request.deadline,request.command,request.screenshot,request.sessionCurrent,request.result,request.completed,
            request.command==null?request.motionSequence:MOTION.sequence(),request.command==null?request.translation:MOTION.translation());
        currentRequest=fresh;
        float delta=counter.getTickProgress(!current.world.getTickManager().shouldSkipTick(current.body));
        active=new Frame(fresh,new BodyViewCamera(current.body,delta));
        return active;
    }
    public static boolean active() { return active!=null; }
    public static boolean activeFor(Camera camera) { return active!=null && active.camera==camera; }
    /** 復帰の事実をIO側へ渡してからframeを閉じる。GPU callbackを待ってCameraを保持しない。 */
    public static void end(Frame frame,boolean cameraRestored) {
        if (frame==null) return;
        if(cameraRestored && frame.scheduled && frame.request.command!=null) {
            var live=frame.request.live;
            try {
                frame.targets=BodyViewTargets.sample(frame.request.command.sessionId(),live.sample.getAsJsonObject("context"),
                    live.world,live.network,live.body,frame.camera.getCameraPos(),frame.projection,frame.view,frame.capturedAt);
            } catch(RuntimeException error) { LOG.warn("Image target sampling failed",error); }
        }
        // 色集計のIOは、このfuture完了後にだけ撮影時の対応表を読む。
        frame.restored.complete(cameraRestored);
        if (!frame.scheduled && DEADLINE.finish(frame.request.id)) {
            lastResult="render_not_captured";complete(frame.request,null,"render_not_captured");
        }
        if (active==frame) active=null;
    }
    public static void matrices(Camera camera,Matrix4f view,Matrix4f projection) {
        if (!activeFor(camera)) return;
        var framebuffer=MinecraftClient.getInstance().getFramebuffer();
        active.width=framebuffer.textureWidth;active.height=framebuffer.textureHeight;
        active.view=new Matrix4f(view);active.projection=new Matrix4f(projection);
    }
    /** ownerの実描画stateをその場でコピー。render末尾でstateが消去されるため、保存時にworldを再読しない。 */
    public static void owner(Entity entity,EntityRenderState state,float delta) {
        Frame frame=active;
        if (frame==null || entity!=frame.request.live.owner) return;
        var client=MinecraftClient.getInstance();
        Vec3d offset=client.getEntityRenderDispatcher().getRenderer(state).getPositionOffset(state);
        Vec3d position=new Vec3d(state.x,state.y,state.z).add(offset);
        double half=state.width/2.0;
        Box box=new Box(position.x-half,position.y,position.z-half,position.x+half,position.y+state.height,position.z+half);
        Vec3d eye=frame.camera.getCameraPos();
        int clear=0;
        for (double fraction:new double[]{.25,.55,.85}) {
            Vec3d target=new Vec3d(position.x,position.y+state.height*fraction,position.z);
            var trace=BodyVisibility.trace(frame.request.live.world,area->loaded(frame.request.live.world,area),eye,target,
                ShapeContext.of(frame.request.live.body),BodyVisibility.Purpose.VISION,null);
            if (trace==BodyVisibility.Trace.CLEAR) clear++;
        }
        frame.owner=new Owner(position,box,delta,state.invisible,clear,entity.getPose().name());
    }
    private static boolean loaded(ClientWorld world,Box area) {
        if (!world.getWorldBorder().contains(area)) return false;
        for(int x=MathHelper.floor(area.minX)>>4;x<=MathHelper.floor(area.maxX)>>4;x++)
            for(int z=MathHelper.floor(area.minZ)>>4;z<=MathHelper.floor(area.maxZ)>>4;z++)
                if(!world.getChunkManager().isChunkLoaded(x,z))return false;
        return true;
    }
    /** 世界の描画完了直後、手・HUD・depth消去より前。ここで記録を固定し、一度だけGPU readbackを予約する。 */
    public static void capture(Camera camera) {
        Frame frame=active;
        if (!activeFor(camera) || frame.scheduled || frame.projection==null) return;
        if ((long)frame.width*frame.height>16_777_216L) {lastResult="frame_too_large";return;}
        JsonObject data=metadata(frame);
        frame.scheduled=true;
        try {
            ScreenshotRecorder.takeScreenshot(MinecraftClient.getInstance().getFramebuffer(),image->{
                // GPUの遅延完了は世代・期限で照合する。旧結果で次の撮影待機を解除しない。
                if (!DEADLINE.claim(frame.request.id,System.nanoTime())) { image.close();return; }
                IO.execute(()->save(frame,data,image));
            });
        } catch (RuntimeException e) {frame.scheduled=false;LOG.warn("Body capture failed",e);}
    }
    private static JsonObject metadata(Frame frame) {
        JsonObject data=new JsonObject();data.addProperty("schema_version",1);data.addProperty("capture_id",frame.request.id);
        data.addProperty("boot_id",BOOT_ID);data.addProperty("adapter_build",DogidoBuildInfo.ADAPTER_BUILD);
        data.addProperty("captured_at",frame.capturedAt.toString());data.addProperty("viewpoint","dogido_client_render_eye");
        data.add("context",frame.request.live.sample.get("context").deepCopy());
        data.add("reference_server_tick",frame.request.live.sample.get("server_tick").deepCopy());
        data.addProperty("reference_snapshot_age_ms",frame.request.live.age);
        data.addProperty("client_world_time",frame.request.live.world.getTime());
        data.addProperty("width",frame.width);data.addProperty("height",frame.height);
        data.add("camera_eye",point(frame.camera.getCameraPos()));data.addProperty("camera_yaw",frame.camera.getYaw());
        data.addProperty("camera_pitch",frame.camera.getPitch());data.addProperty("body_tick_progress",frame.camera.bodyDelta());
        data.add("view_matrix",matrix(frame.view));data.add("projection_matrix",matrix(frame.projection));
        data.addProperty("coordinate_basis","client render state; same frame as image; server snapshot used only for identity");
        data.addProperty("visibility_basis","three block visibility rays, not an entity pixel mask");
        data.addProperty("owner_rendered",frame.owner!=null);
        String status="owner_not_in_render_list";
        if (frame.owner!=null) {
            Owner owner=frame.owner;data.add("owner_render_position",point(owner.position));
            data.addProperty("owner_tick_progress",owner.delta);data.addProperty("owner_pose",owner.pose);
            data.addProperty("owner_invisible",owner.invisible);data.addProperty("owner_clear_rays",owner.clearRays);
            data.add("owner_box_min",point(new Vec3d(owner.box.minX,owner.box.minY,owner.box.minZ)));
            data.add("owner_box_max",point(new Vec3d(owner.box.maxX,owner.box.maxY,owner.box.maxZ)));
            Vec3d eye=frame.camera.getCameraPos();
            var bounds=ScreenProjection.box(new Matrix4f(frame.projection).mul(frame.view),
                owner.box.minX-eye.x,owner.box.minY-eye.y,owner.box.minZ-eye.z,
                owner.box.maxX-eye.x,owner.box.maxY-eye.y,owner.box.maxZ-eye.z,frame.width,frame.height);
            boolean supportedPose=(owner.pose.equals("STANDING") || owner.pose.equals("CROUCHING")) && !frame.request.live.owner.hasVehicle();
            status=!supportedPose?"owner_pose_unsupported":owner.invisible?"owner_invisible":owner.clearRays==0?"owner_occluded_or_unavailable":bounds.isEmpty()?"owner_outside_or_near_plane":"available";
            if (status.equals("available")) {
                var b=bounds.get();JsonObject box=new JsonObject();box.addProperty("left",b.left());box.addProperty("top",b.top());
                box.addProperty("right",b.right());box.addProperty("bottom",b.bottom());box.addProperty("clipped",b.clipped());
                data.add("player_box",box);
            }
        }
        data.addProperty("annotation_status",status);return data;
    }
    private static void save(Frame frame,JsonObject data,NativeImage image) {
        Path ownedDirectory=null;
        boolean published=false;
        try(image) {
            if (!frame.restored.get(5,TimeUnit.SECONDS)) throw new IllegalStateException("camera_not_restored");
            if(frame.request.screenshot!=null) {
                var command=frame.request.screenshot;
                if(command.timeError(frame.capturedAt)!=null)throw new IllegalStateException("invalid_capture_time");
                JsonObject view=ScreenshotProtocol.captureView(command,data.getAsJsonObject("context"),true);
                var saved=ScreenshotRuntime.save(root.getParent(),frame.capturedAt,image.getWidth(),image.getHeight(),image.copyPixelsArgb());
                JsonObject result=ScreenshotProtocol.result(command,"succeeded","saved",Instant.now());
                result.addProperty("filename",saved.filename());result.addProperty("directory",saved.directory());result.addProperty("sha256",saved.sha256());
                result.addProperty("captured_at",frame.capturedAt.toString());result.add("capture_view",view);
                lastResult=saved.filename();LOG.info("Poem body view saved: {}",saved.filename());
                complete(frame.request,result,null);return;
            }
            Path captures=root.resolve("captures");Files.createDirectories(captures);
            Path directory=Files.createDirectory(captures.resolve(frame.request.id));ownedDirectory=directory;
            Path target=directory.resolve("frame.png");image.writeTo(target);
            String hash=BodyViewFiles.hash(target);data.addProperty("image_sha256",hash);data.addProperty("camera_restored",true);
            var original=ImageIO.read(target.toFile());
            BodyViewImage.Encoded resized=BodyViewImage.encode(original,data);
            if(frame.request.command!=null)BodyViewTargets.remember(frame.request.id,frame.targets,original);
            if(frame.request.command!=null) {
                var change=CHANGES.observe(data.get("context").toString(),frame.request.translation,original);
                data.addProperty("motion_sequence",frame.request.motionSequence);
                data.addProperty("visual_change",change.changed());data.addProperty("compared_to_four_before",change.compared());
                data.addProperty("pixel_mean_difference",change.meanDifference());data.addProperty("pixel_changed_fraction",change.changedFraction());
            }
            Files.write(directory.resolve("player-192.png"),resized.png(),StandardOpenOption.CREATE_NEW);
            data.addProperty("annotated_image","player-192.png");data.addProperty("annotated_sha256",resized.sha256());
            data.addProperty("annotated_width",resized.width());data.addProperty("annotated_height",resized.height());
            JsonObject retention=new JsonObject();retention.addProperty("schema_version",1);
            retention.addProperty("last_used_at",frame.capturedAt.toString());retention.addProperty("expires_at",frame.capturedAt.plus(Duration.ofHours(24)).toString());
            JsonArray images=new JsonArray();images.add(BodyViewFiles.image("frame.png",hash));images.add(BodyViewFiles.image("player-192.png",resized.sha256()));retention.add("images",images);
            BodyViewFiles.json(directory.resolve("retention.json"),retention);
            BodyViewFiles.json(directory.resolve("frame.json"),data);published=true;
            lastResult=frame.request.id;LOG.info("Body view capture saved: {} annotation={}",frame.request.id,data.get("annotation_status"));
            if(frame.request.command==null)MinecraftClient.getInstance().execute(()->message("撮影を保存しました: "+frame.request.id+" / 枠: "+data.get("annotation_status").getAsString()));
            else {
                var command=frame.request.command;JsonObject view=new JsonObject();
                view.addProperty("png_base64",resized.base64());view.addProperty("sha256",resized.sha256());
                view.addProperty("width",resized.width());view.addProperty("height",resized.height());
                view.addProperty("captured_at",frame.capturedAt.toString());view.addProperty("capture_id",frame.request.id);
                view.addProperty("body_entity_uuid",command.bodyId().toString());view.addProperty("body_epoch",command.epoch().toString());
                view.addProperty("owner_uuid",command.ownerId().toString());view.addProperty("dimension",command.dimension());
                view.addProperty("camera_restored",true);view.add("annotation_status",data.get("annotation_status").deepCopy());
                for(String key:new String[]{"motion_sequence","visual_change","compared_to_four_before","pixel_mean_difference","pixel_changed_fraction"})
                    view.add(key,data.get(key).deepCopy());
                complete(frame.request,view,null);
            }
        } catch(Exception e) {lastResult="save_failed";LOG.warn("Body view save failed",e);complete(frame.request,null,"save_failed");}
        finally {
            // この要求が新規作成した専用directoryだけを対象に、公開前の失敗で残った画像を回収する。
            if (!published && ownedDirectory!=null) {
                try { BodyViewFiles.discardUnpublished(ownedDirectory); }
                catch (Exception cleanup) { LOG.warn("Unpublished capture cleanup failed",cleanup); }
            }
            DEADLINE.finish(frame.request.id);
            MinecraftClient.getInstance().execute(()->{
                if(currentRequest!=null && currentRequest.id.equals(frame.request.id))currentRequest=null;
            });
        }
    }
    private static JsonObject point(Vec3d p) {JsonObject j=new JsonObject();j.addProperty("x",p.x);j.addProperty("y",p.y);j.addProperty("z",p.z);return j;}
    private static JsonArray matrix(Matrix4f matrix) {JsonArray a=new JsonArray();for(float f:matrix.get(new float[16]))a.add(f);return a;}
    private static void message(String text) {var client=MinecraftClient.getInstance();if(client.player!=null)client.player.sendMessage(Text.literal("[ドギド撮影] "+text),false);}
}

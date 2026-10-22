package dogido.fabric.screenshot;

import com.google.gson.JsonObject;
import dogido.fabric.body.BodyInspectProtocol;
import java.awt.image.BufferedImage;
import java.time.Instant;
import java.util.*;
import net.minecraft.block.PlantBlock;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.registry.Registries;
import net.minecraft.registry.tag.BlockTags;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.RaycastContext;
import org.joml.Matrix4f;

/** 撮影frameの画面方向と実物IDの対応を残し、見た目の属性から移動対象を一意に解決する。
 * 通常の観測表から独立した96×54の照射で、各画素が最初に当たった実物だけを採る。
 * world読取とframe台帳はclient thread、色集計は保存IOで行う。移動前の現在性は既存実行器が再照合する。
 */
public final class BodyViewTargets {
    private BodyViewTargets() {}
    private static final int WIDTH=96,HEIGHT=54;
    record Pixel(int x,int y,String id) {}
    record Candidate(BodyInspectProtocol.Target target,String kind) {}
    record Sample(String session,JsonObject context,ClientWorld world,Object network,LivingEntity body,
                  List<Pixel> pixels,Map<String,Candidate> candidates,Instant captured) {}
    private record Frame(Sample sample,List<BodyViewAppearance.Feature> features) {}
    private static final LinkedHashMap<String,Frame> FRAMES=new LinkedHashMap<>();

    /** camera復帰と同じclient frameで採取する。位置は撮影当時で固定し、後から別の物へすり替えない。 */
    static Sample sample(String session,JsonObject context,ClientWorld world,Object network,LivingEntity body,
                         Vec3d eye,Matrix4f projection,Matrix4f view,Instant at) {
        Matrix4f matrix=new Matrix4f(projection).mul(view);
        List<Pixel> pixels=new ArrayList<>();Map<String,Candidate> candidates=new LinkedHashMap<>();
        List<Entity> entities=new ArrayList<>();
        for(Entity entity:world.getEntities())if(entity!=body && entity instanceof LivingEntity && entity.isAlive()
                && entity.getBoundingBox().squaredMagnitude(eye)<=22*22)entities.add(entity);
        for(int y=0;y<HEIGHT;y++)for(int x=0;x<WIDTH;x++) {
            var ray=ScreenProjection.ray(matrix,(x+.5)/WIDTH,(y+.5)/HEIGHT);if(ray.isEmpty())continue;
            var direction=ray.get();Vec3d end=eye.add(direction.x()*21,direction.y()*21,direction.z()*21);
            Box area=new Box(eye,end).expand(.01);if(!loaded(world,area))continue;
            var block=world.raycast(new RaycastContext(eye,end,RaycastContext.ShapeType.OUTLINE,RaycastContext.FluidHandling.ANY,body));
            double closest=block.getType()==HitResult.Type.BLOCK?eye.distanceTo(block.getPos()):21;
            Entity selected=null;
            for(Entity entity:entities) {
                var hit=entity.getBoundingBox().raycast(eye,end);
                if(hit.isPresent() && eye.distanceTo(hit.get())<closest) {closest=eye.distanceTo(hit.get());selected=entity;}
            }
            // 本人の画素を背後の対象へ割り当てない。ownerへの接近は別の既存行動が所有する。
            if(selected instanceof PlayerEntity)continue;
            Candidate candidate;
            if(selected!=null)candidate=new Candidate(new BodyInspectProtocol.Target("e:"+selected.getUuid(),"entity",
                Registries.ENTITY_TYPE.getId(selected.getType()).toString(),selected.getName().getString(),
                new BodyInspectProtocol.Point(selected.getX(),selected.getY(),selected.getZ()),selected.getUuid()),"mob");
            else {
                if(block.getType()!=HitResult.Type.BLOCK)continue;
                var pos=block.getBlockPos();var state=world.getBlockState(pos);if(state.isAir())continue;
                String type=Registries.BLOCK.getId(state.getBlock()).toString();
                var point=new BodyInspectProtocol.Point(pos.getX()+.5,pos.getY()+.5,pos.getZ()+.5);
                String kind=state.isIn(BlockTags.FLOWERS)?"flower":state.getBlock() instanceof PlantBlock?"plant":
                    !state.getFluidState().isEmpty()&&state.getCollisionShape(world,pos).isEmpty()?"fluid":"block";
                candidate=new Candidate(new BodyInspectProtocol.Target(BodyInspectProtocol.blockId(point,type),"block",type,
                    state.getBlock().getName().getString(),point,null),kind);
            }
            candidates.putIfAbsent(candidate.target.id(),candidate);pixels.add(new Pixel(x,y,candidate.target.id()));
        }
        return new Sample(session,context.deepCopy(),world,network,body,List.copyOf(pixels),Map.copyOf(candidates),at);
    }
    private static boolean loaded(ClientWorld world,Box area) {
        if(!world.getWorldBorder().contains(area))return false;
        for(int x=MathHelper.floor(area.minX)>>4;x<=MathHelper.floor(area.maxX)>>4;x++)
            for(int z=MathHelper.floor(area.minZ)>>4;z<=MathHelper.floor(area.maxZ)>>4;z++)
                if(!world.getChunkManager().isChunkLoaded(x,z))return false;
        return true;
    }
    /** 原画像の色を対応画素だけで集計。2標本以上かつその対象の10%以上に見える色を特徴として残す。 */
    static void remember(String id,Sample sample,BufferedImage image) {
        if(sample==null)return;
        Map<String,List<Pixel>> groups=new HashMap<>();
        for(var pixel:sample.pixels)groups.computeIfAbsent(pixel.id,k->new ArrayList<>()).add(pixel);
        List<BodyViewAppearance.Feature> features=new ArrayList<>();
        for(var entry:groups.entrySet()) {
            var pixels=entry.getValue();if(pixels.size()<2)continue;
            Map<String,Integer> colors=new HashMap<>();double x=0,y=0;
            for(var pixel:pixels) {
                int ix=Math.min(image.getWidth()-1,(int)((pixel.x+.5)*image.getWidth()/WIDTH));
                int iy=Math.min(image.getHeight()-1,(int)((pixel.y+.5)*image.getHeight()/HEIGHT));
                colors.merge(BodyViewAppearance.color(image.getRGB(ix,iy)),1,Integer::sum);x+=pixel.x+.5;y+=pixel.y+.5;
            }
            Set<String> visible=new HashSet<>();
            for(var color:colors.entrySet())if(color.getValue()>=Math.max(2,Math.ceil(pixels.size()*.1)))visible.add(color.getKey());
            features.add(new BodyViewAppearance.Feature(entry.getKey(),sample.candidates.get(entry.getKey()).kind,
                Set.copyOf(visible),x/pixels.size()/WIDTH,y/pixels.size()/HEIGHT));
        }
        List<BodyViewAppearance.Feature> frozen=List.copyOf(features);
        MinecraftClient.getInstance().execute(()->{
            Instant now=Instant.now();FRAMES.entrySet().removeIf(e->!now.isBefore(e.getValue().sample.captured.plusSeconds(30)));
            if(!now.isBefore(sample.captured.plusSeconds(30)))return;
            FRAMES.put(id,new Frame(sample,frozen));while(FRAMES.size()>8)FRAMES.remove(FRAMES.keySet().iterator().next());
        });
    }
    static void clear() { FRAMES.clear(); }
    public static BodyInspectProtocol.Target resolve(BodyInspectProtocol.ImageTarget selection,BodyInspectProtocol.Context context,String session) {
        Frame frame=FRAMES.get(selection.captureId());if(frame==null)return null;
        var sample=frame.sample;var client=MinecraftClient.getInstance();
        if(!sample.session.equals(session) || !sample.context.equals(context.json())
            || !Instant.now().isBefore(sample.captured.plusSeconds(30)) || client.world!=sample.world
            || client.getNetworkHandler()!=sample.network || client.player==null || !client.player.getUuid().equals(context.ownerId())
            || !sample.body.isAlive() || !sample.body.getUuid().equals(context.bodyId()))return null;
        String id=BodyViewAppearance.select(frame.features,selection.kind(),selection.color(),selection.horizontal(),selection.vertical());
        return id==null?null:sample.candidates.get(id).target;
    }
}

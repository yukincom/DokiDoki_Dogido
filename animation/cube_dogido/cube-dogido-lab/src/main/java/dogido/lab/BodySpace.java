package dogido.lab;

import dogido.lab.control.BodyMotion;
import dogido.lab.control.BodySafety;
import dogido.lab.control.BodyTuning;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.block.CampfireBlock;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.entity.mob.Monster;
import net.minecraft.registry.tag.BlockTags;
import net.minecraft.registry.tag.FluidTags;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;

/** serverの読み込み済みワールドから身体の衝突・壁内・火炎接触を実測する。入力は身体の範囲、出力は障害物や接触理由で、ブロック変更・chunk生成・player観測は行わない。 */
final class BodySpace {
    final ServerWorld world;
    final DogidoEntity body;
    BodySpace(ServerWorld world, DogidoEntity body) { this.world = world; this.body = body; }

    boolean loaded(Box area) {
        if (area.minY < world.getBottomY() || area.maxY > world.getTopYInclusive()+1 || !world.getWorldBorder().contains(area)) return false;
        for (int x = MathHelper.floor(area.minX)>>4; x <= MathHelper.floor(area.maxX)>>4; x++)
            for (int z = MathHelper.floor(area.minZ)>>4; z <= MathHelper.floor(area.maxZ)>>4; z++)
                if (world.getChunkManager().getWorldChunk(x,z) == null) return false;
        return true;
    }
    List<LivingEntity> living(Box area) {
        return world.getEntitiesByClass(LivingEntity.class,area,e -> e != body && e.isAlive() && !e.isSpectator());
    }
    /** HostileEntityを継承しないPhantom/Ghastも含め、現在ownerか身体を攻撃する中立Mobを拾う。 */
    private boolean enemy(LivingEntity entity, ServerPlayerEntity owner) {
        return entity instanceof Monster || entity instanceof MobEntity mob && (mob.getTarget() == owner || mob.getTarget() == body);
    }
    /**
     * 読込み済み実体だけを有界な範囲から採る。小さな移動boxだけでは離れた敵からownerへの射線を見落とすため、
     * 身体とownerの周囲を両方含める。次元移動前の呼出しは移動先worldと移動先owner位置を渡す。
     */
    BodySafety safety(ServerPlayerEntity owner, Vec3d position) {
        if (owner == null) return BodySafety.EMPTY;
        Box nearBody = BodyMotion.box(position).expand(BodySafety.SCAN_RADIUS);
        Box nearOwner = owner.getBoundingBox().expand(BodySafety.SCAN_RADIUS);
        List<LivingEntity> seen = new ArrayList<>(living(nearBody));
        for (LivingEntity entity : living(nearOwner)) if (!seen.contains(entity)) seen.add(entity);
        return BodySafety.capture(owner.getBoundingBox().getCenter(),seen.stream().filter(e -> enemy(e,owner))
            .map(e -> new BodySafety.Enemy(e.getBoundingBox(),e.getVelocity())).toList());
    }
    /** DIRECTでは生物との物理衝突だけを処理し、火/敵/壁を避ける行動は選ばない。 */
    List<Box> physicalObstacles(Box path) {
        return living(path.expand(3)).stream().map(e -> e.getBoundingBox().expand(BodyTuning.LIVING_MARGIN)).toList();
    }
    boolean insideSolid(Box box) {
        if (!loaded(box)) return false;
        for (BlockPos p : BlockPos.iterate(MathHelper.floor(box.minX),MathHelper.floor(box.minY),MathHelper.floor(box.minZ),
                MathHelper.floor(box.maxX),MathHelper.floor(box.maxY),MathHelper.floor(box.maxZ))) {
            BlockState state = world.getBlockState(p);
            if (!state.isIn(BlockTags.LEAVES) && state.getCollisionShape(world,p).offset(p).getBoundingBoxes().stream().anyMatch(box::intersects)) return true;
        }
        return false;
    }
    /** noClip移動で省かれる接触を実測する。返値は接触源で、回避先を決めるものではない。 */
    String contactHazard(Box box) {
        if (!loaded(box)) return null;
        String found = null;
        for (BlockPos p : BlockPos.iterate(MathHelper.floor(box.minX),MathHelper.floor(box.minY),MathHelper.floor(box.minZ),
                MathHelper.floor(box.maxX),MathHelper.floor(box.maxY),MathHelper.floor(box.maxZ))) {
            BlockState state = world.getBlockState(p);
            if (!new Box(p).intersects(box)) continue;
            if (state.getFluidState().isIn(FluidTags.LAVA)
                    && BodyMotion.touchesFluid(box,p.getX(),p.getY(),p.getZ(),state.getFluidState().getHeight(world,p))) return "lava";
            if (state.isIn(BlockTags.FIRE)) found = "fire";
            if (state.isIn(BlockTags.CAMPFIRES) && state.contains(CampfireBlock.LIT) && state.get(CampfireBlock.LIT)) found = "campfire";
        }
        return found;
    }
    List<Box> obstacles(Box path) {
        List<Box> result = new ArrayList<>();
        // A short look-ahead lets the planner turn before reaching an entity's collision face.
        for (LivingEntity entity : living(path.expand(3))) result.add(entity.getBoundingBox().expand(BodyTuning.LIVING_MARGIN));
        Box scan = path.expand(2.35);
        for (BlockPos p : BlockPos.iterate(MathHelper.floor(scan.minX),MathHelper.floor(scan.minY),MathHelper.floor(scan.minZ),
                MathHelper.floor(scan.maxX),MathHelper.floor(scan.maxY),MathHelper.floor(scan.maxZ))) {
            if (!world.isInBuildLimit(p) || world.getChunkManager().getWorldChunk(p.getX()>>4,p.getZ()>>4) == null) continue;
            BlockState state = world.getBlockState(p);
            boolean portal = state.isOf(Blocks.NETHER_PORTAL) || state.isOf(Blocks.END_PORTAL) || state.isOf(Blocks.END_GATEWAY);
            boolean fire = state.isIn(BlockTags.FIRE) || state.getFluidState().isIn(FluidTags.LAVA)
                || state.isIn(BlockTags.CAMPFIRES) && state.contains(CampfireBlock.LIT) && state.get(CampfireBlock.LIT);
            if (portal || fire) {
                Box hazard = new Box(p).expand(portal ? 2 : .25);
                if (hazard.intersects(path)) result.add(hazard);
            }
        }
        return result;
    }
    List<Box> motionObstacles(Box path, ServerPlayerEntity owner, boolean retreat) {
        List<Box> result = obstacles(path);
        if (retreat && owner != null) for (LivingEntity e : living(path.expand(5))) {
            Box exclusion = e.getBoundingBox().expand(5);
            if (enemy(e,owner) && exclusion.intersects(path)) result.add(exclusion);
        }
        return result;
    }
    boolean target(Vec3d position, boolean allowWall, ServerPlayerEntity owner, boolean retreat) {
        return target(BodyMotion.box(position),allowWall,owner,retreat);
    }
    /** 待機復帰はhoverの上下余白を含む範囲を渡し、通常目標と同じ衝突・ロード判定を使う。 */
    boolean target(Box box, boolean allowWall, ServerPlayerEntity owner, boolean retreat) {
        // Target and movement must use exactly the same exclusion geometry.
        if (!loaded(box) || motionObstacles(box,owner,retreat).stream().anyMatch(box::intersects)) return false;
        if (!allowWall) {
            for (BlockPos p : BlockPos.iterate(MathHelper.floor(box.minX),MathHelper.floor(box.minY),MathHelper.floor(box.minZ),
                    MathHelper.floor(box.maxX),MathHelper.floor(box.maxY),MathHelper.floor(box.maxZ))) {
                BlockState state = world.getBlockState(p);
                if (state.isIn(BlockTags.LEAVES)) continue;
                if (state.getCollisionShape(world,p).offset(p).getBoundingBoxes().stream().anyMatch(box::intersects)) return false;
            }
        }
        return true;
    }
}

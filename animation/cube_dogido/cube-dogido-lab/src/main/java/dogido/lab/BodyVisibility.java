package dogido.lab;

import java.util.function.Predicate;
import net.minecraft.block.BlockState;
import net.minecraft.block.ShapeContext;
import net.minecraft.registry.Registries;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.shape.VoxelShape;
import net.minecraft.util.shape.VoxelShapes;
import net.minecraft.world.BlockView;
import net.minecraft.world.RaycastContext;

/**
 * 身体の眼から対象への見通しを実測する共通処理。入力は同時刻の線分と読込済み判定、出力は三値。
 * 食料のOUTLINEと一般視覚のCOLLIDER/ガラス透過を用途で分け、移動・消費・履歴は変更しない。
 */
final class BodyVisibility {
    enum Purpose { FOOD, VISION }
    enum Trace { CLEAR, BLOCKED, UNAVAILABLE }
    private BodyVisibility() {}

    /** 未ロードならWorldを読む前に未取得へ戻す。食料だけは対象自身のブロックへの命中を許可する。 */
    static Trace trace(BlockView world, Predicate<Box> loaded, Vec3d eye, Vec3d target,
            ShapeContext shape, Purpose purpose, BlockPos foodBlock) {
        if (!loaded.test(new Box(eye,target).expand(.01))) return Trace.UNAVAILABLE;
        boolean insideWall = false;
        if (purpose == Purpose.VISION) {
            BlockPos originPos = BlockPos.ofFloored(eye);
            BlockState originState = world.getBlockState(originPos);
            insideWall = !transparentGlass(originState) && originState.getCollisionShape(world,originPos,shape)
                .getBoundingBoxes().stream().anyMatch(box -> box.offset(originPos).contains(eye));
        }
        BodyVisionOrigin origin = new BodyVisionOrigin(eye,target,insideWall);
        var context = new RaycastContext(eye,target,
            purpose == Purpose.FOOD ? RaycastContext.ShapeType.OUTLINE : RaycastContext.ShapeType.COLLIDER,
            RaycastContext.FluidHandling.NONE,shape) {
                @Override public VoxelShape getBlockShape(BlockState state, BlockView view, BlockPos pos) {
                    VoxelShape blocks = super.getBlockShape(state,view,pos);
                    if (purpose == Purpose.FOOD) return blocks;
                    if (transparentGlass(state)) return VoxelShapes.empty();
                    // 眼を含む連続した壁の内部だけ透過する。抜けた先の壁は通常のraycastへ残す。
                    return origin.outsideInitialWall(blocks,pos);
                }
            };
        var hit = world.raycast(context);
        return hit.getType() == HitResult.Type.MISS
            || purpose == Purpose.FOOD && foodBlock != null && hit.getBlockPos().equals(foodBlock)
            ? Trace.CLEAR : Trace.BLOCKED;
    }

    /** 透過はvanillaのガラス系に限定する。葉・壁・半ブロック等は実際の衝突形状へ任せる。 */
    private static boolean transparentGlass(BlockState state) {
        var id = Registries.BLOCK.getId(state.getBlock());
        if (!id.getNamespace().equals("minecraft")) return false;
        String path = id.getPath();
        return path.equals("glass") || path.equals("glass_pane") || path.equals("tinted_glass")
            || path.endsWith("_stained_glass") || path.endsWith("_stained_glass_pane");
    }
}

package dogido.lab;

import java.util.HashMap;
import java.util.Map;
import net.minecraft.Bootstrap;
import net.minecraft.SharedConstants;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.block.ShapeContext;
import net.minecraft.block.entity.BlockEntity;
import net.minecraft.fluid.FluidState;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.BlockView;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** ゲームのBlockView/形状raycastを小さい盤面で実行する。ゲーム起動・通信・画面上の実視認とは別の検査。 */
class BodyVisibilityTest {
    @BeforeAll static void bootstrap() { SharedConstants.createGameVersion(); Bootstrap.initialize(); }
    private static class Scene implements BlockView {
        final Map<BlockPos,BlockState> blocks = new HashMap<>();
        int reads;
        @Override public BlockEntity getBlockEntity(BlockPos p) { return null; }
        @Override public BlockState getBlockState(BlockPos p) { reads++; return blocks.getOrDefault(p,Blocks.AIR.getDefaultState()); }
        @Override public FluidState getFluidState(BlockPos p) { return getBlockState(p).getFluidState(); }
        @Override public int getHeight() { return 384; }
        @Override public int getBottomY() { return -64; }
    }
    private static final Vec3d EYE = new Vec3d(.5,.75,.5), TARGET = new Vec3d(.5,.75,4.5);
    private BodyVisibility.Trace trace(Scene scene, BodyVisibility.Purpose purpose, BlockPos foodBlock) {
        return BodyVisibility.trace(scene,box -> true,EYE,TARGET,ShapeContext.absent(),purpose,foodBlock);
    }
    @Test void wallsAndLeavesOccludeButWaterAndGlassFollowVisionPolicy() {
        Scene scene = new Scene();
        assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.VISION,null));
        for (var block : java.util.List.of(Blocks.STONE,Blocks.OAK_LEAVES)) {
            scene.blocks.put(new BlockPos(0,0,2),block.getDefaultState());
            assertEquals(BodyVisibility.Trace.BLOCKED,trace(scene,BodyVisibility.Purpose.VISION,null));
        }
        for (var block : java.util.List.of(Blocks.WATER,Blocks.GLASS,Blocks.GLASS_PANE,
                Blocks.RED_STAINED_GLASS,Blocks.RED_STAINED_GLASS_PANE,Blocks.TINTED_GLASS)) {
            scene.blocks.put(new BlockPos(0,0,2),block.getDefaultState());
            assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.VISION,null));
        }
        scene.blocks.put(new BlockPos(0,0,3),Blocks.STONE.getDefaultState());
        assertEquals(BodyVisibility.Trace.BLOCKED,trace(scene,BodyVisibility.Purpose.VISION,null),"glass must not hide a wall behind it");
    }
    @Test void slabUsesItsShapeAndDifferentEyePositionsSeeDifferentResults() {
        Scene scene = new Scene();
        scene.blocks.put(new BlockPos(0,0,2),Blocks.STONE_SLAB.getDefaultState());
        assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.VISION,null));
        assertEquals(BodyVisibility.Trace.BLOCKED,BodyVisibility.trace(scene,box -> true,
            new Vec3d(.5,.25,.5),new Vec3d(.5,.25,4.5),ShapeContext.absent(),BodyVisibility.Purpose.VISION,null));
        assertEquals(BodyVisibility.Trace.CLEAR,BodyVisibility.trace(scene,box -> true,
            new Vec3d(1.5,.25,.5),new Vec3d(1.5,.25,4.5),ShapeContext.absent(),BodyVisibility.Purpose.VISION,null));
    }
    @Test void foodKeepsItsOpaqueGlassAndTargetBlockRules() {
        Scene scene = new Scene();
        scene.blocks.put(new BlockPos(0,0,2),Blocks.GLASS.getDefaultState());
        assertEquals(BodyVisibility.Trace.BLOCKED,trace(scene,BodyVisibility.Purpose.FOOD,null));
        assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.FOOD,new BlockPos(0,0,2)));
        assertEquals(BodyVisibility.Trace.BLOCKED,trace(scene,BodyVisibility.Purpose.FOOD,new BlockPos(0,0,4)));
        scene.blocks.put(new BlockPos(0,0,2),Blocks.WATER.getDefaultState());
        assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.FOOD,null));
    }
    @Test void anEyeInsideAWallSeesOutWithSpectatorViewButASeparateWallStillOccludes() {
        Scene scene = new Scene();
        scene.blocks.put(new BlockPos(0,0,0),Blocks.STONE.getDefaultState());
        assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.VISION,null));
        scene.blocks.put(new BlockPos(0,0,1),Blocks.STONE.getDefaultState());
        assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.VISION,null),"a thick initial wall is continuous");
        scene.blocks.put(new BlockPos(0,0,3),Blocks.STONE.getDefaultState());
        assertEquals(BodyVisibility.Trace.BLOCKED,trace(scene,BodyVisibility.Purpose.VISION,null),"air gap ends the inside-wall view");
        assertEquals(BodyVisibility.Trace.BLOCKED,trace(scene,BodyVisibility.Purpose.FOOD,null),"food never gets spectator vision");
        scene.blocks.clear();
        scene.blocks.put(new BlockPos(0,0,0),Blocks.GLASS.getDefaultState());
        assertEquals(BodyVisibility.Trace.CLEAR,trace(scene,BodyVisibility.Purpose.VISION,null));
    }
    @Test void spectatorOriginRespectsPartialShapeGapsAndReverseRays() {
        Scene scene = new Scene();
        scene.blocks.put(new BlockPos(0,0,0),Blocks.STONE_SLAB.getDefaultState());
        scene.blocks.put(new BlockPos(0,0,1),Blocks.STONE.getDefaultState());
        assertEquals(BodyVisibility.Trace.CLEAR,BodyVisibility.trace(scene,box -> true,
            new Vec3d(.5,.25,.5),new Vec3d(.5,.25,4.5),ShapeContext.absent(),BodyVisibility.Purpose.VISION,null));
        assertEquals(BodyVisibility.Trace.BLOCKED,trace(scene,BodyVisibility.Purpose.VISION,null),"eye above slab is outside wall");
        assertEquals(BodyVisibility.Trace.BLOCKED,BodyVisibility.trace(scene,box -> true,
            new Vec3d(.5,.5,.5),new Vec3d(.5,.5,4.5),ShapeContext.absent(),BodyVisibility.Purpose.VISION,null),
            "touching the slab top is not an eye inside a wall");
        scene.blocks.clear();
        scene.blocks.put(new BlockPos(0,0,4),Blocks.STONE.getDefaultState());
        scene.blocks.put(new BlockPos(0,0,3),Blocks.STONE.getDefaultState());
        assertEquals(BodyVisibility.Trace.CLEAR,BodyVisibility.trace(scene,box -> true,TARGET,EYE,
            ShapeContext.absent(),BodyVisibility.Purpose.VISION,null));
        scene.blocks.put(new BlockPos(0,0,1),Blocks.STONE.getDefaultState());
        assertEquals(BodyVisibility.Trace.BLOCKED,BodyVisibility.trace(scene,box -> true,TARGET,EYE,
            ShapeContext.absent(),BodyVisibility.Purpose.VISION,null));
    }
    @Test void unloadedSpaceIsUnknownWithoutReadingOrLoadingAnyBlocks() {
        Scene scene = new Scene();
        assertEquals(BodyVisibility.Trace.UNAVAILABLE,BodyVisibility.trace(scene,box -> false,EYE,TARGET,
            ShapeContext.absent(),BodyVisibility.Purpose.VISION,null));
        assertEquals(0,scene.reads);
    }
}

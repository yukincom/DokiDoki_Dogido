package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.lab.api.perception.BodyVisionGeometry;
import net.minecraft.block.Block;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.Entity;
import net.minecraft.entity.ItemEntity;
import net.minecraft.item.Item;
import net.minecraft.util.hit.*;
import net.minecraft.util.math.BlockPos;

/** ＋の命中先一個だけを同じLOOK_TARGETに保持する。周辺一覧の走査・別の向き直りは作らない。 */
record BodyPointedTarget(Entity entity, Item item, BlockPos pos, Block block) {
    static BodyPointedTarget from(MinecraftClient client, HitResult hit) {
        if (hit instanceof EntityHitResult h && h.getEntity().isAlive()) {
            Entity entity=h.getEntity();
            return new BodyPointedTarget(entity,entity instanceof ItemEntity i ? i.getStack().getItem() : null,null,null);
        }
        if (hit instanceof BlockHitResult h && hit.getType()==HitResult.Type.BLOCK && client.world!=null) {
            var state=client.world.getBlockState(h.getBlockPos());
            if (!state.isAir()) return new BodyPointedTarget(null,null,h.getBlockPos().toImmutable(),state.getBlock());
        }
        return null;
    }
    /** 成功時のドギドの眼から同じ対象を確認する。見えなくても向き直りそのものは成功のまま。 */
    String seen(MinecraftClient client, JsonObject sample) {
        if (client.world==null || !"observed".equals(sample.get("status").getAsString())) return null;
        var pose=sample.getAsJsonObject("sample");
        var vision=BodySensesCollector.vision(pose,null);
        double range=BodyVisionGeometry.RANGE;
        if (entity!=null) {
            if (!entity.isAlive() || client.world.getEntityById(entity.getId())!=entity
                    || !vision.entity(client.world,entity,range)) return null;
            if (entity instanceof ItemEntity i)
                return i.getStack().isEmpty() || i.getStack().getItem()!=item ? null : i.getStack().getName().getString();
            return entity.isInvisible() ? null : entity.getType().getName().getString();
        }
        if (client.world.getChunkManager().isChunkLoaded(pos.getX()>>4,pos.getZ()>>4)
                && client.world.getBlockState(pos).getBlock()==block
                && vision.point(client.world,pos.toCenterPos(),pos,range)) return block.getName().getString();
        return null;
    }
}

package dogido.fabric.mixin;

import dogido.fabric.DogidoClientAdapter;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.server.network.ServerPlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MobEntity.class)
abstract class MobAttackTargetMixin {
    // AIが判断した攻撃対象を読む。新しい敵走査は行わない。
    @Inject(method = "tickNewAi", at = @At("TAIL"))
    private void dogido$observeTarget(CallbackInfo ci) {
        if (((MobEntity)(Object)this).getTarget() instanceof ServerPlayerEntity player) {
            DogidoClientAdapter.recordMobTarget(player);
        }
    }
}

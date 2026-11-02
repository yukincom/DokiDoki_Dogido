package dogido.fabric.mixin;

import dogido.fabric.DogidoClientAdapter;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(MobEntity.class)
abstract class MobAttackTargetMixin {
    @Unique private boolean dogido$targetedHost;

    // AIが判断した攻撃対象を読む。新しい敵走査は行わない。
    @Inject(method = "tickNewAi", at = @At("TAIL"))
    private void dogido$observeTarget(CallbackInfo ci) {
        MobEntity mob = (MobEntity)(Object)this;
        if (!(mob.getEntityWorld() instanceof ServerWorld world)) return;
        boolean current = false;
        if (mob.getTarget() instanceof ServerPlayerEntity player) {
            var host = world.getServer().getHostProfile();
            current = host != null && host.id().equals(player.getUuid());
        }
        if (current || this.dogido$targetedHost) {
            DogidoClientAdapter.recordMobTarget(world, mob.getUuid(), current);
        }
        this.dogido$targetedHost = current;
    }
}

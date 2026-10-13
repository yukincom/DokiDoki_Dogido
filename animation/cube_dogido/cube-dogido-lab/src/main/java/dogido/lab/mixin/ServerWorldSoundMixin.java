package dogido.lab.mixin;

import dogido.lab.BodySoundCapture;
import net.minecraft.entity.Entity;
import net.minecraft.entity.damage.DamageSource;
import net.minecraft.particle.BlockParticleEffect;
import net.minecraft.particle.ParticleEffect;
import net.minecraft.registry.entry.RegistryEntry;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.sound.SoundCategory;
import net.minecraft.sound.SoundEvent;
import net.minecraft.util.collection.Pool;
import net.minecraft.world.World;
import net.minecraft.world.explosion.ExplosionBehavior;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** 通常音の終端2本と別packet経路の爆発を一度ずつ採取する。元の発音・packet・爆発処理は変更しない。 */
@Mixin(ServerWorld.class)
abstract class ServerWorldSoundMixin {
    @Inject(method = "playSound(Lnet/minecraft/entity/Entity;DDDLnet/minecraft/registry/entry/RegistryEntry;Lnet/minecraft/sound/SoundCategory;FFJ)V", at = @At("TAIL"))
    private void dogido$hearPositionedSound(Entity except, double x, double y, double z, RegistryEntry<SoundEvent> sound,
            SoundCategory category, float volume, float pitch, long seed, CallbackInfo ci) {
        // exceptは受信除外playerであり、実音源ではない。body聴覚の除外やsource UUIDには使わない。
        BodySoundCapture.record((ServerWorld)(Object)this,x,y,z,sound,category,volume,pitch,null);
    }

    @Inject(method = "playSoundFromEntity(Lnet/minecraft/entity/Entity;Lnet/minecraft/entity/Entity;Lnet/minecraft/registry/entry/RegistryEntry;Lnet/minecraft/sound/SoundCategory;FFJ)V", at = @At("TAIL"))
    private void dogido$hearEntitySound(Entity except, Entity source, RegistryEntry<SoundEvent> sound,
            SoundCategory category, float volume, float pitch, long seed, CallbackInfo ci) {
        BodySoundCapture.record((ServerWorld)(Object)this,source.getX(),source.getY(),source.getZ(),
            sound,category,volume,pitch,source.getUuid());
    }

    @Inject(method = "createExplosion(Lnet/minecraft/entity/Entity;Lnet/minecraft/entity/damage/DamageSource;Lnet/minecraft/world/explosion/ExplosionBehavior;DDDFZLnet/minecraft/world/World$ExplosionSourceType;Lnet/minecraft/particle/ParticleEffect;Lnet/minecraft/particle/ParticleEffect;Lnet/minecraft/util/collection/Pool;Lnet/minecraft/registry/entry/RegistryEntry;)V", at = @At("TAIL"))
    private void dogido$hearExplosion(Entity cause, DamageSource damage, ExplosionBehavior behavior,
            double x, double y, double z, float power, boolean fire, World.ExplosionSourceType type,
            ParticleEffect small, ParticleEffect large, Pool<BlockParticleEffect> particles,
            RegistryEntry<SoundEvent> sound, CallbackInfo ci) {
        // 爆発packetの音量は4。pitchはclientの乱数で決まり、serverでは未取得としてnullにする。
        // 爆発packetの送信を終えた時点で、その発音が届く身体だけを採取する。
        BodySoundCapture.record((ServerWorld)(Object)this,x,y,z,sound,SoundCategory.BLOCKS,4,null,null);
    }
}

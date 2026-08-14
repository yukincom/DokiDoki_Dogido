package dogido.fabric.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dogido.fabric.DogidoClientAdapter;

import net.minecraft.client.sound.SoundInstance;
import net.minecraft.client.sound.SoundManager;
import net.minecraft.client.sound.SoundSystem;

@Mixin(SoundManager.class)
abstract class SoundManagerMixin {
    @Inject(
        method = "play(Lnet/minecraft/client/sound/SoundInstance;)Lnet/minecraft/client/sound/SoundSystem$PlayResult;",
        at = @At("HEAD")
    )
    private void dogido$onPlay(
        SoundInstance sound,
        CallbackInfoReturnable<SoundSystem.PlayResult> cir
    ) {
        DogidoClientAdapter.recordPlayedSound(sound);
    }
}

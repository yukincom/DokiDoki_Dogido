package com.yukinco.dogido.fabric.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import com.yukinco.dogido.fabric.DogidoClientAdapter;

import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.network.packet.s2c.play.PlaySoundFromEntityS2CPacket;
import net.minecraft.network.packet.s2c.play.PlaySoundS2CPacket;

@Mixin(ClientPlayNetworkHandler.class)
abstract class ClientPlayNetworkHandlerMixin {
    @Inject(method = "onPlaySound", at = @At("HEAD"))
    private void dogido$onPlaySound(PlaySoundS2CPacket packet, CallbackInfo ci) {
        DogidoClientAdapter.recordGlobalSoundPacket(packet);
    }

    @Inject(method = "onPlaySoundFromEntity", at = @At("HEAD"))
    private void dogido$onPlaySoundFromEntity(PlaySoundFromEntityS2CPacket packet, CallbackInfo ci) {
        DogidoClientAdapter.recordEntitySoundPacket(packet);
    }
}

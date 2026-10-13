package dogido.lab;

import dogido.lab.api.BodyProtocol.Position;
import java.util.UUID;
import net.minecraft.registry.entry.RegistryEntry;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.sound.SoundCategory;
import net.minecraft.sound.SoundEvent;

/**
 * vanillaのserver発音を身体聴覚へ渡すmixinの入口。発音時の実座標とvanilla到達距離を使い、登録済み個体だけへ配送する。
 * player向けpacketの受信有無を根拠にせず、GameEventやブロックの存在から音を合成しない。
 */
public final class BodySoundCapture {
    private BodySoundCapture() {}
    public static void record(ServerWorld world, double x, double y, double z, RegistryEntry<SoundEvent> sound,
            SoundCategory category, float volume, Float pitch, UUID sourceEntityId) {
        if (!world.getServer().isOnThread() || sound == null || category == null
                || category == SoundCategory.UI || category == SoundCategory.MUSIC || category == SoundCategory.VOICE) return;
        SoundEvent event = sound.value();
        BodyManager.recordSound(world,new Position(x,y,z),event.id().toString(),category.getName(),volume,pitch,
            event.getDistanceToTravel(volume),sourceEntityId);
    }
}

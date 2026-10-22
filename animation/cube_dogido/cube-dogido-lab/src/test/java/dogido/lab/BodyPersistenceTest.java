package dogido.lab;

import java.util.stream.Stream;
import net.fabricmc.fabric.api.object.builder.v1.entity.FabricDefaultAttributeRegistry;
import net.minecraft.Bootstrap;
import net.minecraft.SharedConstants;
import net.minecraft.nbt.NbtCompound;
import net.minecraft.registry.RegistryWrapper;
import net.minecraft.storage.NbtReadView;
import net.minecraft.util.ErrorReporter;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 実際のMinecraftのNBT読込みを通し、召喚・旧保存データからの読込み後も自然消滅防止が残ることを確認する。 */
class BodyPersistenceTest {
    @BeforeAll static void bootstrap() {
        SharedConstants.createGameVersion();
        Bootstrap.initialize();
        FabricDefaultAttributeRegistry.register(DogidoLab.DOGIDO,DogidoEntity.attributes());
    }

    private DogidoEntity load(NbtCompound nbt) {
        // 未登録の召喚を再現する。worldのtick・所有者登録・ワープはこの検査には含めない。
        DogidoEntity body = new DogidoEntity(DogidoLab.DOGIDO,null);
        body.readCustomData(NbtReadView.create(ErrorReporter.EMPTY,RegistryWrapper.WrapperLookup.of(Stream.empty()),nbt));
        return body;
    }

    @Test void ordinarySummonWithoutPersistenceTagRemainsPersistent() {
        assertTrue(load(new NbtCompound()).isPersistent());
    }

    @Test void previouslySavedNonPersistentBodyIsRepairedOnLoad() {
        NbtCompound nbt = new NbtCompound();
        nbt.putBoolean("PersistenceRequired",false);
        assertTrue(load(nbt).isPersistent());
    }

    @Test void explicitlyPersistentBodyRemainsPersistent() {
        NbtCompound nbt = new NbtCompound();
        nbt.putBoolean("PersistenceRequired",true);
        assertTrue(load(nbt).isPersistent());
    }
}

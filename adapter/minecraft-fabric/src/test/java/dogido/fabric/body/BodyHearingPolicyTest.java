package dogido.fabric.body;

import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyHearingPolicyTest {
    private static net.minecraft.client.sound.SoundInstance sound(net.minecraft.sound.SoundCategory category,boolean relative,boolean hasId) {
        return (net.minecraft.client.sound.SoundInstance) java.lang.reflect.Proxy.newProxyInstance(
            BodyHearingPolicyTest.class.getClassLoader(),new Class<?>[]{net.minecraft.client.sound.SoundInstance.class},
            (proxy,method,args) -> switch(method.getName()) {
                case "getId" -> hasId?net.minecraft.util.Identifier.of("minecraft:block.stone.break"):null;
                case "getCategory" -> category;
                case "isRelative" -> relative;
                default -> throw new AssertionError("Unexpected sound access: "+method.getName());
            });
    }
    @Test void excludedSoundsDoNotCaptureAndEligibleSoundCapturesOnce() {
        var reads=new java.util.concurrent.atomic.AtomicInteger();
        var frame=BodyConnection.Frame.unavailable("test");
        java.util.function.Supplier<BodyConnection.Frame> capture=() -> { reads.incrementAndGet(); return frame; };
        assertNull(BodySensesCollector.captureForSound(null,capture));
        assertNull(BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,false),capture));
        for(var category:net.minecraft.sound.SoundCategory.values()) {
            assertNull(BodySensesCollector.captureForSound(sound(category,true,true),capture));
        }
        for(var category:java.util.List.of(net.minecraft.sound.SoundCategory.HOSTILE,net.minecraft.sound.SoundCategory.NEUTRAL,
                net.minecraft.sound.SoundCategory.PLAYERS,net.minecraft.sound.SoundCategory.VOICE,
                net.minecraft.sound.SoundCategory.UI,net.minecraft.sound.SoundCategory.MUSIC)) {
            assertNull(BodySensesCollector.captureForSound(sound(category,false,true),capture));
        }
        assertEquals(0,reads.get());
        assertSame(frame,BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,true),capture));
        assertEquals(1,reads.get());
    }
    @Test void arrivalUsesBodyAtEmissionAndActualSoundRange() {
        var source = new Vec3d(0,64,0);
        assertTrue(BodyHearingPolicy.arrives(new Vec3d(12,64,0),source,12));
        assertFalse(BodyHearingPolicy.arrives(new Vec3d(40,64,0),source,12));
        assertFalse(BodyHearingPolicy.arrives(source,source,Double.NaN));
    }
    @Test void snapshotAndTransportAgeAreAddedExactlyOnceAndNeverRenewTheEmission() {
        assertEquals(550,BodyHearingPolicy.serverSoundAge(100,90,50));
        assertEquals(500,BodyHearingPolicy.referenceAge(550,50));
        assertEquals(800,BodyHearingPolicy.serverSoundAge(100,90,300));
        assertEquals(500,BodyHearingPolicy.referenceAge(800,300));
        assertEquals(-1,BodyHearingPolicy.serverSoundAge(100,101,0));
        assertEquals(-1,BodyHearingPolicy.serverSoundAge(400,90,0));
    }
}

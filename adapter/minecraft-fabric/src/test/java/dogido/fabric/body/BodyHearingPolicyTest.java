package dogido.fabric.body;

import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyHearingPolicyTest {
    private static net.minecraft.client.sound.SoundInstance sound(net.minecraft.sound.SoundCategory category,boolean relative,boolean hasId) {
        return sound(category,relative,hasId,1.0,64.0,2.0,1.0f);
    }
    private static net.minecraft.client.sound.SoundInstance sound(net.minecraft.sound.SoundCategory category,boolean relative,boolean hasId,
            double x,double y,double z,float volume) {
        return (net.minecraft.client.sound.SoundInstance) java.lang.reflect.Proxy.newProxyInstance(
            BodyHearingPolicyTest.class.getClassLoader(),new Class<?>[]{net.minecraft.client.sound.SoundInstance.class},
            (proxy,method,args) -> switch(method.getName()) {
                case "getId" -> hasId?net.minecraft.util.Identifier.of("minecraft:block.stone.break"):null;
                case "getCategory" -> category;
                case "isRelative" -> relative;
                case "getX" -> x;
                case "getY" -> y;
                case "getZ" -> z;
                case "getVolume" -> volume;
                default -> throw new AssertionError("Unexpected sound access: "+method.getName());
            });
    }
    @Test void excludedSoundsDoNotCaptureAndEligibleSoundCapturesOnce() {
        var reads=new java.util.concurrent.atomic.AtomicInteger();
        java.util.function.Function<net.minecraft.util.Identifier,net.minecraft.sound.SoundEvent> events=net.minecraft.sound.SoundEvent::of;
        var frame=BodyConnection.Frame.unavailable("test");
        java.util.function.Supplier<BodyConnection.Frame> capture=() -> { reads.incrementAndGet(); return frame; };
        assertNull(BodySensesCollector.captureForSound(null,events,capture));
        assertNull(BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,false),events,capture));
        for(var category:net.minecraft.sound.SoundCategory.values()) {
            assertNull(BodySensesCollector.captureForSound(sound(category,true,true),events,capture));
        }
        for(var category:java.util.List.of(net.minecraft.sound.SoundCategory.HOSTILE,net.minecraft.sound.SoundCategory.NEUTRAL,
                net.minecraft.sound.SoundCategory.PLAYERS,net.minecraft.sound.SoundCategory.VOICE,
                net.minecraft.sound.SoundCategory.UI,net.minecraft.sound.SoundCategory.MUSIC)) {
            assertNull(BodySensesCollector.captureForSound(sound(category,false,true),events,capture));
        }
        assertEquals(0,reads.get());
        for(double invalid:new double[]{Double.NaN,Double.POSITIVE_INFINITY}) {
            assertNull(BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,true,invalid,64,2,1),events,capture));
            assertNull(BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,true,1,invalid,2,1),events,capture));
            assertNull(BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,true,1,64,invalid,1),events,capture));
        }
        for(float invalid:new float[]{0,-1,Float.NaN,Float.POSITIVE_INFINITY})
            assertNull(BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,true,1,64,2,invalid),events,capture));
        assertNull(BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,true),id -> null,capture));
        assertEquals(0,reads.get());
        var played=BodySensesCollector.captureForSound(sound(net.minecraft.sound.SoundCategory.BLOCKS,false,true),events,capture);
        assertSame(frame,played.frame()); assertEquals(new Vec3d(1,64,2),played.source()); assertEquals(16,played.range());
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

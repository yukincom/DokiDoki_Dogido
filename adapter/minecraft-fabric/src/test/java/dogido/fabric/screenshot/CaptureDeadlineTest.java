package dogido.fabric.screenshot;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class CaptureDeadlineTest {
    @Test void missingGpuCallbackExpiresAndLateResultCannotReleaseNewRequest() {
        var gate=new CaptureDeadline();assertTrue(gate.start("old",100));
        assertNull(gate.expire(99));assertEquals("old",gate.expire(100));assertFalse(gate.busy());
        assertTrue(gate.start("new",200));assertFalse(gate.claim("old",101));assertFalse(gate.finish("old"));
        assertTrue(gate.busy());assertTrue(gate.claim("new",101));assertTrue(gate.finish("new"));
    }
    @Test void arrivedImageRemainsOwnedUntilSaveFinishes() {
        var gate=new CaptureDeadline();gate.start("one",100);assertTrue(gate.claim("one",99));
        assertFalse(gate.claim("one",99));assertNull(gate.expire(200));assertFalse(gate.start("two",300));
        assertTrue(gate.finish("one"));assertTrue(gate.start("two",300));
    }
    @Test void CallbackAtDeadlineIsRejectedWithoutTick() {
        var gate=new CaptureDeadline();gate.start("one",100);assertFalse(gate.claim("one",100));
        assertEquals("one",gate.expire(100));
    }
}

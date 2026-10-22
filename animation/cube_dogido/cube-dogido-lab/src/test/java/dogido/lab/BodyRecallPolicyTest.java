package dogido.lab;

import dogido.lab.api.BodyProtocol.Mode;
import dogido.lab.control.BodyRecallPolicy;
import static dogido.lab.control.BodyRecallPolicy.Resolution.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyRecallPolicyTest {
    @Test void registeredBodyAlwaysWinsAndUnloadedBodyIsNeverReplaced() {
        for (int nearby : new int[]{0,1,2,10}) {
            assertEquals(EXISTING,BodyRecallPolicy.resolve(true,true,nearby));
            assertEquals(UNLOADED,BodyRecallPolicy.resolve(true,false,nearby));
        }
    }
    @Test void unregisteredBodyIsReusedOnlyWhenUnambiguous() {
        assertEquals(SPAWN,BodyRecallPolicy.resolve(false,false,0));
        assertEquals(BIND_NEARBY,BodyRecallPolicy.resolve(false,false,1));
        assertEquals(AMBIGUOUS,BodyRecallPolicy.resolve(false,false,2));
    }
    @Test void oldSavesDefaultToFollowAndExplicitLocalModesArePreserved() {
        assertEquals(Mode.FOLLOW,BodyRecallPolicy.savedLocalMode(""));
        assertEquals(Mode.FOLLOW,BodyRecallPolicy.savedLocalMode("unknown"));
        for (Mode mode : Mode.values()) assertEquals(mode,BodyRecallPolicy.savedLocalMode(mode.name()));
    }
    @Test void dimensionContinuationNeverReplaysCallsAcrossReconnectDeathOrRemoteLease() {
        assertTrue(BodyRecallPolicy.continueAcrossDimension(true,true,true,true,"overworld","nether"));
        assertFalse(BodyRecallPolicy.continueAcrossDimension(true,true,true,true,"overworld","overworld"));
        assertFalse(BodyRecallPolicy.continueAcrossDimension(true,false,true,true,"overworld","nether"));
        assertFalse(BodyRecallPolicy.continueAcrossDimension(true,true,false,true,"overworld","nether"));
        assertFalse(BodyRecallPolicy.continueAcrossDimension(true,true,true,false,"overworld","nether"));
        assertFalse(BodyRecallPolicy.continueAcrossDimension(false,true,true,true,"overworld","nether"));
    }
}

package dogido.fabric.observation.smell;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class ZombieScentPolicyTest {
    @Test
    void recognizesEveryConfiguredZombieFamilyWithoutCombatOrSightGate() {
        for (String type : new String[] {"zombie", "zombie_villager", "husk", "drowned", "zombified_piglin"}) {
            assertTrue(ZombieScentPolicy.isCandidate(type, 10.0));
            assertTrue(ZombieScentPolicy.isCandidate("minecraft:" + type, 1.0));
        }
        // A chicken jockey's living rider is a zombie, not a separate registry ID.
        assertFalse(ZombieScentPolicy.isCandidate("chicken_jockey", 1));
    }
    @Test
    void excludesOtherTypesAndInvalidDistances() {
        for (String type : new String[] {"skeleton", "wither_skeleton", "creeper", "example:zombie", "chicken"}) {
            assertFalse(ZombieScentPolicy.isCandidate(type, 3));
        }
        for (double distance : new double[] {-1, 10.01, Double.NaN, Double.POSITIVE_INFINITY}) {
            assertFalse(ZombieScentPolicy.isCandidate("zombie", distance));
        }
    }
}

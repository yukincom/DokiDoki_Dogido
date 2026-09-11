package dogido.fabric;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

final class ZombieScentPolicyTest {
    @Test
    void allowsOnlyCloseHiddenUnheardZombieFamily() {
        for (String type : new String[] {"zombie", "zombie_villager", "husk", "drowned"}) {
            assertTrue(ZombieScentPolicy.shouldExposeClue(type, 10.0, false, false, false));
        }
        assertTrue(
            ZombieScentPolicy.shouldExposeClue(
                "minecraft:zombie",
                4.0,
                false,
                false,
                false
            )
        );
    }

    @Test
    void excludesSkeletonAndOtherHostiles() {
        assertFalse(ZombieScentPolicy.shouldExposeClue("skeleton", 3.0, false, false, false));
        assertFalse(ZombieScentPolicy.shouldExposeClue("wither_skeleton", 3.0, false, false, false));
        assertFalse(ZombieScentPolicy.shouldExposeClue("creeper", 3.0, false, false, false));
        assertFalse(ZombieScentPolicy.shouldExposeClue("zombified_piglin", 3.0, false, false, false));
    }

    @Test
    void excludesDistantVisibleOrHeardZombie() {
        assertFalse(ZombieScentPolicy.shouldExposeClue("zombie", 10.01, false, false, false));
        assertFalse(ZombieScentPolicy.shouldExposeClue("zombie", 4.0, true, false, false));
        assertFalse(ZombieScentPolicy.shouldExposeClue("zombie", 4.0, false, true, false));
        assertFalse(ZombieScentPolicy.shouldExposeClue("zombie", 4.0, false, false, true));
    }
}

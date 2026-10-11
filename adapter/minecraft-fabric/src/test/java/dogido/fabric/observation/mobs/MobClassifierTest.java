package dogido.fabric.observation.mobs;

import java.util.UUID;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class MobClassifierTest {
    @Test void shulkerAwakeningStaysWithItsIdentityUntilReset() {
        MobClassifier classifier = new MobClassifier();
        UUID first = UUID.randomUUID(), second = UUID.randomUUID();
        assertFalse(classifier.observeShulkerAwakening(first, 0.0f));
        assertTrue(classifier.observeShulkerAwakening(first, 0.1f));
        assertTrue(classifier.observeShulkerAwakening(first, 0.0f));
        assertFalse(classifier.observeShulkerAwakening(second, 0.0f));
        classifier.reset();
        assertFalse(classifier.observeShulkerAwakening(first, 0.0f));
    }
}

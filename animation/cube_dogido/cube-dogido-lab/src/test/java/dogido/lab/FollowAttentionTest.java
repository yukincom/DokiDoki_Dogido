package dogido.lab;

import dogido.lab.control.FollowAttention;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 到着時刻と所有者の実移動で待機期限を確かめる。待機は向きを選ぶ処理を持たない。 */
class FollowAttentionTest {
    @Test void dwellStartsAtActualArrivalAndResetDiscardsIt() {
        var attention = new FollowAttention();
        attention.arrived(590);
        assertTrue(attention.dwelling(689));
        assertFalse(attention.dwelling(690));
        attention.arrived(800); attention.reset();
        assertFalse(attention.dwelling(801));
    }
    @Test void fastOwnerMotionSettlesForThreeSecondsButWalkingAndSameTickDoNotExtendIt() {
        var attention = new FollowAttention();
        attention.observeOwner(100,Vec3d.ZERO);
        assertTrue(attention.settling(159)); assertFalse(attention.settling(160));
        attention.observeOwner(160,new Vec3d(12,0,0));
        assertFalse(attention.settling(160));
        attention.observeOwner(170,new Vec3d(15,0,0));
        assertTrue(attention.settling(229));
        attention.observeOwner(170,new Vec3d(100,0,0));
        attention.observeOwner(230,new Vec3d(15,0,0));
        assertFalse(attention.settling(230));
        attention.reset(); attention.observeOwner(231,new Vec3d(15,0,0));
        assertTrue(attention.settling(231));
    }
}

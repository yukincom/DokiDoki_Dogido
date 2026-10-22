package dogido.fabric.screenshot;
import org.joml.Matrix4f;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class BodyViewRayTest {
    @Test void invertsTheCapturedProjectionAndKeepsImageAxisOrientation() {
        var matrix=new Matrix4f().perspective((float)Math.toRadians(60),16f/9,.1f,100f);
        var center=ScreenProjection.ray(matrix,.5,.5).orElseThrow();assertEquals(0,center.x(),1e-6);assertEquals(0,center.y(),1e-6);assertEquals(-1,center.z(),1e-6);
        assertTrue(ScreenProjection.ray(matrix,.8,.5).orElseThrow().x()>0);
        assertTrue(ScreenProjection.ray(matrix,.5,.2).orElseThrow().y()>0);
        var turned=new Matrix4f(matrix).rotateY((float)Math.toRadians(90));
        assertTrue(ScreenProjection.ray(turned,.5,.5).orElseThrow().x()>.99);
        assertTrue(ScreenProjection.ray(new Matrix4f().zero(),.5,.5).isEmpty());
        assertTrue(ScreenProjection.ray(matrix,Double.NaN,.5).isEmpty());assertTrue(ScreenProjection.ray(matrix,1.1,.5).isEmpty());
    }
}

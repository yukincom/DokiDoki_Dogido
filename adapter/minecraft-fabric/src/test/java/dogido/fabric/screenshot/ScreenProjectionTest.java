package dogido.fabric.screenshot;

import org.joml.Matrix4f;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ScreenProjectionTest {
    private Matrix4f projection() { return new Matrix4f().perspective((float)Math.toRadians(90),2,0.05F,100); }
    @Test void projectsKnownBoxAndFlipsScreenY() {
        var box=ScreenProjection.box(projection(),-.5,0,-4.5,.5,2,-3.5,400,200).orElseThrow();
        assertEquals(200,(box.left()+box.right())/2,0.01);
        assertEquals(100,box.bottom(),0.01);
        assertTrue(box.top()<60);assertFalse(box.clipped());
    }
    @Test void rejectsBehindCameraAndNearPlaneCrossing() {
        assertTrue(ScreenProjection.box(projection(),-.5,0,3,.5,2,4,400,200).isEmpty());
        assertTrue(ScreenProjection.box(projection(),-.5,0,-1,.5,2,.1,400,200).isEmpty());
        assertTrue(ScreenProjection.box(projection(),-.01,0,-.06,.01,.01,-.01,400,200).isEmpty());
    }
    @Test void clipsPartialScreenButRejectsFullyOutside() {
        var box=ScreenProjection.box(projection(),6,0,-4.5,10,2,-3.5,400,200).orElseThrow();
        assertTrue(box.clipped());assertEquals(400,box.right());
        assertTrue(ScreenProjection.box(projection(),20,0,-4.5,21,2,-3.5,400,200).isEmpty());
    }
    @Test void rotationAndResolutionApplyTheSameTransform() {
        var matrix=projection().mul(new Matrix4f().rotationY((float)Math.toRadians(90)));
        var a=ScreenProjection.box(matrix,3.5,0,-.5,4.5,2,.5,400,200).orElseThrow();
        var b=ScreenProjection.box(matrix,3.5,0,-.5,4.5,2,.5,192,96).orElseThrow();
        assertEquals(a.left()*192/400,b.left(),0.001);assertEquals(a.top()*96/200,b.top(),0.001);
    }
    @Test void rejectsInvalidDimensionsOrNumbers() {
        assertTrue(ScreenProjection.box(projection(),-.5,0,-4.5,.5,2,-3.5,0,200).isEmpty());
        assertTrue(ScreenProjection.box(projection(),Double.NaN,0,-4.5,.5,2,-3.5,400,200).isEmpty());
    }
}

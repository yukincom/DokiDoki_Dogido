package dogido.fabric.screenshot;

import java.util.Optional;
import org.joml.Matrix4f;
import org.joml.Vector4f;

/** 同じ描画frameの行列とcamera相対Boxを受け、画面内に切った画素枠を返す純粋計算。状態や画像は変更しない。 */
public final class ScreenProjection {
    private ScreenProjection() {}
    public record Ray(double x,double y,double z) {}
    /** 画像の左上(0,0)〜右下(1,1)を同frameのcamera相対rayへ戻す。意味や距離は推定しない。 */
    public static Optional<Ray> ray(Matrix4f projectionView,double x,double y) {
        if(!Double.isFinite(x)||!Double.isFinite(y)||x<0||x>1||y<0||y>1)return Optional.empty();
        Matrix4f inverse=new Matrix4f(projectionView).invert();
        Vector4f near=inverse.transform(new Vector4f((float)(2*x-1),(float)(1-2*y),-1,1));
        Vector4f far=inverse.transform(new Vector4f((float)(2*x-1),(float)(1-2*y),1,1));
        if(Math.abs(near.w)<1e-8 || Math.abs(far.w)<1e-8)return Optional.empty();
        double dx=far.x/far.w-near.x/near.w,dy=far.y/far.w-near.y/near.w,dz=far.z/far.w-near.z/near.w;
        double length=Math.sqrt(dx*dx+dy*dy+dz*dz);
        if(!Double.isFinite(length)||length<1e-8)return Optional.empty();
        return Optional.of(new Ray(dx/length,dy/length,dz/length));
    }
    /** 右端・下端も画素の境界座標。画像縮小時は各軸へ同じ倍率を掛ける。 */
    public record Bounds(double left, double top, double right, double bottom, boolean clipped) {}

    /**
     * 入力Boxはcameraを原点とするblock単位、行列は実描画のprojection×view。
     * near面を跨ぐBoxは今回は不成立にし、背後の点を巨大な画面内枠へ誤投影しない。
     */
    public static Optional<Bounds> box(Matrix4f projectionView, double minX, double minY, double minZ,
            double maxX, double maxY, double maxZ, int width, int height) {
        if (width <= 0 || height <= 0 || minX >= maxX || minY >= maxY || minZ >= maxZ) return Optional.empty();
        double left=Double.POSITIVE_INFINITY, top=left, right=Double.NEGATIVE_INFINITY, bottom=right;
        for (int i=0; i<8; i++) {
            var p=new Vector4f((float)((i&1)==0?minX:maxX), (float)((i&2)==0?minY:maxY),
                (float)((i&4)==0?minZ:maxZ),1);
            projectionView.transform(p);
            if (!Float.isFinite(p.x) || !Float.isFinite(p.y) || !Float.isFinite(p.z)
                    || !Float.isFinite(p.w) || p.w <= 0.00001F || p.z < -p.w || p.z > p.w) return Optional.empty();
            double x=(p.x/p.w+1)*width/2.0, y=(1-p.y/p.w)*height/2.0;
            left=Math.min(left,x); right=Math.max(right,x); top=Math.min(top,y); bottom=Math.max(bottom,y);
        }
        if (right<=0 || bottom<=0 || left>=width || top>=height) return Optional.empty();
        boolean clipped=left<0 || top<0 || right>width || bottom>height;
        return Optional.of(new Bounds(Math.max(0,left),Math.max(0,top),Math.min(width,right),Math.min(height,bottom),clipped));
    }
}

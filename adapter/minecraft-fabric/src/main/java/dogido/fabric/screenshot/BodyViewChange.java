package dogido.fabric.screenshot;

import java.awt.image.BufferedImage;
import java.util.ArrayDeque;

/** 同じ場所での旋回が続いた場合だけ4枚前と比較する。画像の意味や対象名は判定しない。
 * 枠なし原画像を32×18のRGB平均へ縮小し、平均差6/255以下かつ大きな差の画素2%以下を同じとする。
 * 移動・身体世代・画面比率が変わった場合と、比較用4枚が無い場合は新しい観測として扱う。
 */
final class BodyViewChange {
    record Sample(String context,long translation,int width,int height,int[] pixels) {}
    record Result(boolean changed,boolean compared,double meanDifference,double changedFraction) {}
    private final ArrayDeque<Sample> history=new ArrayDeque<>();
    Result observe(String context,long translation,BufferedImage image) {
        Sample current=new Sample(context,translation,image.getWidth(),image.getHeight(),pixels(image));
        Sample old=history.size()==4?history.getFirst():null;
        boolean compared=old!=null && history.stream().allMatch(s->s.context.equals(context) && s.translation==translation)
            && (long)old.width*current.height==(long)current.width*old.height;
        double mean=0,fraction=0;
        if (compared) {
            int changed=0;
            for (int i=0;i<current.pixels.length;i+=3) {
                double difference=0;
                for(int c=0;c<3;c++)difference+=Math.abs(current.pixels[i+c]-old.pixels[i+c]);
                difference/=3;mean+=difference;if(difference>24)changed++;
            }
            mean/=576;fraction=changed/576.0;
        }
        history.addLast(current);while(history.size()>4)history.removeFirst();
        return new Result(!compared || mean>6 || fraction>.02,compared,mean,fraction);
    }
    private static int[] pixels(BufferedImage image) {
        int[] pixels=new int[32*18*3];
        for(int y=0;y<18;y++)for(int x=0;x<32;x++) {
            int x0=x*image.getWidth()/32,x1=Math.max(x0+1,(x+1)*image.getWidth()/32);
            int y0=y*image.getHeight()/18,y1=Math.max(y0+1,(y+1)*image.getHeight()/18);
            long r=0,g=0,b=0,n=0;
            for(int iy=y0;iy<Math.min(y1,image.getHeight());iy++)for(int ix=x0;ix<Math.min(x1,image.getWidth());ix++) {
                int p=image.getRGB(ix,iy);r+=(p>>16)&255;g+=(p>>8)&255;b+=p&255;n++;
            }
            int i=(y*32+x)*3;pixels[i]=(int)(r/n);pixels[i+1]=(int)(g/n);pixels[i+2]=(int)(b/n);
        }
        return pixels;
    }
}

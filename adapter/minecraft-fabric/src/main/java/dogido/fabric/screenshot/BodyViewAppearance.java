package dogido.fabric.screenshot;

import java.awt.Color;
import java.util.List;
import java.util.Set;

/** 画像の見た目の閉じた分類と照合。世界やモデルには触れず、候補が一意の場合だけ実物のキーを返す。
 * 色は撮影時の画素から算出するため、照明・遮蔽の影響も含む。種類はMinecraftの実IDとtagから与える。
 */
public final class BodyViewAppearance {
    private BodyViewAppearance() {}
    public record Feature(String id,String kind,Set<String> colors,double x,double y) {}
    public static boolean valid(String kind,String color,String horizontal,String vertical) {
        return Set.of("any","mob","flower","plant","block","fluid").contains(kind)
            && Set.of("any","red","orange","yellow","green","cyan","blue","purple","pink","brown","white","gray","black").contains(color)
            && Set.of("any","left","center","right").contains(horizontal)
            && Set.of("any","upper","middle","lower").contains(vertical)
            && !(kind.equals("any")&&color.equals("any")&&horizontal.equals("any")&&vertical.equals("any"));
    }
    /** 全指定属性に一致する対象が複数なら未解決。近いものへ勝手に置換しない。 */
    public static String select(List<Feature> features,String kind,String color,String horizontal,String vertical) {
        if(!valid(kind,color,horizontal,vertical))return null;
        String found=null;
        for(var f:features) {
            if(!kind.equals("any") && !kind.equals(f.kind))continue;
            if(!color.equals("any") && !f.colors.contains(color))continue;
            String h=f.x<1.0/3?"left":f.x>2.0/3?"right":"center";
            String v=f.y<1.0/3?"upper":f.y>2.0/3?"lower":"middle";
            if(!horizontal.equals("any") && !horizontal.equals(h) || !vertical.equals("any") && !vertical.equals(v))continue;
            if(found!=null)return null;
            found=f.id;
        }
        return found;
    }
    /** RGBの色相・彩度・明度だけを使う。対象名から未観測の色を補わない。 */
    static String color(int rgb) {
        float[] hsv=Color.RGBtoHSB((rgb>>16)&255,(rgb>>8)&255,rgb&255,null);
        float h=hsv[0]*360,s=hsv[1],v=hsv[2];
        if(v<.17)return "black";
        if(s<.16)return v>.8?"white":"gray";
        if(h<15 || h>=345)return s<.65 && v>.65?"pink":"red";
        if(h<45)return v<.65?"brown":"orange";
        if(h<70)return "yellow";
        if(h<165)return "green";
        if(h<200)return "cyan";
        if(h<265)return "blue";
        if(h<300)return "purple";
        return "pink";
    }
}

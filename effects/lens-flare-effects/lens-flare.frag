// Inferred virtual sun. Additive-looking ghosts composited as subtle alpha.
#pragma static
#pragma param uOriginX 0.2
#pragma param uOriginY 0.85
#pragma param uIntensity 0.08
#pragma param uRadius 0.14
#pragma param uSpread 0.65
#pragma param uStreaks 0.6
#pragma param uHalos 0.4
#pragma param uGhosts 0.5
#pragma param uGlints 0.3
#pragma param uDispersion 0.35
#pragma param uHue 0.08
uniform float uOriginX,uOriginY,uIntensity,uRadius,uSpread,uStreaks,uHalos,uGhosts,uGlints,uDispersion,uHue;
void mainImage(out vec4 color,in vec2 fc) {
    float aspect=iResolution.x/iResolution.y;
    vec2 uv=fc/iResolution.y, sun=vec2(uOriginX*aspect,uOriginY), center=vec2(aspect*.5,.5);
    vec2 d=uv-sun; float radius=max(uRadius,.015), r=length(d)/radius;
    float light=exp(-r*r*3.5)*.4;
    light+=exp(-abs(d.y)/max(radius*.025,.001))*exp(-abs(d.x)/(radius*5.))*uStreaks;
    light+=exp(-pow((r-1.)*18.,2.))*uHalos*.18;
    float ang=atan(d.y,d.x);
    light+=pow(abs(cos(ang*3.)),36.)*exp(-r*5.)*uGlints*.5;
    vec3 c=(.65+.35*cos(6.2831853*(uHue+vec3(0.,.1,.23))))*light;
    int samples=uQuality<.8?3:6;
    for(int i=0;i<6;i++) {
        if(i>=samples) break;
        float f=float(i)+1.;
        vec2 ghost=center+(center-sun)*(f*.38-.5)*uSpread;
        vec2 v=(uv-ghost)/(radius*(.28+f*.12));
        float rr=length(v);
        float aperture=1.-smoothstep(.72,.9,max(abs(v.x),abs(v.y)*.866+abs(v.x)*.5));
        vec3 spectrum=.5+.5*cos(6.2831853*(f*.19+rr*uDispersion*.2+vec3(0.,.333,.667)));
        c+=spectrum*aperture*uGhosts*.11;
    }
    float a=clamp(max(c.r,max(c.g,c.b))*uIntensity,0.,.22);
    color=vec4(c/max(max(c.r,max(c.g,c.b)),.0001),a);
}

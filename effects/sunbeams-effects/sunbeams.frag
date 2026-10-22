// Virtual light source; no desktop luminance detection or background sampling.
#pragma param uOriginX 0.15
#pragma param uOriginY 1.1
#pragma param uAngle 25.0
#pragma param uSpread 0.8
#pragma param uSoftness 0.5
#pragma param uLength 1.5
#pragma param uIntensity 0.12
#pragma param uHaze 0.25
#pragma param uMotion 0.4
#pragma param uSpeed 1.0
#pragma param uHue 0.10
uniform float uOriginX,uOriginY,uAngle,uSpread,uSoftness,uLength,uIntensity,uHaze,uMotion,uSpeed,uHue;
float noise1(float x) {
    float i=floor(x), f=fract(x); f=f*f*(3.-2.*f);
    return mix(fract(sin(i*127.1)*43758.5),fract(sin((i+1.)*127.1)*43758.5),f);
}
void mainImage(out vec4 color,in vec2 fc) {
    vec2 uv=fc/iResolution.y;
    vec2 origin=vec2(uOriginX*iResolution.x/iResolution.y,uOriginY);
    vec2 d=uv-origin;
    float angle=atan(d.x,-d.y)-radians(uAngle);
    float t=iTime*uSpeed*uMotion;
    float rays=noise1(angle*22.+t*.09)*.65+noise1(angle*49.-t*.12)*.35;
    if (uQuality>1.) rays=mix(rays,noise1(angle*91.+t*.07),.18);
    rays=smoothstep(.32,.8+uSoftness*.15,rays);
    float cone=1.-smoothstep(max(.03,uSpread*.2),max(.04,uSpread),abs(angle));
    float lengthFade=exp(-length(d)/max(uLength,.05));
    float a=clamp(uIntensity,0.,.4)*cone*lengthFade*(rays*.8+uHaze*.2);
    vec3 col=.72+.28*cos(6.2831853*(uHue+vec3(0.,.08,.18)));
    color=vec4(col,a);
}

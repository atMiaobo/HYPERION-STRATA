// Portable approximate colour wash. True grading requires the native backend.
#pragma static
#pragma param uTemperature 0.0
#pragma param uTintHue 0.08
#pragma param uTintOpacity 0.0
#pragma param uVignette 0.0
uniform float uTemperature,uTintHue,uTintOpacity,uVignette;
void mainImage(out vec4 c,in vec2 fc){
 vec2 uv=fc/iResolution.xy;
 float warm=abs(uTemperature)*.12;
 vec3 col=uTemperature>=0.?vec3(1.,.65,.3):vec3(.3,.5,1.);
 float tint=clamp(uTintOpacity,0.,.3);
 col=mix(col,.5+.5*cos(6.2831853*(uTintHue+vec3(0.,.333,.667))),tint/max(warm+tint,.0001));
 float vig=uVignette*smoothstep(.15,.72,length((uv-.5)*vec2(1.,.85)));
 float a=warm+tint+vig*(1.-warm-tint);
 c=vec4(col*(warm+tint)/max(a,.0001),a);
}

// Portable translucent grain; this does not modify sampled desktop pixels.
#pragma param uIntensity 0.035
#pragma param uSize 1.0
#pragma param uMono 1.0
#pragma param uHue 0.08
#pragma param uColour 0.0
#pragma param uTemporal 1.0
#pragma param uSpeed 1.0
#pragma param uStep 12.0
#pragma fps uStep
uniform float uIntensity,uSize,uMono,uHue,uColour,uTemporal,uSpeed,uStep;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
void mainImage(out vec4 c,in vec2 fc){
 float t=uTemporal*floor(iTime*uSpeed*max(uStep,1.));
 vec2 p=floor(fc/max(uSize,1.))+t*vec2(13.7,9.1);
 vec3 n=vec3(hash(p),hash(p+19.),hash(p-31.));
 n=mix(n,vec3(n.r),uMono);
 n=mix(n,n*(.5+.5*cos(6.2831853*(uHue+vec3(0.,.333,.667)))),uColour);
 c=vec4(n,clamp(uIntensity,0.,.15));
}

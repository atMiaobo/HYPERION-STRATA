/* Exercise the real layer graph without a Wayland session or IPC listener. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <sys/socket.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "overlay.h"
int main(void) {
    struct overlay ov = {.visible=true,.opacity=1,.quality=1,.inotify_fd=-1};
    wl_list_init(&ov.outputs);
    assert(gl_init(&ov,true));
    char path[4096]; assert(shader_resolve("grade",path)); assert(shader_load(&ov,path));
    assert(layer_add(&ov,"one","grade")); assert(layer_add(&ov,"two","grade"));
    struct overlay *one=layer_find(&ov,"one"), *two=layer_find(&ov,"two");
    assert(one->program==two->program && one->program==ov.program);
    ov.base_disabled=true; one->visible=false; two->opacity=0;
    draw_frame(&ov,8,8,0);
    assert(one->draws==0 && two->draws==0 && ov.draws==0);
    assert(!engine_animated(&ov,NULL));
    assert(layer_add(&ov,"rays","sunbeams"));
    assert(engine_animated(&ov,NULL));
    param_set(layer_find(&ov,"rays"),"uSpeed",0);
    assert(!engine_animated(&ov,NULL));
    char name[]="/tmp/cadere-engine-test-XXXXXX"; int fd=mkstemp(name); assert(fd>=0);
    FILE *f=fdopen(fd,"w");
    for(int i=0;i<128;i++) fprintf(f,"#pragma param uP%d 0.4\nuniform float uP%d;\n",i,i);
    fprintf(f,"void mainImage(out vec4 c,in vec2 p){c=vec4(uP100,0,0,1);}\n"); fclose(f);
    assert(shader_load(one,name)); assert(one->n_params==128); assert(param_get(one,"uP100",0)>.39);
    param_set(one,"uP100",.6); GLuint good=one->program;
    f=fopen(name,"w"); fprintf(f,"this is invalid GLSL\n"); fclose(f);
    assert(!shader_load(one,name)); assert(one->program==good && param_get(one,"uP100",0)>.59);
    unlink(name);
    f=tmpfile(); assert(f);
    assert(engine_command(&ov,fileno(f),"layer order rays -10"));
    assert(!strcmp(ov.layers[0]->layer_id,"rays"));
    assert(engine_command(&ov,fileno(f),"layer opacity rays nan"));
    assert(layer_find(&ov,"rays")->opacity==1);
    fclose(f);
    for(int i=0;i<IPC_CLIENT_LIMIT;i++) ov.peers[i].fd=-1;
    int pair[2]; assert(socketpair(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK,0,pair)==0);
    ov.peers[0].fd=pair[0]; ov.peers[0].deadline=now_seconds()+1;
    struct pollfd events[IPC_CLIENT_LIMIT]={0}; events[0].revents=POLLIN;
    assert(write(pair[1],"lay",3)==3); ipc_poll_dispatch(&ov,events);
    assert(ov.peers[0].length==3 && !ov.peers[0].response);
    assert(write(pair[1],"er list\n",8)==8); ipc_poll_dispatch(&ov,events);
    char response[8192]; ssize_t got=read(pair[1],response,sizeof response-1);
    assert(got>0); response[got]=0; assert(strstr(response,"\"layers\""));
    assert(ov.peers[0].fd==-1); close(pair[1]);
    assert(socketpair(AF_UNIX,SOCK_STREAM|SOCK_NONBLOCK,0,pair)==0);
    ov.peers[0].fd=pair[0]; ov.peers[0].deadline=0;
    memset(events,0,sizeof events); ipc_poll_dispatch(&ov,events);
    assert(ov.peers[0].fd==-1); close(pair[1]);
    gl_finish(&ov);
    puts("layer/cache/idle/dynamic-params/rollback/dispatch: passed");
    return 0;
}

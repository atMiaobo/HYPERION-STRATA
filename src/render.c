/*
 * EGL/GLES3 side: context setup, ShaderToy-compatible shader wrapping,
 * noise channel textures and per-output rendering.
 */
#include <ctype.h>
#include <dirent.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "overlay.h"
#include <EGL/eglext.h>

double now_seconds(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

/* --------------------------------------------------------------- shaders */

static const char *vert_src =
	"#version 300 es\n"
	"void main() {\n"
	"    // Fullscreen triangle, no vertex buffer needed.\n"
	"    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));\n"
	"    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);\n"
	"}\n";

/* Prepended to every user shader: ShaderToy uniforms + overlay extras. */
static const char *frag_header =
	"#version 300 es\n"
	"precision highp float;\n"
	"precision highp int;\n"
	"uniform vec3  iResolution;\n"
	"uniform float iTime;\n"
	"uniform float iTimeDelta;\n"
	"uniform int   iFrame;\n"
	"uniform vec4  iMouse;\n"
	"uniform vec4  iDate;\n"
	"uniform vec3  iChannelResolution[4];\n"
	"uniform sampler2D iChannel0;\n"
	"uniform sampler2D iChannel1;\n"
	"uniform sampler2D iChannel2;\n"
	"uniform sampler2D iChannel3;\n"
	"uniform float uOpacity;\n"
    "uniform float uQuality;\n"
	"out vec4 overlay_FragColor;\n";

/*
 * mainImage() returns straight (non-premultiplied) RGBA where alpha means
 * "how much of the overlay covers the desktop". Wayland expects
 * premultiplied alpha, so we convert here.
 */
static const char *frag_footer =
	"\nvoid main() {\n"
	"    vec4 c = vec4(0.0);\n"
	"    mainImage(c, gl_FragCoord.xy);\n"
	"    c = clamp(c, 0.0, 1.0);\n"
	"    float a = c.a * uOpacity;\n"
	"    overlay_FragColor = vec4(c.rgb * a, a);\n"
	"}\n";

static GLuint compile(GLenum type, const char *const *srcs, int n)
{
	GLuint s = glCreateShader(type);
	glShaderSource(s, n, srcs, NULL);
	glCompileShader(s);
	GLint ok;
	glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
	if (!ok) {
		char log[4096];
		glGetShaderInfoLog(s, sizeof log, NULL, log);
		fprintf(stderr, "gl-overlay: shader compile error:\n%s", log);
		glDeleteShader(s);
		return 0;
	}
	return s;
}

static char *read_file(const char *path)
{
	FILE *f = fopen(path, "rb");
	if (!f)
		return NULL;
	fseek(f, 0, SEEK_END);
	long len = ftell(f);
	fseek(f, 0, SEEK_SET);
	if (len < 0 || len > 1048576) { fclose(f); return NULL; }
	char *buf = malloc(len + 1);
    if (!buf) { fclose(f); return NULL; }
	if (fread(buf, 1, len, f) != (size_t)len) {
		free(buf);
		fclose(f);
		return NULL;
	}
	buf[len] = '\0';
	fclose(f);
	return buf;
}

static struct param *param_find(struct overlay *ov, const char *name)
{
	for (int i = 0; i < ov->n_params; i++)
		if (strcmp(ov->params[i].name, name) == 0)
			return &ov->params[i];
	return NULL;
}

static struct param *param_add(struct overlay *ov, const char *name)
{
	struct param *p = param_find(ov, name);
	if (p || ov->n_params >= PARAM_LIMIT || strlen(name) >= sizeof p->name)
        return p;
    if (ov->n_params == ov->params_capacity) {
        int cap = ov->params_capacity ? ov->params_capacity * 2 : 32;
        struct param *next = realloc(ov->params, cap * sizeof *next);
        if (!next) return NULL;
        ov->params = next;
        ov->params_capacity = cap;
    }
	p = &ov->params[ov->n_params++];
	memset(p, 0, sizeof *p);
	snprintf(p->name, sizeof p->name, "%s", name);
    p->location = ov->program ? glGetUniformLocation(ov->program, name) : -1;
	return p;
}

float param_get(struct overlay *ov, const char *name, float fallback)
{
	struct param *p = param_find(ov, name);
	return p ? p->value : fallback;
}

/* Runtime (IPC) override. Sticks across shader reloads. */
void param_set(struct overlay *ov, const char *name, float value)
{
	if (!isfinite(value) || fabsf(value) > 1000000) return;
    struct param *p = param_add(ov, name);
	if (!p)
		return;
	p->value = value;
	p->overridden = true;
}

/*
 * Prepare the user source:
 *  - drop any `#version` line (we supply our own)
 *  - pick up `#pragma param <name> <default>` declarations; GLSL compilers
 *    ignore unknown pragmas, so the file stays valid ShaderToy-ish GLSL.
 */
static void preprocess(struct overlay *ov, char *src)
{
	for (char *line = src; line && *line;) {
		char *next = strchr(line, '\n');
		char *s = line;
		while (*s == ' ' || *s == '\t')
			s++;

		if (strncmp(s, "#pragma static", 14) == 0) ov->static_shader = true;
        if (strncmp(s, "#version", 8) == 0) {
			for (char *c = line; c < (next ? next : line + strlen(line)); c++)
				*c = ' ';
		} else if (strncmp(s, "#pragma sound", 13) == 0) {
			/* `#pragma sound <name> [param]`: default soundtrack, loudness follows param */
			char name[64] = "", follow[64] = "";
			if (sscanf(s + 13, " %63s %63s", name, follow) >= 1 && name[0] != '/') {
				snprintf(ov->sound_effect, sizeof ov->sound_effect, "%s", name);
				/* a trailing // comment is not a parameter */
				snprintf(ov->sound_follow, sizeof ov->sound_follow, "%s",
				         isalpha((unsigned char)follow[0]) || follow[0] == '_' ? follow : "");
			}
		} else if (strncmp(s, "#pragma fps", 11) == 0) {
			/* `#pragma fps <param>`: the picture only changes every 1/param s
			 * (held drawings), so render once per step, right after it starts */
			char name[64] = "";
			if (sscanf(s + 11, " %63s", name) == 1 && (isalpha((unsigned char)name[0]) || name[0] == '_'))
				snprintf(ov->step_follow, sizeof ov->step_follow, "%s", name);
		} else if (strncmp(s, "#pragma param", 13) == 0) {
			char name[64];
			float def;
			if (sscanf(s + 13, " %63s %f", name, &def) == 2 && isfinite(def)) {
				struct param *p = param_add(ov, name);
				if (p && !p->overridden)
					p->value = def;
			}
		}
		line = next ? next + 1 : NULL;
	}
}

/* Compiled source cache, shared by every layer in this EGL context. */
struct cached_program { GLuint program; uint64_t hash; unsigned refs; char path[4096]; };
static struct cached_program programs[LAYER_LIMIT + 4];
void shader_release(GLuint program) {
    if (!program) return;
    for (unsigned i = 0; i < sizeof programs/sizeof *programs; i++) {
        if (programs[i].program != program) continue;
        if (--programs[i].refs == 0) { glDeleteProgram(program); programs[i].program = 0; }
        return;
    }
    glDeleteProgram(program); /* native validation programs are not cached */
}
bool shader_load(struct overlay *ov, const char *path)
{
	char *src = read_file(path);
	if (!src) {
		fprintf(stderr, "gl-overlay: cannot read shader '%s'\n", path);
		return false;
	}
    uint64_t source_hash = 1469598103934665603ULL;
    for (const unsigned char *c = (unsigned char *)src; *c; c++) source_hash = (source_hash ^ *c) * 1099511628211ULL;
    struct cached_program *cached = NULL, *empty = NULL;
    for (unsigned i = 0; i < sizeof programs/sizeof *programs; i++) {
        if (!programs[i].program) empty = &programs[i];
        else if (programs[i].hash == source_hash && !strcmp(programs[i].path, path)) cached = &programs[i];
    }
    /* Parse into a separate candidate: compile failure preserves all old state. */
    struct overlay candidate = *ov;
    candidate.params = calloc(PARAM_LIMIT, sizeof *candidate.params);
    if (!candidate.params) { free(src); return false; }
    candidate.params_capacity = PARAM_LIMIT;
    bool switching = ov->program && strcmp(path, ov->shader_path) != 0;
    candidate.n_params = switching ? 0 : ov->n_params;
    if (candidate.n_params) memcpy(candidate.params, ov->params, candidate.n_params * sizeof *candidate.params);
    candidate.sound_effect[0] = candidate.sound_follow[0] = candidate.step_follow[0] = '\0';
    candidate.static_shader = false;
    preprocess(&candidate, src);

    GLuint prog;
    if (cached) { prog = cached->program; cached->refs++; free(src); goto linked; }
	const char *frag_srcs[] = { frag_header, "#line 1\n", src, frag_footer };
	GLuint vs = compile(GL_VERTEX_SHADER, &vert_src, 1);
	GLuint fs = compile(GL_FRAGMENT_SHADER, frag_srcs, 4);
	free(src);
	if (!vs || !fs) {
		if (vs) glDeleteShader(vs);
		if (fs) glDeleteShader(fs);
		goto fail; /* keep the previous program running */
	}

	prog = glCreateProgram();
	glAttachShader(prog, vs);
	glAttachShader(prog, fs);
	glLinkProgram(prog);
	glDeleteShader(vs);
	glDeleteShader(fs);

	GLint ok;
	glGetProgramiv(prog, GL_LINK_STATUS, &ok);
	if (!ok) {
		char log[4096];
		glGetProgramInfoLog(prog, sizeof log, NULL, log);
		fprintf(stderr, "gl-overlay: link error:\n%s", log);
		glDeleteProgram(prog);
		goto fail;
	}

    if (empty) {
        empty->program = prog; empty->hash = source_hash; empty->refs = 1;
        snprintf(empty->path, sizeof empty->path, "%s", path);
    }
linked:
    if (ov->program) shader_release(ov->program);
    free(ov->params);
    candidate.program = prog;
    candidate.locations_cached = false;
    *ov = candidate;
    for (int i = 0; i < ov->n_params; i++)
        ov->params[i].location = glGetUniformLocation(prog, ov->params[i].name);
    if (path != ov->shader_path) snprintf(ov->shader_path, sizeof ov->shader_path, "%s", path);
    ov->frame = 0;
    fprintf(stderr, "gl-overlay: loaded %s\n", path);
    return true;
fail:
    free(candidate.params);
    return false;
}

/* Where effects live, first match wins: the source tree (when run from it),
 * the user's own, then the installed ones. Each effect is a directory
 * <name>-effects/ holding <name>.frag and its sounds. */
int effect_dirs(const char *dirs[3], char *user, size_t len)
{
	int n = 0;
	dirs[n++] = "effects";
	const char *xdg = getenv("XDG_DATA_HOME"), *home = getenv("HOME");
	if (xdg && *xdg)
		snprintf(user, len, "%s/gl-overlay/effects", xdg), dirs[n++] = user;
	else if (home)
		snprintf(user, len, "%s/.local/share/gl-overlay/effects", home), dirs[n++] = user;
	dirs[n++] = GL_OVERLAY_DATADIR "/effects";
	return n;
}

const char *shader_resolve(const char *name, char *buf)
{
	if (strchr(name, '/') || strstr(name, ".frag"))
		return realpath(name, buf);
	const char *dirs[3];
	char user[PATH_MAX - 160], cand[PATH_MAX];
	int n = effect_dirs(dirs, user, sizeof user);
	for (int i = 0; i < n; i++) {
		snprintf(cand, sizeof cand, "%s/%.64s-effects/%.64s.frag", dirs[i], name, name);
		if (access(cand, R_OK) == 0 && realpath(cand, buf))
			return buf;
	}
	return NULL;
}

void shader_list(void (*fn)(const char *name, const char *path, void *data), void *data)
{
	const char *dirs[3];
	char user[PATH_MAX - 160], seen[2048] = " ";
	int n = effect_dirs(dirs, user, sizeof user);
	for (int i = 0; i < n; i++) {
		DIR *d = opendir(dirs[i]);
		if (!d)
			continue;
		struct dirent *e;
		while ((e = readdir(d))) {
			size_t len = strlen(e->d_name);
			if (len < 9 || len > 72 || strcmp(e->d_name + len - 8, "-effects"))
				continue;
			char name[64], key[70], path[PATH_MAX];
			snprintf(name, sizeof name, "%.*s", (int)(len - 8), e->d_name);
			snprintf(key, sizeof key, " %s ", name);
			if (strstr(seen, key) || strlen(seen) + strlen(key) >= sizeof seen)
				continue; /* shadowed by an earlier directory */
			snprintf(path, sizeof path, "%s/%s/%s.frag", dirs[i], e->d_name, name);
			if (access(path, R_OK) != 0)
				continue;
			strcat(seen, key + 1);
			char abs[PATH_MAX];
			fn(name, realpath(path, abs) ? abs : path, data);
		}
		closedir(d);
	}
}

/* -------------------------------------------------------------- channels */

/* ShaderToy-style RGBA noise, tiling, mipmapped. */
static GLuint make_noise(int size, uint32_t seed)
{
	unsigned char *px = malloc(size * size * 4);
	for (int i = 0; i < size * size * 4; i++) {
		seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
		px[i] = seed & 0xff;
	}
	GLuint tex;
	glGenTextures(1, &tex);
	glBindTexture(GL_TEXTURE_2D, tex);
	glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, size, size, 0, GL_RGBA,
	             GL_UNSIGNED_BYTE, px);
	glGenerateMipmap(GL_TEXTURE_2D);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
	free(px);
	return tex;
}

/* ------------------------------------------------------------------- EGL */

/* headless: no Wayland, render into an FBO (used by gl-overlay-still). */
bool gl_init(struct overlay *ov, bool headless)
{
    ov->base_opacity = 1;
	ov->egl_display = headless
		? eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL)
		: eglGetPlatformDisplay(EGL_PLATFORM_WAYLAND_KHR, ov->display, NULL);
	if (ov->egl_display == EGL_NO_DISPLAY ||
	    !eglInitialize(ov->egl_display, NULL, NULL)) {
		fprintf(stderr, "gl-overlay: EGL init failed\n");
		return false;
	}
	eglBindAPI(EGL_OPENGL_ES_API);

	const EGLint cfg_attr[] = {
		EGL_SURFACE_TYPE, headless ? 0 : EGL_WINDOW_BIT,
		EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT,
		EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8,
		EGL_ALPHA_SIZE, 8, /* a translucent overlay needs a real alpha channel */
		EGL_NONE,
	};
	EGLint n = 0;
	if (!eglChooseConfig(ov->egl_display, cfg_attr, &ov->egl_config, 1, &n) || !n) {
		fprintf(stderr, "gl-overlay: no RGBA8 GLES3 EGL config\n");
		return false;
	}

	const EGLint ctx_attr[] = { EGL_CONTEXT_MAJOR_VERSION, 3, EGL_NONE };
	ov->egl_context = eglCreateContext(ov->egl_display, ov->egl_config,
	                                   EGL_NO_CONTEXT, ctx_attr);
	if (ov->egl_context == EGL_NO_CONTEXT) {
		fprintf(stderr, "gl-overlay: cannot create GLES3 context\n");
		return false;
	}
	/* Surfaceless current so we can compile shaders before any output exists. */
	eglMakeCurrent(ov->egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, ov->egl_context);

	glGenVertexArrays(1, &ov->vao);
	for (int i = 0; i < 4; i++) {
		ov->channels[i] = make_noise(256, 0x9e3779b9u * (i + 1));
		ov->channel_res[i][0] = ov->channel_res[i][1] = 256;
	}
	return true;
}

void gl_finish(struct overlay *ov)
{
	if (ov->egl_display == EGL_NO_DISPLAY || !ov->egl_display)
		return;
	if (ov->egl_context) {
		eglMakeCurrent(ov->egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, ov->egl_context);
		if (ov->program) shader_release(ov->program);
        free(ov->params); ov->params = NULL;
        for (int i = 0; i < ov->n_layers; i++) layer_free(ov->layers[i]);
        free(ov->layers); ov->layers = NULL; ov->n_layers = 0;
		glDeleteTextures(4, ov->channels);
        if (ov->native_texture) glDeleteTextures(1, &ov->native_texture);
		glDeleteVertexArrays(1, &ov->vao);
		eglMakeCurrent(ov->egl_display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
		eglDestroyContext(ov->egl_display, ov->egl_context);
	}
	eglTerminate(ov->egl_display);
}

/* ---------------------------------------------------------------- render */

void output_render(struct overlay *ov, struct output *out)
{
	if (!out->configured || out->egl_surface == EGL_NO_SURFACE)
		return;
	/* Paused/hidden and nothing changed: stop the frame loop -> ~0% GPU. */
	if ((ov->paused || !ov->visible) && !out->dirty)
		return;
	out->dirty = false;

	double now = now_seconds();
	double dt = ov->last_frame > 0 ? now - ov->last_frame : 0;
	if (!ov->paused && dt > 0)
		ov->time += dt;
	ov->last_frame = now;
	out->last_render = now;
	out->render_time = ov->time;

	eglMakeCurrent(ov->egl_display, out->egl_surface, out->egl_surface, ov->egl_context);
	ov->render_output = out->name;
    float opacity = ov->opacity;
    ov->opacity *= out->output_opacity;
    bool visible = ov->visible;
    ov->visible = visible && !out->disabled;
    double begin = now_seconds();
    draw_frame(ov, out->buffer_width, out->buffer_height, dt);
    ov->cpu_frame_ms = (now_seconds() - begin) * 1000.0;
    ov->opacity = opacity; ov->visible = visible;

	sound_update(ov, false);

	/* Request the next frame *before* swapping (swap commits the surface). */
	if (!ov->paused && ov->visible && !out->disabled && out->output_opacity > 0 && engine_animated(ov, out->name))
		output_request_frame(out);
	eglSwapBuffers(ov->egl_display, out->egl_surface);
}

/* Draw one frame of the active shader into the current framebuffer. */
static void draw_program(struct overlay *ov, int w, int h, double dt)
{

	if (ov->visible && ov->program) {
		GLuint p = ov->program;
        if (!ov->locations_cached) {
            static const char *names[] = {"iResolution","iTime","iTimeDelta","iFrame","iMouse","uOpacity","iDate","iChannel0","iChannel1","iChannel2","iChannel3","iChannelResolution","uQuality"};
            for (int i = 0; i < 13; i++) ov->uniforms[i] = glGetUniformLocation(p, names[i]);
            ov->locations_cached = true;
        }
		glUseProgram(p);
        glUniform1f(ov->uniforms[12], ov->quality > 0 ? ov->quality : 1.0f);

		glUniform3f(ov->uniforms[0], w, h, 1.0f);
		glUniform1f(ov->uniforms[1], (float)(ov->frozen ? ov->frozen_time : ov->time - ov->time_offset));
		glUniform1f(ov->uniforms[2], (float)dt);
		glUniform1i(ov->uniforms[3], ov->frame);
		/* We take no input, so there is no pointer; scripts can drive it via IPC. */
		struct param *mx = param_find(ov, "iMouse.x"), *my = param_find(ov, "iMouse.y");
		glUniform4f(ov->uniforms[4],
		            mx ? mx->value : 0, my ? my->value : 0, 0, 0);
		glUniform1f(ov->uniforms[5], ov->opacity);

		time_t t = time(NULL);
		struct tm tm;
		localtime_r(&t, &tm);
		glUniform4f(ov->uniforms[6], tm.tm_year + 1900, tm.tm_mon,
		            tm.tm_mday, tm.tm_hour * 3600 + tm.tm_min * 60 + tm.tm_sec);

		float cres[12];
		for (int i = 0; i < 4; i++) {
			char name[16];
			snprintf(name, sizeof name, "iChannel%d", i);
			glActiveTexture(GL_TEXTURE0 + i);
			glBindTexture(GL_TEXTURE_2D, ov->channels[i]);
			glUniform1i(ov->uniforms[7 + i], i);
			cres[i * 3 + 0] = ov->channel_res[i][0];
			cres[i * 3 + 1] = ov->channel_res[i][1];
			cres[i * 3 + 2] = 1;
		}
		glUniform3fv(ov->uniforms[11], 4, cres);
        if (ov->native_texture) { glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, ov->native_texture); }

		for (int i = 0; i < ov->n_params; i++) {
			GLint loc = ov->params[i].location;
			if (loc >= 0)
				glUniform1f(loc, ov->params[i].value);
		}

		glBindVertexArray(ov->vao);
		glDrawArrays(GL_TRIANGLES, 0, 3);
		ov->frame++; ov->draws++;
	}
}

void draw_frame(struct overlay *ov, int w, int h, double dt)
{
    glViewport(0, 0, w, h);
    glClearColor(0, 0, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    if (!ov->visible || ov->opacity <= 0) return;
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    if (!ov->base_disabled && ov->base_opacity > 0) {
        float opacity=ov->opacity; ov->opacity*=ov->base_opacity;
        draw_program(ov,w,h,dt); ov->opacity=opacity;
    }
    for (int i = 0; i < ov->n_layers; i++) {
        struct overlay *layer = ov->layers[i];
        if (!layer->visible || layer->opacity <= 0) continue;
        if (layer->output_filter[0] && ov->render_output && strcmp(layer->output_filter, ov->render_output)) continue;
        layer->time = ov->time;
        layer->quality = ov->quality;
        layer->vao = ov->vao;
        memcpy(layer->channels, ov->channels, sizeof ov->channels);
        memcpy(layer->channel_res, ov->channel_res, sizeof ov->channel_res);
        float opacity = layer->opacity;
        layer->opacity *= ov->opacity;
        draw_program(layer, w, h, dt);
        layer->opacity = opacity;
    }
    glDisable(GL_BLEND);
}

/* Native-screen-shader validator / test renderer. The live overlay never binds
 * a desktop texture; this entry point is only used by gl-overlay-still. */
bool native_shader_load(struct overlay *ov, const char *path) {
    char *src = read_file(path);
    if (!src) return false;
    const char *vertex = "#version 300 es\nout vec2 v_texcoord;\nvoid main(){ vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2)); v_texcoord=p; gl_Position=vec4(p*2.0-1.0,0,1); }\n";
    const char *fragment = src;
    GLuint vs = compile(GL_VERTEX_SHADER, &vertex, 1), fs = compile(GL_FRAGMENT_SHADER, &fragment, 1);
    free(src);
    if (!vs || !fs) { if (vs) glDeleteShader(vs); if (fs) glDeleteShader(fs); return false; }
    GLuint p = glCreateProgram(); glAttachShader(p, vs); glAttachShader(p, fs); glLinkProgram(p);
    glDeleteShader(vs); glDeleteShader(fs);
    GLint ok; glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) { glDeleteProgram(p); return false; }
    if (ov->program) shader_release(ov->program);
    ov->program = p; ov->locations_cached = false;
    glUseProgram(p); glUniform1i(glGetUniformLocation(p, "tex"), 0);
    return true;
}

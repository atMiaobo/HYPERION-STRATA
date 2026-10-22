/*
 * gl-overlay-still — render one frame of a shader headlessly, for previews
 * and shader development without touching the live desktop.
 *
 *   gl-overlay-still -s rain -t 42 -p uDream=0 -o out.pam
 *   magick bg.png out.pam -composite preview.png
 *
 * Output is PAM (RGB_ALPHA, straight alpha), top row first.
 */
#include <getopt.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "overlay.h"

int main(int argc, char **argv)
{
	struct overlay ov = { .opacity = 1.0f, .visible = true };
	const char *shader = "rain", *outpath = "still.pam";
	int w = 1920, h = 1200;
	double t = 10.0;
	int bench = 0;
    const char *native = NULL, *background = NULL;
    char *layers[16], *layer_params[128]; int nl = 0, np = 0;

	int c;
	while ((c = getopt(argc, argv, "s:o:t:w:h:p:n:l:P:bN:B:")) != -1) {
		switch (c) {
		case 's': shader = optarg; break;
        case 'N': native = optarg; break;
        case 'B': background = optarg; break;
        case 'b': ov.base_disabled = true; break;
        case 'l': if (nl == 16) return 1; layers[nl++] = optarg; break;
        case 'P': if (np == 128) return 1; layer_params[np++] = optarg; break;
		case 'o': outpath = optarg; break;
		case 't': t = strtod(optarg, NULL); break;
		case 'w': w = atoi(optarg); break;
		case 'h': h = atoi(optarg); break;
		case 'n': bench = atoi(optarg); break;
		case 'p': {
			char *eq = strchr(optarg, '=');
			if (!eq)
				return 1;
			*eq = '\0';
			param_set(&ov, optarg, strtof(eq + 1, NULL));
			break;
		}
		default:
			fprintf(stderr, "usage: gl-overlay-still [-s effect|shader.frag] [-o out.pam] "
			                "[-t seconds] [-w W] [-h H] [-n bench_frames] [-p name=value]...\n"
                                "  [-l ID:EFFECT] [-P ID:PARAM=VALUE] [-b disable-primary]\n"
                                "  [-N native.frag] [-B reference.ppm]\n");
			return 1;
		}
	}

	char path[PATH_MAX];
	if (!native && !shader_resolve(shader, path)) {
		fprintf(stderr, "gl-overlay-still: %s: no such effect or shader file\n", shader);
		return 1;
	}
	if (w < 1 || h < 1 || w > 16384 || h > 16384) return 1;
    if (!gl_init(&ov, true) || !(native ? native_shader_load(&ov, native) : shader_load(&ov, path))) return 1;
    for (int i = 0; i < nl; i++) {
        char *sep = strchr(layers[i], ':'); if (!sep) return 1; *sep = 0;
        if (!layer_add(&ov, layers[i], sep+1)) return 1;
    }
    for (int i = 0; i < np; i++) {
        char *sep = strchr(layer_params[i], ':'), *eq = strchr(layer_params[i], '=');
        if (!sep || !eq || eq < sep) return 1;
        *sep = *eq = 0;
        struct overlay *layer = layer_find(&ov, layer_params[i]); if (!layer) return 1;
        if (!strcmp(sep+1, "opacity")) layer->opacity = strtof(eq+1, NULL);
        else param_set(layer, sep+1, strtof(eq+1, NULL));
    }
    if (background) {
        FILE *bg = fopen(background, "rb"); int bw, bh, maxval; char magic[3];
        if (!bg || fscanf(bg, "%2s %d %d %d", magic, &bw, &bh, &maxval) != 4 || strcmp(magic,"P6") || bw < 1 || bh < 1 || bw > 16384 || bh > 16384 || maxval != 255) return 1;
        fgetc(bg); size_t size = (size_t)bw*bh*3; unsigned char *data = malloc(size);
        if (!data || fread(data,1,size,bg) != size) return 1;
        fclose(bg);
        glGenTextures(1, &ov.native_texture); glBindTexture(GL_TEXTURE_2D, ov.native_texture);
        glPixelStorei(GL_UNPACK_ALIGNMENT,1);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGB8,bw,bh,0,GL_RGB,GL_UNSIGNED_BYTE,data); free(data);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
    }

	GLuint fbo, rb;
	glGenFramebuffers(1, &fbo);
	glBindFramebuffer(GL_FRAMEBUFFER, fbo);
	glGenRenderbuffers(1, &rb);
	glBindRenderbuffer(GL_RENDERBUFFER, rb);
	glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, w, h);
	glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, rb);

	ov.time = t;
	draw_frame(&ov, w, h, 1.0 / 60.0);

	if (bench > 0) { /* -n N: time N frames, GPU included */
		glFinish();
		double t0 = now_seconds();
		for (int i = 0; i < bench; i++) {
			ov.time += 1.0 / 60.0;
			draw_frame(&ov, w, h, 1.0 / 60.0);
		}
		glFinish();
		fprintf(stderr,"renderer: %s\n", glGetString(GL_RENDERER));
		printf("%.2f ms/frame at %dx%d\n", (now_seconds() - t0) * 1000.0 / bench, w, h);
	}

	unsigned char *px = malloc((size_t)w * h * 4);
	glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, px);

	FILE *f = fopen(outpath, "wb");
	if (!f) {
		perror(outpath);
		return 1;
	}
	fprintf(f, "P7\nWIDTH %d\nHEIGHT %d\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n", w, h);
	for (int y = h - 1; y >= 0; y--) {
		unsigned char *row = px + (size_t)y * w * 4;
		for (int x = 0; x < w; x++) {
			unsigned char *p = row + x * 4;
			/* GL gave us premultiplied; PAM wants straight alpha. */
			if (p[3]) {
				for (int i = 0; i < 3; i++) {
					int v = p[i] * 255 / p[3];
					p[i] = v > 255 ? 255 : v;
				}
			}
		}
		fwrite(row, 4, w, f);
	}
	fclose(f);
	free(px);

	glDeleteRenderbuffers(1, &rb);
	glDeleteFramebuffers(1, &fbo);
	gl_finish(&ov);
	return 0;
}

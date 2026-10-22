/* Bounded layer graph. Disabled programs remain cached until explicit removal. */
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/resource.h>
#include "overlay.h"

struct overlay *layer_find(struct overlay *ov, const char *id) {
    if (!strcmp(id, "base")) return ov;
    for (int i = 0; i < ov->n_layers; i++)
        if (!strcmp(ov->layers[i]->layer_id, id)) return ov->layers[i];
    return NULL;
}
void layer_free(struct overlay *layer) {
    if (layer->program) shader_release(layer->program);
    free(layer->params); free(layer);
}
static bool valid_id(const char *id) {
    if (!*id || strlen(id) > 63 || !strcmp(id, "base")) return false;
    for (; *id; id++) if (!isalnum((unsigned char)*id) && *id != '-' && *id != '_') return false;
    return true;
}
bool layer_add(struct overlay *ov, const char *id, const char *effect) {
    if (!valid_id(id) || layer_find(ov, id) || ov->n_layers >= LAYER_LIMIT) return false;
    char path[4096];
    if (!shader_resolve(effect, path)) return false;
    struct overlay *layer = calloc(1, sizeof *layer);
    if (!layer) return false;
    layer->opacity = 1; layer->visible = true; layer->order = ov->n_layers;
    snprintf(layer->layer_id, sizeof layer->layer_id, "%s", id);
    if (!shader_load(layer, path)) { layer_free(layer); return false; }
    struct overlay **next = realloc(ov->layers, (ov->n_layers + 1) * sizeof *next);
    if (!next) { layer_free(layer); return false; }
    ov->layers = next; ov->layers[ov->n_layers++] = layer;
    return true;
}
static bool animated(struct overlay *s) {
    return s->visible && s->opacity > 0 && !s->frozen && !s->static_shader && param_get(s, "uSpeed", 1) != 0 && param_get(s, "uTemporal", 1) != 0 && param_get(s, "uMotion", 1) != 0;
}
bool engine_animated(struct overlay *ov, const char *output) {
    if (ov->opacity <= 0) return false;
    if (!ov->base_disabled && ov->base_opacity > 0 && animated(ov)) return true;
    for (int i = 0; i < ov->n_layers; i++) {
        struct overlay *s = ov->layers[i];
        if (output && s->output_filter[0] && strcmp(output, s->output_filter)) continue;
        if (animated(s)) return true;
    }
    return false;
}
double engine_step(struct overlay *ov, const char *output) {
    double fastest = 0;
    for (int i = -1; i < ov->n_layers; i++) {
        struct overlay *s = i < 0 ? ov : ov->layers[i];
        if ((i < 0 && (ov->base_disabled || ov->base_opacity <= 0)) || !animated(s)) continue;
        if (i >= 0 && output && s->output_filter[0] && strcmp(output, s->output_filter)) continue;
        float step = s->step_follow[0] ? param_get(s, s->step_follow, 0) : 0;
        if (step <= 0) return 0; /* any continuously animated layer wins */
        if (step > fastest) fastest = step;
    }
    return fastest;
}
static void json_string(int fd, const char *s) {
    dprintf(fd, "\"");
    for (; *s; s++) {
        unsigned char c = *s;
        if (c == '"' || c == '\\') dprintf(fd, "\\%c", c);
        else if (c < 32) dprintf(fd, "\\u%04x", c);
        else dprintf(fd, "%c", c);
    }
    dprintf(fd, "\"");
}
static int compare_layers(const void *a, const void *b) {
    const struct overlay *x = *(struct overlay *const *)a, *y = *(struct overlay *const *)b;
    if (x->order != y->order) return x->order < y->order ? -1 : 1;
    return strcmp(x->layer_id, y->layer_id);
}
bool engine_command(struct overlay *ov, int fd, const char *line) {
    char cmd[32] = "", action[64] = "", id[64] = "", key[64] = "", val[4096] = "";
    int n = sscanf(line, "%31s %63s %63s %63s %4095s", cmd, action, id, key, val);
    if (!strcmp(cmd, "engine") || (!strcmp(cmd, "layer") && !strcmp(action, "list"))) {
        dprintf(fd, "{\"backend\":\"wayland-overlay\",\"background_sampling\":false,\"moving_refraction\":false,\"cpu_submit_ms\":%.4f,\"base_enabled\":%s,\"quality\":%.2f,\"layers\":[", ov->cpu_frame_ms, ov->base_disabled ? "false" : "true", ov->quality);
        for (int i = -1; i < ov->n_layers; i++) {
            struct overlay *s = i < 0 ? ov : ov->layers[i];
            dprintf(fd, "%s{\"id\":", i == -1 ? "" : ","); json_string(fd, i < 0 ? "base" : s->layer_id);
            dprintf(fd, ",\"shader\":"); json_string(fd, s->shader_path);
            dprintf(fd, ",\"output\":"); json_string(fd, s->output_filter);
            dprintf(fd, ",\"enabled\":%s,\"animated\":%s,\"opacity\":%g,\"order\":%d,\"draws\":%lu,\"params\":{", (i < 0 ? !ov->base_disabled : s->visible) ? "true":"false", animated(s) ? "true":"false", i < 0 ? s->base_opacity : s->opacity, s->order, s->draws);
            for (int j = 0; j < s->n_params; j++) { if (j) dprintf(fd, ","); json_string(fd, s->params[j].name); dprintf(fd, ":%g", s->params[j].value); }
            dprintf(fd, "}}");
        }
        struct rusage usage; getrusage(RUSAGE_SELF, &usage);
        dprintf(fd, "],\"gpu_frame_ms\":null,\"rss_peak_kib\":%ld,\"renderer\":", usage.ru_maxrss);
        json_string(fd, (const char *)glGetString(GL_RENDERER));
        dprintf(fd, "}\n"); return true;
    }
    if (!strcmp(cmd, "outputs")) {
        struct output *out;
        wl_list_for_each(out, &ov->outputs, link)
            dprintf(fd, "%s logical=%ux%u buffer=%dx%d scale=%g enabled=%d opacity=%g fps=%g\n", out->name, out->width, out->height, out->buffer_width, out->buffer_height, out->fractional_scale ? out->fractional_scale / 120.0 : out->scale, !out->disabled, out->output_opacity, out->fps_override);
        return true;
    }
    if (!strcmp(cmd, "quality")) {
        if (!strcmp(action, "low")) ov->quality = .65;
        else if (!strcmp(action, "balanced")) ov->quality = 1;
        else if (!strcmp(action, "high")) ov->quality = 1.25;
        else { dprintf(fd, "error: quality low|balanced|high\n"); return true; }
        struct output *out; wl_list_for_each(out, &ov->outputs, link) output_resize_buffer(out);
    } else if (!strcmp(cmd, "base")) {
        if (strcmp(action, "0") && strcmp(action, "1")) goto invalid;
        ov->base_disabled = !strcmp(action, "0");
    } else if (!strcmp(cmd, "output") && n == 4) {
        char *end; float v = strtof(key, &end);
        if (*end || !isfinite(v)) goto invalid;
        bool enabled = !strcmp(id,"enabled") && (v == 0 || v == 1);
        bool opacity = !strcmp(id,"opacity") && v >= 0 && v <= 1;
        bool fps = !strcmp(id,"fps") && v >= 0 && v <= 240;
        if (!enabled && !opacity && !fps) goto invalid;
        struct output_rule *rule = NULL;
        for(int i=0;i<ov->n_output_rules;i++) if(!strcmp(ov->output_rules[i].name,action)) rule=&ov->output_rules[i];
        if(!rule) {
            if(ov->n_output_rules==32) goto invalid;
            rule=&ov->output_rules[ov->n_output_rules++];
            snprintf(rule->name,sizeof rule->name,"%s",action); rule->opacity=1;
        }
        if(enabled) rule->disabled=v==0;
        if(opacity) rule->opacity=v;
        if(fps) rule->fps=v;
        struct output *out;
        wl_list_for_each(out, &ov->outputs, link) if (!strcmp(out->name, action)) {
            out->disabled=rule->disabled; out->output_opacity=rule->opacity; out->fps_override=rule->fps;
        }
    } else if (!strcmp(cmd, "layer")) {
        if (!strcmp(action, "add") && n == 4) {
            if (!layer_add(ov, id, key)) goto invalid;
        } else {
            struct overlay *s = layer_find(ov, id);
            if (!s) goto invalid;
            char *end; float v = strtof(n == 5 ? val : key, &end);
            if (!strcmp(action, "set") && n == 5 && !*end && isfinite(v) && fabsf(v) <= 1000000) {
                bool known = false;
                for (int i = 0; i < s->n_params; i++) if (!strcmp(key, s->params[i].name)) known = true;
                if (!known) goto invalid;
                param_set(s, key, v);
            } else if (!strcmp(action, "output") && n == 4) {
                snprintf(s->output_filter, sizeof s->output_filter, "%s", strcmp(key, "*") ? key : "");
            } else if (!strcmp(action, "reload") && n == 3) {
                if (!shader_load(s, s->shader_path)) goto invalid;
            } else if (!strcmp(action, "remove") && n == 3 && s != ov) {
                for (int i = 0; i < ov->n_layers; i++) if (ov->layers[i] == s) {
                    layer_free(s); memmove(ov->layers+i, ov->layers+i+1, (--ov->n_layers-i)*sizeof *ov->layers); break;
                }
            } else if (n == 4 && !*end && isfinite(v)) {
                if (!strcmp(action, "enable") && (v == 0 || v == 1)) { if (s == ov) ov->base_disabled = v == 0; else s->visible = v != 0; }
                else if (!strcmp(action, "opacity") && v >= 0 && v <= 1) { if(s == ov) ov->base_opacity=v; else s->opacity=v; }
                else if (!strcmp(action, "animate") && (v == 0 || v == 1)) { if (v == 0 && !s->frozen) s->frozen_time = ov->time - s->time_offset;
                    if (v == 1 && s->frozen) s->time_offset = ov->time - s->frozen_time;
                    s->frozen = v == 0; }
                else if (!strcmp(action, "order") && fabsf(v) <= 1000 && s != ov) { s->order = (int)v; qsort(ov->layers, ov->n_layers, sizeof *ov->layers, compare_layers); }
                else goto invalid;
            } else goto invalid;
        }
    } else return false;
    outputs_schedule_all(ov); dprintf(fd, "ok\n"); return true;
invalid:
    dprintf(fd, "error: invalid engine command, value, layer, or resource limit\n"); return true;
}

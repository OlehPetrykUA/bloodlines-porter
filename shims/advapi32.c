#include "recomp_types.h"
#include "rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct val {
    char *name;
    uint32_t type, n;
    unsigned char *data;
} val;

typedef struct key {
    char *name;
    struct key *parent, **kids;
    val *vals;
    int nkids, nvals;
} key;

static key s_roots[4] = {{.name = "HKEY_CLASSES_ROOT"}, {.name = "HKEY_CURRENT_USER"}, {.name = "HKEY_LOCAL_MACHINE"},
                         {.name = "HKEY_USERS"}};
static const char *const k_root_names[4] = {"HKEY_CLASSES_ROOT", "HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE", "HKEY_USERS"};
static key *s_handles[1024];
static char s_path[1024];
static char **s_logged;
static int s_nlogged;

static key *child(key *k, const char *name, int create, int *created)
{
    for (int i = 0; i < k->nkids; i++)
        if (!rt_stricmp(k->kids[i]->name, name)) return k->kids[i];
    if (!create) return NULL;
    key *c = calloc(1, sizeof *c);
    c->name = strdup(name);
    c->parent = k;
    k->kids = realloc(k->kids, (size_t)(k->nkids + 1) * sizeof *k->kids);
    k->kids[k->nkids++] = c;
    if (created) *created = 1;
    return c;
}

static key *walk(key *k, const char *sub, int create, int *created)
{
    char b[1024];
    snprintf(b, sizeof b, "%s", sub ? sub : "");
    char *save;
    for (char *t = strtok_r(b, "\\", &save); t && k; t = strtok_r(NULL, "\\", &save))
        if (*t) k = child(k, t, create, created);
    return k;
}

static key *from_handle(uint32_t h)
{
    if (h >= 0x80000000u && h <= 0x80000003u) return &s_roots[h - 0x80000000u];
    if (h >= 0x00050000u && h < 0x00050000u + 4u * 1024u && !(h & 3u)) return s_handles[(h - 0x00050000u) / 4u];
    return NULL;
}

static uint32_t new_handle(key *k)
{
    for (int i = 1; i < 1024; i++)
        if (!s_handles[i]) {
            s_handles[i] = k;
            return 0x00050000u + 4u * (uint32_t)i;
        }
    rt_fatal(5, "registry handle table full");
}

static val *find_val(key *k, const char *name)
{
    for (int i = 0; i < k->nvals; i++)
        if (!rt_stricmp(k->vals[i].name, name ? name : "")) return &k->vals[i];
    return NULL;
}

static void set_val(key *k, const char *name, uint32_t type, const void *data, uint32_t n)
{
    val *v = find_val(k, name);
    if (!v) {
        k->vals = realloc(k->vals, (size_t)(k->nvals + 1) * sizeof *k->vals);
        v = &k->vals[k->nvals++];
        v->name = strdup(name ? name : "");
        v->data = NULL;
    }
    free(v->data);
    v->type = type;
    v->n = n;
    v->data = malloc(n ? n : 1);
    memcpy(v->data, data, n);
}

static void key_path(key *k, char *out, size_t n)
{
    if (!k->parent) {
        snprintf(out, n, "%s", k->name);
        return;
    }
    char b[1024];
    key_path(k->parent, b, sizeof b);
    snprintf(out, n, "%s\\%s", b, k->name);
}

static void put_str(FILE *f, const char *s)
{
    fputc('"', f);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') fputc('\\', f);
        fputc(*s, f);
    }
    fputc('"', f);
}

static void save_key(FILE *f, key *k)
{
    if (k->parent) {
        char p[1024];
        key_path(k, p, sizeof p);
        fprintf(f, "\n[%s]\n", p);
        for (int i = 0; i < k->nvals; i++) {
            val *v = &k->vals[i];
            if (v->name[0]) put_str(f, v->name);
            else fputc('@', f);
            fputc('=', f);
            if (v->type == 1 && v->n && !v->data[v->n - 1] && !memchr(v->data, 0, v->n - 1)) put_str(f, (char *)v->data);
            else if (v->type == 4 && v->n == 4) fprintf(f, "dword:%08x", *(uint32_t *)v->data);
            else {
                if (v->type == 3) fprintf(f, "hex:");
                else fprintf(f, "hex(%x):", v->type);
                for (uint32_t j = 0; j < v->n; j++) fprintf(f, "%s%02x", j ? "," : "", v->data[j]);
            }
            fputc('\n', f);
        }
    }
    for (int i = 0; i < k->nkids; i++) save_key(f, k->kids[i]);
}

static void save(void)
{
    if (!s_path[0]) return;
    char tmp[1100];
    snprintf(tmp, sizeof tmp, "%s.tmp", s_path);
    FILE *f = fopen(tmp, "w");
    if (!f) {
        rt_log("cannot write %s", tmp);
        return;
    }
    fprintf(f, "REGEDIT4\n");
    for (int r = 0; r < 4; r++) save_key(f, &s_roots[r]);
    int bad = fflush(f) != 0 || ferror(f) || fsync(fileno(f)) != 0;
    bad |= fclose(f) != 0;
    if (bad || rename(tmp, s_path)) {
        rt_log("cannot write %s; %s left unchanged", tmp, s_path);
        unlink(tmp);
    }
}

static int get_str(const char **p, char *out, size_t n)
{
    const char *s = *p;
    if (*s != '"') return -1;
    s++;
    size_t o = 0;
    while (*s && *s != '"') {
        if (*s == '\\' && s[1]) s++;
        if (o + 1 < n) out[o++] = *s;
        s++;
    }
    if (*s != '"') return -1;
    out[o] = 0;
    *p = s + 1;
    return 0;
}

int rt_reg_load(const char *path)
{
    snprintf(s_path, sizeof s_path, "%s", path);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    char line[8192];
    key *cur = NULL;
    int ln = 0;
    while (fgets(line, sizeof line, f)) {
        ln++;
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (ln == 1) {
            if (strcmp(line, "REGEDIT4")) goto bad;
            continue;
        }
        if (!n || line[0] == ';') continue;
        if (line[0] == '[') {
            char *e = strrchr(line, ']');
            if (!e) goto bad;
            *e = 0;
            char *sl = strchr(line + 1, '\\');
            if (sl) *sl = 0;
            int r = -1;
            for (int i = 0; i < 4; i++)
                if (!rt_stricmp(line + 1, k_root_names[i])) r = i;
            if (r < 0) goto bad;
            cur = sl ? walk(&s_roots[r], sl + 1, 1, NULL) : &s_roots[r];
            continue;
        }
        if (!cur) goto bad;
        const char *p = line;
        char name[1024] = "";
        if (*p == '@') p++;
        else if (get_str(&p, name, sizeof name)) goto bad;
        if (*p++ != '=') goto bad;
        if (*p == '"') {
            char v[8192];
            if (get_str(&p, v, sizeof v)) goto bad;
            set_val(cur, name, 1, v, (uint32_t)strlen(v) + 1);
        } else if (!strncmp(p, "dword:", 6)) {
            uint32_t d = (uint32_t)strtoul(p + 6, NULL, 16);
            set_val(cur, name, 4, &d, 4);
        } else if (!strncmp(p, "hex", 3)) {
            uint32_t type = 3;
            p += 3;
            if (*p == '(') {
                type = (uint32_t)strtoul(p + 1, (char **)&p, 16);
                if (*p++ != ')') goto bad;
            }
            if (*p++ != ':') goto bad;
            unsigned char buf[4096];
            uint32_t k = 0;
            while (*p && k < sizeof buf) {
                char *e;
                buf[k++] = (unsigned char)strtoul(p, &e, 16);
                if (e == p) goto bad;
                p = *e == ',' ? e + 1 : e;
            }
            set_val(cur, name, type, buf, k);
        } else {
            goto bad;
        }
    }
    fclose(f);
    return 0;
bad:
    fclose(f);
    fprintf(stderr, "rt: %s line %d does not parse: %s\n", path, ln, line);
    return -1;
}

static uint32_t open_key(uint32_t h, uint32_t sub, uint32_t *out, int create, uint32_t *disp)
{
    key *k = from_handle(h);
    if (!k) return 6;
    int created = 0;
    key *r = walk(k, sub ? rt_gstr(sub) : "", create, &created);
    if (!r) return 2;
    if (out) MEM32(*out) = new_handle(r);
    if (disp) MEM32(*disp) = created ? 1 : 2;
    if (created) {
        char p[1024];
        key_path(r, p, sizeof p);
        rt_log("reg create %s", p);
        save();
    }
    return 0;
}

static uint32_t RegOpenKeyA(const uint32_t *a)
{
    uint32_t out = a[2];
    return open_key(a[0], a[1], &out, 0, NULL);
}

static uint32_t RegOpenKeyExA(const uint32_t *a)
{
    uint32_t out = a[4];
    return open_key(a[0], a[1], &out, 0, NULL);
}

static uint32_t RegCreateKeyExA(const uint32_t *a)
{
    uint32_t out = a[7], disp = a[8];
    return open_key(a[0], a[1], &out, 1, disp ? &disp : NULL);
}

static uint32_t RegCloseKey(const uint32_t *a)
{
    if (a[0] >= 0x80000000u && a[0] <= 0x80000003u) return 0;
    if (!from_handle(a[0])) return 6;
    s_handles[(a[0] - 0x00050000u) / 4u] = NULL;
    return 0;
}

static void log_query(key *k, const char *name, int hit)
{
    char p[1100], q[1024];
    key_path(k, q, sizeof q);
    snprintf(p, sizeof p, "%s\\%s", q, name && *name ? name : "@");
    for (int i = 0; i < s_nlogged; i++)
        if (!strcmp(s_logged[i], p)) return;
    s_logged = realloc(s_logged, (size_t)(s_nlogged + 1) * sizeof *s_logged);
    s_logged[s_nlogged++] = strdup(p);
    rt_log("reg query %s %s", p, hit ? "hit" : "miss");
}

static uint32_t query(key *k, const char *name, uint32_t type_p, uint32_t data, uint32_t cb_p)
{
    val *v = find_val(k, name);
    log_query(k, name, v != NULL);
    if (!v) return 2;
    if (type_p) MEM32(type_p) = v->type;
    if (!cb_p) return data ? 87 : 0;
    uint32_t cap = MEM32(cb_p);
    MEM32(cb_p) = v->n;
    if (!data) return 0;
    if (cap < v->n) return 234;
    if (!rt_span_ok(data, v->n)) return 998;
    memcpy(rt_host(data), v->data, v->n);
    return 0;
}

static uint32_t RegQueryValueExA(const uint32_t *a)
{
    key *k = from_handle(a[0]);
    if (!k) return 6;
    return query(k, a[1] ? rt_gstr(a[1]) : "", a[3], a[4], a[5]);
}

static uint32_t RegQueryValueA(const uint32_t *a)
{
    key *k = from_handle(a[0]);
    if (!k) return 6;
    if (a[1] && *rt_gstr(a[1])) k = walk(k, rt_gstr(a[1]), 0, NULL);
    if (!k) return 2;
    return query(k, "", 0, a[2], a[3]);
}

static uint32_t RegSetValueExA(const uint32_t *a)
{
    key *k = from_handle(a[0]);
    if (!k) return 6;
    uint32_t type = a[3], n = a[5];
    if (!rt_span_ok(a[4], n)) return 998;
    const void *d = rt_host(a[4]);
    if (type == 1 && (!n || ((const char *)d)[n - 1])) {
        char b[8192];
        snprintf(b, sizeof b, "%.*s", (int)n, (const char *)d);
        set_val(k, a[1] ? rt_gstr(a[1]) : "", type, b, (uint32_t)strlen(b) + 1);
    } else {
        set_val(k, a[1] ? rt_gstr(a[1]) : "", type, d, n);
        if (type == 4 && n == 4 && a[1]) rt_aspect_options_value(rt_gstr(a[1]), *(const uint32_t *)d);
    }
    save();
    return 0;
}

static uint32_t RegEnumKeyExA(const uint32_t *a)
{
    key *k = from_handle(a[0]);
    if (!k) return 6;
    if (a[1] >= (uint32_t)k->nkids) return 259;
    const char *n = k->kids[a[1]]->name;
    uint32_t len = (uint32_t)strlen(n);
    if (MEM32(a[3]) < len + 1) return 234;
    if (!rt_span_ok(a[2], (uint64_t)len + 1)) return 998;
    memcpy(rt_host(a[2]), n, len + 1);
    MEM32(a[3]) = len;
    if (a[5] && a[6]) {
        MEM8(a[5]) = 0;
        MEM32(a[6]) = 0;
    }
    if (a[7]) {
        MEM32(a[7]) = 0;
        MEM32(a[7] + 4) = 0;
    }
    return 0;
}

static uint32_t RegQueryInfoKeyA(const uint32_t *a)
{
    key *k = from_handle(a[0]);
    if (!k) return 6;
    uint32_t maxk = 0, maxn = 0, maxv = 0;
    for (int i = 0; i < k->nkids; i++)
        if (strlen(k->kids[i]->name) > maxk) maxk = (uint32_t)strlen(k->kids[i]->name);
    for (int i = 0; i < k->nvals; i++) {
        if (strlen(k->vals[i].name) > maxn) maxn = (uint32_t)strlen(k->vals[i].name);
        if (k->vals[i].n > maxv) maxv = k->vals[i].n;
    }
    if (a[1] && a[2] && MEM32(a[2])) MEM8(a[1]) = 0;
    if (a[2]) MEM32(a[2]) = 0;
    uint32_t v[8] = {(uint32_t)k->nkids, maxk, 0, (uint32_t)k->nvals, maxn, maxv, 0, 0};
    for (int i = 0; i < 7; i++)
        if (a[4 + i]) MEM32(a[4 + i]) = v[i];
    if (a[11]) {
        MEM32(a[11]) = 0;
        MEM32(a[11] + 4) = 0;
    }
    return 0;
}

const rt_shim rt_advapi32_shims[] = {
    {"advapi32.dll", "RegCloseKey", 1, RegCloseKey},
    {"advapi32.dll", "RegCreateKeyExA", 9, RegCreateKeyExA},
    {"advapi32.dll", "RegEnumKeyExA", 8, RegEnumKeyExA},
    {"advapi32.dll", "RegOpenKeyA", 3, RegOpenKeyA},
    {"advapi32.dll", "RegOpenKeyExA", 5, RegOpenKeyExA},
    {"advapi32.dll", "RegQueryInfoKeyA", 12, RegQueryInfoKeyA},
    {"advapi32.dll", "RegQueryValueA", 4, RegQueryValueA},
    {"advapi32.dll", "RegQueryValueExA", 6, RegQueryValueExA},
    {"advapi32.dll", "RegSetValueExA", 6, RegSetValueExA},
};

const uint32_t rt_nadvapi32_shims = sizeof rt_advapi32_shims / sizeof rt_advapi32_shims[0];

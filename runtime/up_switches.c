#include "rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define R(sec, key, def, sup, cls, prio) {sec, key, def, sup, cls, prio}

typedef struct {
    const char *section;
    const char *key;
    double def;
    int supported;
    const char *cls;
    const char *prio;
} up_row;

static const up_row k_rows[] = {
    R("Loader", "Write Log", 0, 0, "control", "N/A"),
    R("Loader", "Internal Hooks", 1, 0, "control", "N/A"),
    R("Loader", "Check Windows", 1, 0, "control", "N/A"),
    R("Modern OS", "15MB Fix", 1, 0, "code", "inherent"),
    R("Modern OS", "Low Textures Fix", 1, 0, "code", "inherent"),
    R("Modern OS", "DWM Fix", 1, 0, "nothing", "N/A"),
    R("Modern OS", "Console", 1, 0, "code", "P3"),
    R("Bugs", "No Coronas Fix", 1, 1, "hook", "P2"),
    R("Bugs", "Lights Through Walls Fix", 1, 1, "hook", "P2"),
    R("Bugs", "Fix Claws Stats", 1, 1, "hook", "P2"),
    R("Bugs", "Fix Refresh Rate", 1, 0, "code", "inherent"),
    R("Bugs", "Fix Paths", 1, 1, "code", "P1"),
    R("Bugs", "Fix Histories", 1, 1, "hook", "P2"),
    R("Bugs", "Fix Screenshots", 1, 0, "hook", "P3"),
    R("Bugs", "Force Shadows Rebuild", 180, 1, "hook", "P2"),
    R("Bugs", "Window Focus Fix", 1, 0, "nothing", "N/A"),
    R("Bugs", "Developer Mode Fix", 1, 0, "data", "P3"),
    R("Bugs", "Floats For Female Fix", 1, 1, "code", "P2"),
    R("Bugs", "Fix Model LODs", 1, 1, "code", "P2"),
    R("Bugs", "Fix Python Exploit", 1, 0, "data", "inherent"),
    R("Bugs", "Fix Texture Memory", 1, 0, "code", "inherent"),
    R("Mods", "Create Dirs", 1, 1, "data", "P1"),
    R("Mods", "Fix Movies", 1, 0, "nothing", "N/A"),
    R("Mods", "Fix sys.path", 1, 1, "export hook", "P1"),
    R("Mods", "Delete Paths", 1, 1, "inside hook", "P1"),
    R("Mods", "Fix Cfg-Saves", 1, 1, "iat", "P1"),
    R("Mods", "Check Restart", 0, 0, "hook", "P3"),
    R("WideScreen", "Auto Bink AR", 1, 0, "hook", "M9"),
    R("WideScreen", "Auto AR", 1, 0, "code", "M9"),
    R("WideScreen", "FOV Hor+", 1, 0, "hook+data", "M9"),
    R("WideScreen", "Correct Char Model", 1, 0, "hook", "M9"),
    R("WideScreen", "Correct HUD", 1, 0, "pointer", "M9"),
    R("WideScreen", "Correct Dialogs", 1, 0, "nothing", "M9"),
    R("WideScreen", "Default_FOV", 0, 0, "pointer", "M9"),
    R("WideScreen", "Viewmodel_FOV", 0, 0, "pointer", "M9"),
    R("WideScreen", "Camera_FOV", 0, 0, "nothing", "M9"),
    R("WideScreen", "Fix Menu Fonts", 1, 0, "data", "M9"),
    R("WideScreen", "Fix In-Game Fonts", 1, 0, "data", "M9"),
    R("WideScreen", "Separate Textures", 1, 0, "nothing", "M9"),
    R("WideScreen", "Add HiRes Fonts", 1, 0, "data", "M9"),
    R("UI Fixes", "Skip Intro", 1, 0, "code", "M9"),
    R("UI Fixes", "Hotkeys Window", 1, 0, "code", "M9"),
    R("UI Fixes", "Titles Dividers", 1, 0, "code+data", "M9"),
    R("UI Fixes", "Loading Bar", 1, 0, "nothing", "M9"),
    R("UI Fixes", "Blood HP Bars", 1, 0, "data+pointer", "M9"),
    R("UI Fixes", "Terminal Emails", 1, 0, "data", "M9"),
    R("UI Fixes", "Context Icons", 1, 0, "data+pointer", "M9"),
    R("UI Fixes", "Inventory Texts", 1, 0, "code", "M9"),
    R("UI Fixes", "Buttons Shadows", 1, 0, "code+data", "M9"),
    R("Tweaks", "Enable Console", 1, 0, "code", "P3"),
    R("Tweaks", "Improve Save Speed", 1, 1, "code", "P2"),
    R("Tweaks", "Stop Trash Dumps", 1, 0, "data", "P3"),
    R("Tweaks", "Lists Height Scale", 1.0, 0, "nothing", "P3"),
    R("Tweaks", "Dialog Box Width", 832, 0, "nothing", "P3"),
    R("Tweaks", "Correct Bubbles", 1, 0, "nothing", "P3"),
    R("Tweaks", "Desktop Resolution", 1, 0, "registry", "M9"),
    R("Tweaks", "Default Clan Fix", 1, 1, "file", "P2"),
    R("Tweaks", "Loading Delay Sec", 0, 1, "inside hook", "P3"),
    R("Tweaks", "Exit by Alt+F4", 1, 0, "hook", "P3"),
    R("Tweaks", "FOV Reset Fix", 1, 1, "hook", "P2"),
    R("Tweaks", "Non-Solid Ragdolls", 1, 1, "code", "P2"),
    R("Tweaks", "No Bullet Tracers", 0, 0, "code", "P3"),
    R("Localization", "All Chars Support", 1, 0, "code+hook", "N/A"),
    R("Localization", "Fix Terminals Quit", 1, 0, "nothing", "N/A"),
    R("Localization", "Fix Clan Name Links", 1, 0, "hook", "N/A"),
    R("Localization", "Fix Key Bindings", 1, 0, "hook", "N/A"),
    R("Localization", "Adjust Name Width", 1, 0, "pointer", "N/A"),
    R("Localization", "Fix Default Name", 1, 0, "nothing", "N/A"),
    R("Localization", "Localization File", 1, 0, "nothing", "N/A"),
};

#define NROWS (sizeof k_rows / sizeof k_rows[0])

static double s_val[NROWS];

static int find_row(const char *section, const char *key)
{
    for (size_t i = 0; i < NROWS; i++)
        if (!strcasecmp(k_rows[i].section, section) && !strcasecmp(k_rows[i].key, key)) return (int)i;
    return -1;
}

static char *trim(char *s)
{
    while (*s == ' ' || *s == '\t') s++;
    size_t n = strlen(s);
    while (n && (s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\r' || s[n - 1] == '\n')) s[--n] = 0;
    return s;
}

uint32_t rt_up_switch_count(void) { return (uint32_t)NROWS; }

int rt_up_switch_row(uint32_t i, const char **section, const char **key, int *supported)
{
    if (i >= NROWS) return -1;
    *section = k_rows[i].section;
    *key = k_rows[i].key;
    *supported = k_rows[i].supported;
    return 0;
}

void rt_up_switches_reset(void)
{
    for (size_t i = 0; i < NROWS; i++) s_val[i] = k_rows[i].def;
}

typedef struct {
    const char *plugin;
    const char *key;
    double def;
} plug_row;

static const plug_row k_plug[] = {
    {"console-pause-fix", "Enabled", 1},
    {"container-stack-fix", "Enabled", 1},
    {"griffith-frenzy-werewolf-fix", "Enabled", 1},
    {"history-stat-reset-fix", "Enabled", 1},
    {"model-loader-fix", "Enabled", 1},
    {"model-loader-fix", "Enable Model Loader Fix", 1},
    {"protean-improved", "Enabled", 1},
    {"protean-improved", "Warform Frenzy", 1},
    {"protean-improved", "Filter Load Fix", 1},
    {"subtitle-pause-fix", "Enabled", 1},
    {"vtmb-tweaks", "Enabled", 1},
    {"vtmb-tweaks", "bNoCorpseDespawn", 1},
    {"vtmb-tweaks", "bNoTracers", 1},
    {"vtmb-tweaks", "bNPCsDropAllWeapons", 1},
    {"vtmb-tweaks", "bNonSolidRagdolls", 1},
    {"vtmb-tweaks", "bFixDoorsAtHighFPS", 1},
    {"vtmb-tweaks", "bNoDoorAutoClose", 0},
    {"vtmb-tweaks", "bStackableWeapons", 1},
    {"vtmb-tweaks", "bEnableAltF4", 1},
};

#define NPLUG (sizeof k_plug / sizeof k_plug[0])

const char *rt_up_plugin_ini(const char *plugin) { return strcasecmp(plugin, "model-loader-fix") ? plugin : "modelloader_fix"; }

static double s_pval[NPLUG];

static int find_plug(const char *plugin, const char *key)
{
    for (size_t i = 0; i < NPLUG; i++)
        if (!strcasecmp(k_plug[i].plugin, plugin) && !strcasecmp(k_plug[i].key, key)) return (int)i;
    return -1;
}

int rt_up_plugin_known(const char *plugin)
{
    for (size_t i = 0; i < NPLUG; i++)
        if (!strcasecmp(k_plug[i].plugin, plugin)) return 1;
    return 0;
}

void rt_up_plugins_reset(void)
{
    for (size_t i = 0; i < NPLUG; i++) s_pval[i] = k_plug[i].def;
}

void rt_up_plugin_set(const char *plugin, const char *key, double v)
{
    int i = find_plug(plugin, key);
    if (i >= 0) s_pval[i] = v;
}

double rt_up_plugin_num(const char *plugin, const char *key)
{
    int i = find_plug(plugin, key);
    return i < 0 ? 0 : s_pval[i];
}

int rt_up_plugin_on(const char *plugin, const char *key) { return rt_up_plugin_num(plugin, key) != 0; }

uint32_t rt_up_plugin_count(void) { return (uint32_t)NPLUG; }

int rt_up_plugin_row(uint32_t i, const char **plugin, const char **key)
{
    if (i >= NPLUG) return -1;
    *plugin = k_plug[i].plugin;
    *key = k_plug[i].key;
    return 0;
}

static int ini_read(const char *path, const char *plugin)
{
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    char line[1024], section[128] = "";
    int set = 0;
    while (fgets(line, sizeof line, f)) {
        char *semi = strchr(line, ';');
        if (semi) *semi = 0;
        char *p = trim(line);
        if (*p == '[') {
            char *e = strchr(p, ']');
            if (e) {
                *e = 0;
                snprintf(section, sizeof section, "%s", trim(p + 1));
            }
            continue;
        }
        char *eq = strchr(p, '=');
        if (!eq) continue;
        *eq = 0;
        char *key = trim(p), *val = trim(eq + 1);
        char *end;
        double v = strtod(val, &end);
        if (end == val || *trim(end)) continue;
        int i = plugin ? find_plug(plugin, key) : find_row(section, key);
        if (i < 0) continue;
        if (plugin) s_pval[i] = v;
        else s_val[i] = v;
        set++;
    }
    fclose(f);
    return set;
}

int rt_up_switches_read(const char *path) { return ini_read(path, NULL); }

int rt_up_plugin_read(const char *path, const char *plugin) { return ini_read(path, plugin); }

double rt_up_switch_num(const char *section, const char *key)
{
    int i = find_row(section, key);
    return i < 0 ? 0 : s_val[i];
}

int rt_up_switch_on(const char *section, const char *key) { return rt_up_switch_num(section, key) != 0; }

void rt_up_switches_log(void)
{
    for (size_t i = 0; i < NROWS; i++)
        rt_log("up fix %s/%s %s", k_rows[i].section, k_rows[i].key,
               !k_rows[i].supported ? "not supported" : s_val[i] != 0 ? "on" : "off");
}

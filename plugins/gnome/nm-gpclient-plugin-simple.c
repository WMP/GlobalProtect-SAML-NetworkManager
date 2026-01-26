/* Minimal NetworkManager gpclient VPN plugin */

#define GETTEXT_PACKAGE "NetworkManager-gpclient"

#include <glib.h>
#include <glib/gi18n-lib.h>
#include <gmodule.h>
#include <NetworkManager.h>
#include "nm-vpn-plugin-utils.h"

typedef struct {
    GObject parent;
} GpclientPlugin;

typedef struct {
    GObjectClass parent;
} GpclientPluginClass;

static void gpclient_plugin_interface_init (NMVpnEditorPluginInterface *iface);

G_DEFINE_TYPE_EXTENDED (GpclientPlugin, gpclient_plugin, G_TYPE_OBJECT, 0,
                        G_IMPLEMENT_INTERFACE (NM_TYPE_VPN_EDITOR_PLUGIN,
                                               gpclient_plugin_interface_init))

enum {
    PROP_0,
    PROP_NAME,
    PROP_DESC,
    PROP_SERVICE,
};

static void
gpclient_plugin_init (GpclientPlugin *plugin)
{
}

static void
gpclient_plugin_get_property (GObject *object, guint prop_id,
                               GValue *value, GParamSpec *pspec)
{
    switch (prop_id) {
    case PROP_NAME:
        g_value_set_string (value, "GlobalProtect");
        break;
    case PROP_DESC:
        g_value_set_string (value, "GlobalProtect VPN Client");
        break;
    case PROP_SERVICE:
        g_value_set_string (value, "org.freedesktop.NetworkManager.gpclient");
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
        break;
    }
}

static void
gpclient_plugin_class_init (GpclientPluginClass *klass)
{
    GObjectClass *object_class = G_OBJECT_CLASS (klass);
    object_class->get_property = gpclient_plugin_get_property;
    
    g_object_class_install_property (object_class, PROP_NAME,
        g_param_spec_string (NM_VPN_EDITOR_PLUGIN_NAME, "", "",
                             "GlobalProtect",
                             G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
    
    g_object_class_install_property (object_class, PROP_DESC,
        g_param_spec_string (NM_VPN_EDITOR_PLUGIN_DESCRIPTION, "", "",
                             "GlobalProtect VPN Client",
                             G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
    
    g_object_class_install_property (object_class, PROP_SERVICE,
        g_param_spec_string (NM_VPN_EDITOR_PLUGIN_SERVICE, "", "",
                             "org.freedesktop.NetworkManager.gpclient",
                             G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
}

static NMVpnEditor *
_call_editor_factory (gpointer factory,
                      NMVpnEditorPlugin *editor_plugin,
                      NMConnection *connection,
                      gpointer user_data,
                      GError **error)
{
    typedef NMVpnEditor * (*FactoryFunc) (NMVpnEditorPlugin *, NMConnection *, GError **);
    return ((FactoryFunc) factory) (editor_plugin, connection, error);
}

static NMVpnEditor *
get_editor (NMVpnEditorPlugin *plugin, NMConnection *connection, GError **error)
{
    gpointer gtk3_only_symbol;
    GModule *self_module;
    const char *editor;

    g_return_val_if_fail (NM_IS_CONNECTION (connection), NULL);
    g_return_val_if_fail (!error || !*error, NULL);

    self_module = g_module_open (NULL, 0);
    g_module_symbol (self_module, "gtk_container_add", &gtk3_only_symbol);
    g_module_close (self_module);

    if (gtk3_only_symbol) {
        editor = "libnm-vpn-plugin-gpclient-editor.so";
    } else {
        editor = "libnm-gtk4-vpn-plugin-gpclient-editor.so";
    }

    return nm_vpn_plugin_utils_load_editor (editor,
                                            "nm_vpn_editor_factory_gpclient",
                                            _call_editor_factory,
                                            plugin,
                                            connection,
                                            NULL,
                                            error);
}

static guint32
get_capabilities (NMVpnEditorPlugin *plugin)
{
    return 0;
}

static void
gpclient_plugin_interface_init (NMVpnEditorPluginInterface *iface)
{
    iface->get_editor = get_editor;
    iface->get_capabilities = get_capabilities;
}

G_MODULE_EXPORT NMVpnEditorPlugin *
nm_vpn_editor_plugin_factory (GError **error)
{
    return g_object_new (gpclient_plugin_get_type (), NULL);
}

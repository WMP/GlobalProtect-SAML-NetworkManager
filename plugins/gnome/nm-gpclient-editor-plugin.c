/* NetworkManager gpclient VPN editor plugin - GTK4 factory */

#include <glib-object.h>
#include <NetworkManager.h>
#include <nm-vpn-editor-plugin.h>

#include "nm-gpclient-editor-plugin.h"
#include "nm-gpclient-editor.h"

struct _NMGpclientEditorPlugin {
    GObject parent;
};

static void nm_gpclient_editor_plugin_interface_init (NMVpnEditorPluginInterface *iface);

G_DEFINE_TYPE_WITH_CODE (NMGpclientEditorPlugin, nm_gpclient_editor_plugin, G_TYPE_OBJECT,
                         G_IMPLEMENT_INTERFACE (NM_TYPE_VPN_EDITOR_PLUGIN,
                                                nm_gpclient_editor_plugin_interface_init))

enum {
    PROP_0,
    PROP_NAME,
    PROP_DESC,
    PROP_SERVICE,
};

static NMVpnEditor *
get_editor (NMVpnEditorPlugin *plugin,
            NMConnection       *connection,
            GError            **error)
{
    return NM_VPN_EDITOR (nm_gpclient_editor_new (connection));
}

static void
get_property (GObject    *object,
              guint       prop_id,
              GValue     *value,
              GParamSpec *pspec)
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
nm_gpclient_editor_plugin_interface_init (NMVpnEditorPluginInterface *iface)
{
    iface->get_editor = get_editor;
}

static void
nm_gpclient_editor_plugin_class_init (NMGpclientEditorPluginClass *klass)
{
    GObjectClass *object_class = G_OBJECT_CLASS (klass);

    object_class->get_property = get_property;

    g_object_class_override_property (object_class, PROP_NAME, NM_VPN_EDITOR_PLUGIN_NAME);
    g_object_class_override_property (object_class, PROP_DESC, NM_VPN_EDITOR_PLUGIN_DESCRIPTION);
    g_object_class_override_property (object_class, PROP_SERVICE, NM_VPN_EDITOR_PLUGIN_SERVICE);
}

static void
nm_gpclient_editor_plugin_init (NMGpclientEditorPlugin *plugin)
{
}

/* CRITICAL: This is the symbol GNOME Settings looks for */
G_MODULE_EXPORT NMVpnEditorPlugin *
nm_vpn_editor_plugin_factory (GError **error)
{
    return g_object_new (NM_TYPE_GPCLIENT_EDITOR_PLUGIN, NULL);
}

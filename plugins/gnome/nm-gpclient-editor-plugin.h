/* NetworkManager gpclient VPN editor plugin header */

#ifndef __NM_GPCLIENT_EDITOR_PLUGIN_H__
#define __NM_GPCLIENT_EDITOR_PLUGIN_H__

#include <glib-object.h>
#include <NetworkManager.h>
#include <nm-vpn-editor-plugin.h>

#define NM_TYPE_GPCLIENT_EDITOR_PLUGIN            (nm_gpclient_editor_plugin_get_type ())
#define NM_GPCLIENT_EDITOR_PLUGIN(obj)            (G_TYPE_CHECK_INSTANCE_CAST ((obj), NM_TYPE_GPCLIENT_EDITOR_PLUGIN, NMGpclientEditorPlugin))
#define NM_GPCLIENT_EDITOR_PLUGIN_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST ((klass), NM_TYPE_GPCLIENT_EDITOR_PLUGIN, NMGpclientEditorPluginClass))
#define NM_IS_GPCLIENT_EDITOR_PLUGIN(obj)         (G_TYPE_CHECK_INSTANCE_TYPE ((obj), NM_TYPE_GPCLIENT_EDITOR_PLUGIN))
#define NM_IS_GPCLIENT_EDITOR_PLUGIN_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE ((klass), NM_TYPE_GPCLIENT_EDITOR_PLUGIN))
#define NM_GPCLIENT_EDITOR_PLUGIN_GET_CLASS(obj)  (G_TYPE_INSTANCE_GET_CLASS ((obj), NM_TYPE_GPCLIENT_EDITOR_PLUGIN, NMGpclientEditorPluginClass))

typedef struct _NMGpclientEditorPlugin NMGpclientEditorPlugin;
typedef struct _NMGpclientEditorPluginClass NMGpclientEditorPluginClass;

struct _NMGpclientEditorPluginClass {
    GObjectClass parent;
};

GType nm_gpclient_editor_plugin_get_type (void);

#endif /* __NM_GPCLIENT_EDITOR_PLUGIN_H__ */

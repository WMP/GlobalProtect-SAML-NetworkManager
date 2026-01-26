/* NetworkManager gpclient VPN editor header - GTK4 */

#ifndef __NM_GPCLIENT_EDITOR_H__
#define __NM_GPCLIENT_EDITOR_H__

#include <glib-object.h>
#include <NetworkManager.h>
#include <nm-vpn-editor-plugin.h>

#define NM_TYPE_GPCLIENT_EDITOR            (nm_gpclient_editor_get_type ())
#define NM_GPCLIENT_EDITOR(obj)            (G_TYPE_CHECK_INSTANCE_CAST ((obj), NM_TYPE_GPCLIENT_EDITOR, NMGpclientEditor))
#define NM_GPCLIENT_EDITOR_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST ((klass), NM_TYPE_GPCLIENT_EDITOR, NMGpclientEditorClass))
#define NM_IS_GPCLIENT_EDITOR(obj)         (G_TYPE_CHECK_INSTANCE_TYPE ((obj), NM_TYPE_GPCLIENT_EDITOR))
#define NM_IS_GPCLIENT_EDITOR_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE ((klass), NM_TYPE_GPCLIENT_EDITOR))
#define NM_GPCLIENT_EDITOR_GET_CLASS(obj)  (G_TYPE_INSTANCE_GET_CLASS ((obj), NM_TYPE_GPCLIENT_EDITOR, NMGpclientEditorClass))

typedef struct _NMGpclientEditor NMGpclientEditor;
typedef struct _NMGpclientEditorClass NMGpclientEditorClass;

struct _NMGpclientEditorClass {
    GObjectClass parent;
};

GType nm_gpclient_editor_get_type (void);

NMGpclientEditor *nm_gpclient_editor_new (NMConnection *connection);

#endif /* __NM_GPCLIENT_EDITOR_H__ */

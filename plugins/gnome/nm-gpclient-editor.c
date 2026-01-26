/* NetworkManager gpclient VPN connection editor - GTK4 for GNOME Settings */

#include <gtk/gtk.h>
#include <NetworkManager.h>
#include <nm-vpn-editor-plugin.h>
#include "nm-gpclient-editor.h"

struct _NMGpclientEditor {
    GObject parent;
    NMConnection *connection;
    GtkWidget *widget;
    GtkWidget *gateway_entry;
    GtkWidget *browser_combo;
    GtkWidget *dns_entry;
};

static void nm_gpclient_editor_interface_init (NMVpnEditorInterface *iface);

G_DEFINE_TYPE_WITH_CODE (NMGpclientEditor, nm_gpclient_editor, G_TYPE_OBJECT,
                         G_IMPLEMENT_INTERFACE (NM_TYPE_VPN_EDITOR,
                                                nm_gpclient_editor_interface_init))

static void
entry_changed_cb (GtkEditable *editable, gpointer user_data)
{
    g_signal_emit_by_name (user_data, "changed");
}

static void
browser_combo_changed_cb (GtkComboBox *combo, gpointer user_data)
{
    const gchar *text = gtk_combo_box_text_get_active_text (GTK_COMBO_BOX_TEXT (combo));

    if (text && g_strcmp0(text, "Custom...") == 0) {
        // User selected "Custom...", clear the entry so they can type
        // In GTK4, combo box with entry has the entry as child widget
        GtkWidget *entry = gtk_combo_box_get_child (GTK_COMBO_BOX (combo));
        if (entry && GTK_IS_EDITABLE (entry)) {
            gtk_editable_set_text (GTK_EDITABLE (entry), "");
            gtk_widget_grab_focus (entry);
        }
    }

    g_free ((gchar *)text);
    g_signal_emit_by_name (user_data, "changed");
}

static GtkWidget *
build_ui (NMGpclientEditor *self)
{
    GtkWidget *grid, *label;
    NMSettingVpn *s_vpn;
    const char *value;
    int row = 0;

    grid = gtk_grid_new ();
    gtk_grid_set_column_spacing (GTK_GRID (grid), 12);
    gtk_grid_set_row_spacing (GTK_GRID (grid), 6);
    gtk_widget_set_margin_top (grid, 12);
    gtk_widget_set_margin_bottom (grid, 12);
    gtk_widget_set_margin_start (grid, 12);
    gtk_widget_set_margin_end (grid, 12);

    s_vpn = nm_connection_get_setting_vpn (self->connection);

    /* Gateway field */
    label = gtk_label_new ("Gateway:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->gateway_entry = gtk_entry_new ();
    gtk_widget_set_hexpand (self->gateway_entry, TRUE);
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "gateway");
        if (value && *value)
            gtk_editable_set_text (GTK_EDITABLE (self->gateway_entry), value);
    }
    g_signal_connect (self->gateway_entry, "changed", G_CALLBACK (entry_changed_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->gateway_entry, 1, row++, 1, 1);

    /* Browser field */
    label = gtk_label_new ("Browser:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->browser_combo = gtk_combo_box_text_new_with_entry ();
    gtk_widget_set_hexpand (self->browser_combo, TRUE);
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->browser_combo), "/usr/libexec/gpclient/edge-wrapper");
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->browser_combo), "/usr/bin/firefox");
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->browser_combo), "/usr/bin/chromium");
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->browser_combo), "Custom...");
    gtk_widget_set_tooltip_text (self->browser_combo, "Browser for 2FA/SAML authentication. Select 'Custom...' to specify your own executable.");
    
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "browser");
        if (value && *value) {
            // Check if value matches one of predefined options
            gboolean found = FALSE;
            const char *predefined[] = {"/usr/libexec/gpclient/edge-wrapper", "/usr/bin/firefox", "/usr/bin/chromium", NULL};
            for (int i = 0; predefined[i] != NULL; i++) {
                if (g_strcmp0(value, predefined[i]) == 0) {
                    gtk_combo_box_set_active (GTK_COMBO_BOX (self->browser_combo), i);
                    found = TRUE;
                    break;
                }
            }
            if (!found) {
                // Custom value - set entry text directly
                GtkWidget *entry = gtk_combo_box_get_child (GTK_COMBO_BOX (self->browser_combo));
                if (entry && GTK_IS_EDITABLE (entry))
                    gtk_editable_set_text (GTK_EDITABLE (entry), value);
            }
        } else {
            gtk_combo_box_set_active (GTK_COMBO_BOX (self->browser_combo), 0);
        }
    } else {
        gtk_combo_box_set_active (GTK_COMBO_BOX (self->browser_combo), 0);
    }
    g_signal_connect (self->browser_combo, "changed", G_CALLBACK (browser_combo_changed_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->browser_combo, 1, row++, 1, 1);

    /* DNS Servers field */
    label = gtk_label_new ("DNS Servers:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->dns_entry = gtk_entry_new ();
    gtk_widget_set_hexpand (self->dns_entry, TRUE);
    gtk_widget_set_tooltip_text (self->dns_entry, 
        "Recommended: Leave empty to use DNS servers from VPN with automatic split DNS configuration.\n"
        "Split DNS allows resolving VPN-specific domains through VPN DNS while using local DNS for other domains.\n"
        "Override: Enter semicolon-separated DNS servers (e.g., 8.8.8.8;8.8.4.4) to use custom DNS instead.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "dns");
        if (value && *value)
            gtk_editable_set_text (GTK_EDITABLE (self->dns_entry), value);
    }
    g_signal_connect (self->dns_entry, "changed", G_CALLBACK (entry_changed_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->dns_entry, 1, row++, 1, 1);

    return grid;
}

static GObject *
get_widget (NMVpnEditor *editor)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (editor);

    if (!self->widget) {
        self->widget = build_ui (self);
        g_object_ref_sink (self->widget);
    }

    return G_OBJECT (self->widget);
}

static gboolean
update_connection (NMVpnEditor *editor,
                   NMConnection *connection,
                   GError **error)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (editor);
    NMSettingVpn *s_vpn;
    const char *str;

    s_vpn = nm_connection_get_setting_vpn (connection);
    if (!s_vpn) {
        s_vpn = (NMSettingVpn *) nm_setting_vpn_new ();
        nm_connection_add_setting (connection, NM_SETTING (s_vpn));
    }

    g_object_set (s_vpn, NM_SETTING_VPN_SERVICE_TYPE,
                  "org.freedesktop.NetworkManager.gpclient", NULL);

    /* Save Gateway */
    str = gtk_editable_get_text (GTK_EDITABLE (self->gateway_entry));
    if (str && *str)
        nm_setting_vpn_add_data_item (s_vpn, "gateway", str);
    else
        nm_setting_vpn_remove_data_item (s_vpn, "gateway");

    /* Save Browser */
    str = gtk_combo_box_text_get_active_text (GTK_COMBO_BOX_TEXT (self->browser_combo));
    if (str && *str)
        nm_setting_vpn_add_data_item (s_vpn, "browser", str);
    else
        nm_setting_vpn_remove_data_item (s_vpn, "browser");
    g_free ((char *)str);

    /* Save DNS */
    str = gtk_editable_get_text (GTK_EDITABLE (self->dns_entry));
    if (str && *str)
        nm_setting_vpn_add_data_item (s_vpn, "dns", str);
    else
        nm_setting_vpn_remove_data_item (s_vpn, "dns");

    return TRUE;
}

static void
nm_gpclient_editor_interface_init (NMVpnEditorInterface *iface)
{
    iface->get_widget = get_widget;
    iface->update_connection = update_connection;
}

static void
nm_gpclient_editor_init (NMGpclientEditor *self)
{
    self->widget = NULL;
    self->connection = NULL;
}

static void
nm_gpclient_editor_dispose (GObject *object)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (object);

    if (self->widget) {
        g_object_unref (self->widget);
        self->widget = NULL;
    }

    if (self->connection) {
        g_object_unref (self->connection);
        self->connection = NULL;
    }

    G_OBJECT_CLASS (nm_gpclient_editor_parent_class)->dispose (object);
}

static void
nm_gpclient_editor_class_init (NMGpclientEditorClass *klass)
{
    GObjectClass *object_class = G_OBJECT_CLASS (klass);
    object_class->dispose = nm_gpclient_editor_dispose;
}

NMGpclientEditor *
nm_gpclient_editor_new (NMConnection *connection)
{
    NMGpclientEditor *self;

    g_return_val_if_fail (NM_IS_CONNECTION (connection), NULL);

    self = g_object_new (NM_TYPE_GPCLIENT_EDITOR, NULL);
    self->connection = g_object_ref (connection);

    return self;
}

/* Factory function called by nm_vpn_plugin_utils_load_editor() */
G_MODULE_EXPORT NMVpnEditor *
nm_vpn_editor_factory_gpclient (NMVpnEditorPlugin *editor_plugin,
                                NMConnection *connection,
                                GError **error)
{
    g_return_val_if_fail (!error || !*error, NULL);
    return NM_VPN_EDITOR (nm_gpclient_editor_new (connection));
}

/* NetworkManager gpclient VPN connection editor - GTK3 version */

#include <stdio.h>
#include <string.h>
#include <gtk/gtk.h>
#include <NetworkManager.h>
#include <nm-vpn-editor-plugin.h>

#include "nm-gpclient-editor.h"

/************** UI widget class **************/

struct _NMGpclientEditor {
    GObject parent;
    NMConnection *connection;
    GtkWidget *widget;
    GtkEntry *gateway_entry;
    GtkComboBoxText *browser_combo;
    GtkEntry *dns_entry;
};

static void nm_gpclient_editor_interface_init (NMVpnEditorInterface *iface_class);

G_DEFINE_TYPE_WITH_CODE (NMGpclientEditor, nm_gpclient_editor, G_TYPE_OBJECT,
                         G_IMPLEMENT_INTERFACE (NM_TYPE_VPN_EDITOR,
                                                nm_gpclient_editor_interface_init))

static void
stuff_changed_cb (GtkWidget *widget, gpointer user_data)
{
    g_signal_emit_by_name (G_OBJECT (user_data), "changed");
}

static void
browser_combo_changed_cb (GtkComboBox *combo, gpointer user_data)
{
    const gchar *text = gtk_combo_box_text_get_active_text (GTK_COMBO_BOX_TEXT (combo));

    if (text && g_strcmp0(text, "Custom...") == 0) {
        GtkWidget *entry = gtk_bin_get_child (GTK_BIN (combo));
        gtk_entry_set_text (GTK_ENTRY (entry), "");
        gtk_widget_grab_focus (entry);
    }

    g_free ((gchar *)text);
    g_signal_emit_by_name (G_OBJECT (user_data), "changed");
}

static GtkWidget *
build_ui (NMGpclientEditor *self)
{
    NMSettingVpn *s_vpn;
    GtkWidget *widget, *label;
    const char *value;
    int row = 0;

    s_vpn = nm_connection_get_setting_vpn (self->connection);

    widget = gtk_grid_new ();
    gtk_grid_set_column_spacing (GTK_GRID (widget), 12);
    gtk_grid_set_row_spacing (GTK_GRID (widget), 6);

    /* Gateway */
    label = gtk_label_new ("Gateway:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->gateway_entry = GTK_ENTRY (gtk_entry_new ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->gateway_entry), TRUE);
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "gateway");
        if (value)
            gtk_entry_set_text (self->gateway_entry, value);
    }
    g_signal_connect (G_OBJECT (self->gateway_entry), "changed", G_CALLBACK (stuff_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->gateway_entry), 1, row++, 1, 1);

    /* Browser */
    label = gtk_label_new ("Browser:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->browser_combo = GTK_COMBO_BOX_TEXT (gtk_combo_box_text_new_with_entry ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->browser_combo), TRUE);

    const char *browsers[] = {
        "/usr/libexec/gpclient/edge-wrapper",
        "/usr/bin/firefox",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        NULL
    };

    for (int i = 0; browsers[i] != NULL; i++) {
        if (g_file_test(browsers[i], G_FILE_TEST_EXISTS)) {
            gtk_combo_box_text_append_text (self->browser_combo, browsers[i]);
        }
    }

    gtk_combo_box_text_append_text (self->browser_combo, "Custom...");
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->browser_combo),
        "Browser for 2FA/SAML authentication. Select 'Custom...' to specify your own executable.");

    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "browser");
        if (value && *value) {
            GtkTreeModel *model = gtk_combo_box_get_model (GTK_COMBO_BOX (self->browser_combo));
            GtkTreeIter iter;
            gboolean found = FALSE;

            if (gtk_tree_model_get_iter_first (model, &iter)) {
                int index = 0;
                do {
                    gchar *item_text;
                    gtk_tree_model_get (model, &iter, 0, &item_text, -1);
                    if (g_strcmp0 (value, item_text) == 0) {
                        gtk_combo_box_set_active (GTK_COMBO_BOX (self->browser_combo), index);
                        found = TRUE;
                        g_free (item_text);
                        break;
                    }
                    g_free (item_text);
                    index++;
                } while (gtk_tree_model_iter_next (model, &iter));
            }

            if (!found) {
                GtkWidget *entry = gtk_bin_get_child (GTK_BIN (self->browser_combo));
                gtk_entry_set_text (GTK_ENTRY (entry), value);
            }
        } else {
            gtk_combo_box_set_active (GTK_COMBO_BOX (self->browser_combo), 0);
        }
    } else {
        gtk_combo_box_set_active (GTK_COMBO_BOX (self->browser_combo), 0);
    }
    g_signal_connect (G_OBJECT (self->browser_combo), "changed", G_CALLBACK (browser_combo_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->browser_combo), 1, row++, 1, 1);

    /* DNS */
    label = gtk_label_new ("DNS Servers:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->dns_entry = GTK_ENTRY (gtk_entry_new ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->dns_entry), TRUE);
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->dns_entry),
        "Recommended: Leave empty to use DNS servers from VPN with automatic split DNS configuration.\n"
        "Split DNS allows resolving VPN-specific domains through VPN DNS while using local DNS for other domains.\n"
        "Override: Enter semicolon-separated DNS servers (e.g., 8.8.8.8;8.8.4.4) to use custom DNS instead.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "dns");
        if (value)
            gtk_entry_set_text (self->dns_entry, value);
    }
    g_signal_connect (G_OBJECT (self->dns_entry), "changed", G_CALLBACK (stuff_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->dns_entry), 1, row++, 1, 1);

    gtk_widget_show_all (widget);

    return widget;
}

static gboolean
update_connection (NMVpnEditor *iface,
                  NMConnection *connection,
                  GError **error)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (iface);
    NMSettingVpn *s_vpn;
    const char *str;

    /* Check gateway is set */
    if (self->gateway_entry) {
        str = gtk_entry_get_text (self->gateway_entry);
        if (!str || !strlen (str)) {
            if (error) {
                g_set_error (error,
                             NM_VPN_PLUGIN_ERROR,
                             NM_VPN_PLUGIN_ERROR_BAD_ARGUMENTS,
                             "gateway is required");
            }
            return FALSE;
        }
    }

    s_vpn = nm_connection_get_setting_vpn (connection);
    if (!s_vpn) {
        s_vpn = (NMSettingVpn *) nm_setting_vpn_new ();
        nm_connection_add_setting (connection, NM_SETTING (s_vpn));
    }

    g_object_set (s_vpn, NM_SETTING_VPN_SERVICE_TYPE,
                  "org.freedesktop.NetworkManager.gpclient", NULL);

    if (self->gateway_entry) {
        str = gtk_entry_get_text (self->gateway_entry);
        if (str && strlen (str))
            nm_setting_vpn_add_data_item (s_vpn, "gateway", str);
    }

    if (self->browser_combo) {
        str = gtk_combo_box_text_get_active_text (self->browser_combo);
        if (str && strlen (str) && g_strcmp0(str, "Custom...") != 0)
            nm_setting_vpn_add_data_item (s_vpn, "browser", str);
        g_free ((char *)str);
    }

    if (self->dns_entry) {
        str = gtk_entry_get_text (self->dns_entry);
        if (str && strlen (str))
            nm_setting_vpn_add_data_item (s_vpn, "dns", str);
    }

    return TRUE;
}

static GObject *
get_widget (NMVpnEditor *iface)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (iface);

    if (!self->widget) {
        self->widget = build_ui (self);
        g_object_ref_sink (self->widget);
    }

    return G_OBJECT (self->widget);
}

static void
dispose (GObject *object)
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
nm_gpclient_editor_class_init (NMGpclientEditorClass *req_class)
{
    GObjectClass *object_class = G_OBJECT_CLASS (req_class);

    object_class->dispose = dispose;
}

static void
nm_gpclient_editor_interface_init (NMVpnEditorInterface *iface_class)
{
    iface_class->get_widget = get_widget;
    iface_class->update_connection = update_connection;
}

static void
nm_gpclient_editor_init (NMGpclientEditor *self)
{
    self->widget = NULL;
    self->connection = NULL;
    self->gateway_entry = NULL;
    self->browser_combo = NULL;
    self->dns_entry = NULL;
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

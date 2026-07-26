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
    GtkWidget *as_gateway_check;
    GtkWidget *preferred_gateway_combo;
    GtkWidget *auth_mode_combo;
    GtkWidget *username_entry;
    GtkWidget *browser_combo;
    GtkWidget *dns_entry;
    GtkWidget *hip_check;
};

/* First entry of the preferred-gateway combo: let the portal decide. Stored as
 * an empty vpn.data value, so existing profiles keep behaving the same. */
#define GATEWAY_AUTO_LABEL "First proposed by portal (automatic)"

/* Browser values understood by the service (see resolve_browser()) */
static const char *browser_values[] = {
    "edge", "firefox", "chrome", "chromium", "default", NULL
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

static void
check_toggled_cb (GtkCheckButton *check, gpointer user_data)
{
    g_signal_emit_by_name (user_data, "changed");
}

static void
combo_changed_cb (GtkComboBox *combo, gpointer user_data)
{
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

    /* Portal / gateway address */
    label = gtk_label_new ("Portal or gateway address:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->gateway_entry = gtk_entry_new ();
    gtk_widget_set_hexpand (self->gateway_entry, TRUE);
    gtk_widget_set_tooltip_text (self->gateway_entry,
        "Address of the GlobalProtect portal (e.g. vpn.company.com).\n"
        "If your organisation gave you a gateway address instead, also tick "
        "\"Address is a gateway\" below.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "gateway");
        if (value && *value)
            gtk_editable_set_text (GTK_EDITABLE (self->gateway_entry), value);
    }
    g_signal_connect (self->gateway_entry, "changed", G_CALLBACK (entry_changed_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->gateway_entry, 1, row++, 1, 1);

    /* Address is a gateway, not a portal */
    self->as_gateway_check = gtk_check_button_new_with_label ("Address is a gateway (skip the portal)");
    gtk_widget_set_tooltip_text (self->as_gateway_check,
        "Enable when the address above is a gateway rather than a portal.\n"
        "Without this the portal workflow is tried first, which can make you "
        "authenticate twice.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "as-gateway");
        gtk_check_button_set_active (GTK_CHECK_BUTTON (self->as_gateway_check),
                                    g_strcmp0 (value, "true") == 0);
    }
    g_signal_connect (self->as_gateway_check, "toggled", G_CALLBACK (check_toggled_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->as_gateway_check, 1, row++, 1, 1);

    /* Preferred gateway */
    label = gtk_label_new ("Preferred gateway:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->preferred_gateway_combo = gtk_combo_box_text_new_with_entry ();
    gtk_widget_set_hexpand (self->preferred_gateway_combo, TRUE);
    gtk_widget_set_tooltip_text (self->preferred_gateway_combo,
        "Which gateway to connect to. The list is filled in after the first "
        "successful connection.\n"
        "If the chosen gateway is not offered by the portal any more, the first "
        "proposal is used instead.");
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->preferred_gateway_combo), GATEWAY_AUTO_LABEL);

    if (s_vpn) {
        const char *cached = nm_setting_vpn_get_data_item (s_vpn, "gateway-list");
        if (cached && *cached) {
            gchar **entries = g_strsplit (cached, ";", -1);
            for (int i = 0; entries[i] != NULL; i++) {
                gchar *entry = g_strstrip (entries[i]);
                if (*entry)
                    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->preferred_gateway_combo), entry);
            }
            g_strfreev (entries);
        }

        value = nm_setting_vpn_get_data_item (s_vpn, "preferred-gateway");
        if (value && *value) {
            GtkWidget *entry = gtk_combo_box_get_child (GTK_COMBO_BOX (self->preferred_gateway_combo));
            if (entry && GTK_IS_EDITABLE (entry))
                gtk_editable_set_text (GTK_EDITABLE (entry), value);
        } else {
            gtk_combo_box_set_active (GTK_COMBO_BOX (self->preferred_gateway_combo), 0);
        }
    } else {
        gtk_combo_box_set_active (GTK_COMBO_BOX (self->preferred_gateway_combo), 0);
    }
    g_signal_connect (self->preferred_gateway_combo, "changed", G_CALLBACK (combo_changed_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->preferred_gateway_combo, 1, row++, 1, 1);

    /* Authentication mode */
    label = gtk_label_new ("Authentication:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->auth_mode_combo = gtk_combo_box_text_new ();
    gtk_widget_set_hexpand (self->auth_mode_combo, TRUE);
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->auth_mode_combo), "Browser (SAML, passkey, 2FA)");
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->auth_mode_combo), "Username and password (RSA token)");
    gtk_widget_set_tooltip_text (self->auth_mode_combo,
        "Browser: the portal opens a browser window for single sign-on (default).\n"
        "Username and password: the portal asks on the terminal - the credentials "
        "and any token are requested in a dialog instead.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "auth-mode");
        gtk_combo_box_set_active (GTK_COMBO_BOX (self->auth_mode_combo),
                                 g_strcmp0 (value, "credentials") == 0 ? 1 : 0);
    } else {
        gtk_combo_box_set_active (GTK_COMBO_BOX (self->auth_mode_combo), 0);
    }
    g_signal_connect (self->auth_mode_combo, "changed", G_CALLBACK (combo_changed_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->auth_mode_combo, 1, row++, 1, 1);

    /* Username */
    label = gtk_label_new ("Username:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->username_entry = gtk_entry_new ();
    gtk_widget_set_hexpand (self->username_entry, TRUE);
    gtk_widget_set_tooltip_text (self->username_entry,
        "Optional. Used by portals that ask for credentials on the terminal, so "
        "you are not asked for the username every time.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "username");
        if (value && *value)
            gtk_editable_set_text (GTK_EDITABLE (self->username_entry), value);
    }
    g_signal_connect (self->username_entry, "changed", G_CALLBACK (entry_changed_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->username_entry, 1, row++, 1, 1);

    /* Browser field */
    label = gtk_label_new ("Browser:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->browser_combo = gtk_combo_box_text_new_with_entry ();
    gtk_widget_set_hexpand (self->browser_combo, TRUE);
    for (int i = 0; browser_values[i] != NULL; i++)
        gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->browser_combo), browser_values[i]);
    gtk_combo_box_text_append_text (GTK_COMBO_BOX_TEXT (self->browser_combo), "Custom...");
    gtk_widget_set_tooltip_text (self->browser_combo,
        "Browser used for SAML/2FA authentication. \"edge\" is the best tested "
        "option; \"default\" uses your desktop's default browser.\n"
        "Select 'Custom...' to give the full path to your own executable.");

    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "browser");
        if (value && *value) {
            gboolean found = FALSE;
            for (int i = 0; browser_values[i] != NULL; i++) {
                if (g_strcmp0 (value, browser_values[i]) == 0) {
                    gtk_combo_box_set_active (GTK_COMBO_BOX (self->browser_combo), i);
                    found = TRUE;
                    break;
                }
            }
            if (!found) {
                /* An older profile with a full path - keep it as typed */
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

    /* HIP checkbox */
    label = gtk_label_new ("HIP:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (grid), label, 0, row, 1, 1);

    self->hip_check = gtk_check_button_new_with_label ("Enable Host Integrity Protection (HIP)");
    gtk_widget_set_tooltip_text (self->hip_check,
        "Enable HIP (Host Integrity Protection) to send host information to the VPN gateway.\n"
        "This is required by some organizations for security compliance.");
    /* Default: enabled */
    gboolean hip_enabled = TRUE;
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "hip");
        if (value && *value)
            hip_enabled = (g_strcmp0(value, "true") == 0);
    }
    gtk_check_button_set_active (GTK_CHECK_BUTTON (self->hip_check), hip_enabled);
    g_signal_connect (self->hip_check, "toggled", G_CALLBACK (check_toggled_cb), self);
    gtk_grid_attach (GTK_GRID (grid), self->hip_check, 1, row++, 1, 1);

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

    /* Save "address is a gateway" */
    if (gtk_check_button_get_active (GTK_CHECK_BUTTON (self->as_gateway_check)))
        nm_setting_vpn_add_data_item (s_vpn, "as-gateway", "true");
    else
        nm_setting_vpn_remove_data_item (s_vpn, "as-gateway");

    /* Save preferred gateway (the automatic entry stores nothing) */
    str = gtk_combo_box_text_get_active_text (GTK_COMBO_BOX_TEXT (self->preferred_gateway_combo));
    if (str && *str && g_strcmp0 (str, GATEWAY_AUTO_LABEL) != 0)
        nm_setting_vpn_add_data_item (s_vpn, "preferred-gateway", str);
    else
        nm_setting_vpn_remove_data_item (s_vpn, "preferred-gateway");
    g_free ((char *) str);

    /* Save authentication mode (SAML is the default, stores nothing) */
    if (gtk_combo_box_get_active (GTK_COMBO_BOX (self->auth_mode_combo)) == 1)
        nm_setting_vpn_add_data_item (s_vpn, "auth-mode", "credentials");
    else
        nm_setting_vpn_remove_data_item (s_vpn, "auth-mode");

    /* Save username */
    str = gtk_editable_get_text (GTK_EDITABLE (self->username_entry));
    if (str && *str)
        nm_setting_vpn_add_data_item (s_vpn, "username", str);
    else
        nm_setting_vpn_remove_data_item (s_vpn, "username");

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

    /* Save HIP */
    if (gtk_check_button_get_active (GTK_CHECK_BUTTON (self->hip_check)))
        nm_setting_vpn_add_data_item (s_vpn, "hip", "true");
    else
        nm_setting_vpn_add_data_item (s_vpn, "hip", "false");

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

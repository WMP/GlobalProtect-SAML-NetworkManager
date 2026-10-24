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
    GtkCheckButton *as_gateway_check;
    GtkComboBoxText *preferred_gateway_combo;
    GtkComboBoxText *auth_mode_combo;
    GtkEntry *username_entry;
    GtkComboBoxText *browser_combo;
    GtkSpinButton *timeout_spin;
    gboolean timeout_follows_auth_mode;
    GtkEntry *dns_entry;
    GtkCheckButton *hip_check;
    GtkWidget *hip_script_label;
    GtkEntry *hip_script_entry;
    GtkComboBoxText *fix_openssl_combo;
};

/* First entry of the preferred-gateway combo: let the portal decide. Stored as
 * an empty vpn.data value, so existing profiles keep behaving the same. */
#define GATEWAY_AUTO_LABEL "First proposed by portal (automatic)"

/* NetworkManager stops a VPN activation after vpn.timeout seconds (0 = its
 * default, usually 60 s), which is too short for a SAML login with MFA. A new
 * connection with browser authentication starts with this value. While the
 * stored value is 0 and the user has not changed the field, choosing browser
 * authentication sets it (issue #46). It is longer than the limit of the
 * browser login itself (GP_AUTH_TIMEOUT, 300 s, scripts/browser-wrapper.sh)
 * plus the time to connect after the login, so that limit and its clearer
 * error come first. */
#define SAML_TIMEOUT_DEFAULT 360

/* Browser values understood by the service (see resolve_browser()) */
static const char *browser_values[] = {
    "edge", "firefox", "chrome", "chromium", "default", NULL
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
hip_toggled_cb (GtkWidget *widget, gpointer user_data)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (user_data);
    gboolean active = gtk_toggle_button_get_active (GTK_TOGGLE_BUTTON (widget));

    /* The HIP script only matters while HIP is on */
    gtk_widget_set_sensitive (self->hip_script_label, active);
    gtk_widget_set_sensitive (GTK_WIDGET (self->hip_script_entry), active);
    g_signal_emit_by_name (G_OBJECT (self), "changed");
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

static void
timeout_changed_cb (gpointer widget, gpointer user_data)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (user_data);

    /* The user set a value (+/-, typing): keep it whatever the authentication
     * mode. Connected only to the text "changed" of the spin button: every
     * user change (typing, +/-, wheel, keys) changes the text, so there is one
     * signal per change. GNOME Settings enables Apply only on the editor's
     * "changed". auth_mode_changed_cb blocks this handler for its own changes. */
    self->timeout_follows_auth_mode = FALSE;
    g_signal_emit_by_name (G_OBJECT (self), "changed");
}

/* The value the user sees. Typed text is committed to the adjustment only on
 * focus-out or Enter, so read the text. Parse it as GTK does on commit (a
 * numeric spin button accepts a sign at position 0): g_strtod, then clamp to
 * the range of the adjustment ("+90" is 90, "-5" is 0, a very long number is
 * the upper limit, empty text is 0). This never changes the widget. */
static guint
timeout_from_spin (GtkSpinButton *spin)
{
    const char *text = gtk_entry_get_text (GTK_ENTRY (spin));
    GtkAdjustment *adj = gtk_spin_button_get_adjustment (spin);
    double v = g_ascii_strtod (text ? text : "", NULL);

    v = CLAMP (v, gtk_adjustment_get_lower (adj), gtk_adjustment_get_upper (adj));
    return (guint) v;
}

static void
auth_mode_changed_cb (GtkComboBox *combo, gpointer user_data)
{
    NMGpclientEditor *self = NM_GPCLIENT_EDITOR (user_data);

    if (self->timeout_follows_auth_mode && self->timeout_spin) {
        g_signal_handlers_block_by_func (self->timeout_spin, timeout_changed_cb, self);
        gtk_spin_button_set_value (self->timeout_spin,
                                   gtk_combo_box_get_active (combo) == 0 ? SAML_TIMEOUT_DEFAULT : 0);
        g_signal_handlers_unblock_by_func (self->timeout_spin, timeout_changed_cb, self);
    }
    g_signal_emit_by_name (G_OBJECT (self), "changed");
}

static GtkWidget *
build_ui (NMGpclientEditor *self)
{
    NMSettingVpn *s_vpn;
    GtkWidget *widget, *label, *timeout_box, *timeout_hint;
    const char *value;
    gboolean is_new;
    guint stored;
    int auth_index;
    int row = 0;

    s_vpn = nm_connection_get_setting_vpn (self->connection);
    /* A profile that exists has at least the gateway in vpn.data */
    is_new = !s_vpn || nm_setting_vpn_get_num_data_items (s_vpn) == 0;
    stored = s_vpn ? nm_setting_vpn_get_timeout (s_vpn) : 0;

    widget = gtk_grid_new ();
    gtk_grid_set_column_spacing (GTK_GRID (widget), 12);
    gtk_grid_set_row_spacing (GTK_GRID (widget), 6);

    /* Portal / gateway address */
    label = gtk_label_new ("Portal or gateway address:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->gateway_entry = GTK_ENTRY (gtk_entry_new ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->gateway_entry), TRUE);
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->gateway_entry),
        "Address of the GlobalProtect portal (e.g. vpn.company.com).\n"
        "If your organisation gave you a gateway address instead, also tick "
        "\"Address is a gateway\" below.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "gateway");
        if (value)
            gtk_entry_set_text (self->gateway_entry, value);
    }
    g_signal_connect (G_OBJECT (self->gateway_entry), "changed", G_CALLBACK (stuff_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->gateway_entry), 1, row++, 1, 1);

    /* Address is a gateway, not a portal */
    self->as_gateway_check = GTK_CHECK_BUTTON (
        gtk_check_button_new_with_label ("Address is a gateway (skip the portal)"));
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->as_gateway_check),
        "Enable when the address above is a gateway rather than a portal.\n"
        "Without this the portal workflow is tried first, which can make you "
        "authenticate twice.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "as-gateway");
        gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (self->as_gateway_check),
                                      g_strcmp0 (value, "true") == 0);
    }
    g_signal_connect (G_OBJECT (self->as_gateway_check), "toggled", G_CALLBACK (stuff_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->as_gateway_check), 1, row++, 1, 1);

    /* Preferred gateway */
    label = gtk_label_new ("Preferred gateway:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->preferred_gateway_combo = GTK_COMBO_BOX_TEXT (gtk_combo_box_text_new_with_entry ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->preferred_gateway_combo), TRUE);
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->preferred_gateway_combo),
        "Which gateway to connect to. The list is filled in after the first "
        "successful connection.\n"
        "If the chosen gateway is not offered by the portal any more, the first "
        "proposal is used instead.");
    gtk_combo_box_text_append_text (self->preferred_gateway_combo, GATEWAY_AUTO_LABEL);

    if (s_vpn) {
        const char *cached = nm_setting_vpn_get_data_item (s_vpn, "gateway-list");
        if (cached && *cached) {
            gchar **entries = g_strsplit (cached, ";", -1);
            for (int i = 0; entries[i] != NULL; i++) {
                gchar *entry = g_strstrip (entries[i]);
                if (*entry)
                    gtk_combo_box_text_append_text (self->preferred_gateway_combo, entry);
            }
            g_strfreev (entries);
        }

        value = nm_setting_vpn_get_data_item (s_vpn, "preferred-gateway");
        if (value && *value) {
            GtkWidget *entry = gtk_bin_get_child (GTK_BIN (self->preferred_gateway_combo));
            gtk_entry_set_text (GTK_ENTRY (entry), value);
        } else {
            gtk_combo_box_set_active (GTK_COMBO_BOX (self->preferred_gateway_combo), 0);
        }
    } else {
        gtk_combo_box_set_active (GTK_COMBO_BOX (self->preferred_gateway_combo), 0);
    }
    g_signal_connect (G_OBJECT (self->preferred_gateway_combo), "changed", G_CALLBACK (stuff_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->preferred_gateway_combo), 1, row++, 1, 1);

    /* Authentication mode */
    label = gtk_label_new ("Authentication:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->auth_mode_combo = GTK_COMBO_BOX_TEXT (gtk_combo_box_text_new ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->auth_mode_combo), TRUE);
    gtk_combo_box_text_append_text (self->auth_mode_combo, "Browser (SAML, passkey, 2FA)");
    gtk_combo_box_text_append_text (self->auth_mode_combo, "Username and password (RSA token)");
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->auth_mode_combo),
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
    auth_index = gtk_combo_box_get_active (GTK_COMBO_BOX (self->auth_mode_combo));
    g_signal_connect (G_OBJECT (self->auth_mode_combo), "changed", G_CALLBACK (auth_mode_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->auth_mode_combo), 1, row++, 1, 1);

    /* Username */
    label = gtk_label_new ("Username:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->username_entry = GTK_ENTRY (gtk_entry_new ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->username_entry), TRUE);
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->username_entry),
        "Optional. Used by portals that ask for credentials on the terminal, so "
        "you are not asked for the username every time.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "username");
        if (value)
            gtk_entry_set_text (self->username_entry, value);
    }
    g_signal_connect (G_OBJECT (self->username_entry), "changed", G_CALLBACK (stuff_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->username_entry), 1, row++, 1, 1);

    /* Browser */
    label = gtk_label_new ("Browser:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->browser_combo = GTK_COMBO_BOX_TEXT (gtk_combo_box_text_new_with_entry ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->browser_combo), TRUE);

    for (int i = 0; browser_values[i] != NULL; i++)
        gtk_combo_box_text_append_text (self->browser_combo, browser_values[i]);

    gtk_combo_box_text_append_text (self->browser_combo, "Custom...");
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->browser_combo),
        "Browser used for SAML/2FA authentication. \"edge\" is the best tested "
        "option; \"default\" uses your desktop's default browser.\n"
        "Select 'Custom...' to give the full path to your own executable.");

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
                /* An older profile with a full path - keep it as typed */
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

    /* Connect timeout (NetworkManager's vpn.timeout, not a vpn.data item) */
    label = gtk_label_new ("Connect timeout:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    timeout_box = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 6);
    self->timeout_spin = GTK_SPIN_BUTTON (gtk_spin_button_new_with_range (0, G_MAXUINT32, 10));
    gtk_spin_button_set_digits (self->timeout_spin, 0);
    gtk_spin_button_set_numeric (self->timeout_spin, TRUE);
    gtk_widget_set_hexpand (GTK_WIDGET (self->timeout_spin), TRUE);
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->timeout_spin),
        "How long NetworkManager waits for the VPN to connect, including the "
        "login in the browser (vpn.timeout).\n"
        "0 uses the NetworkManager default, usually 60 s - often too short for a "
        "SAML login with MFA. A new connection with browser authentication starts "
        "with 360 s. While a connection has no timeout of its own (0) and you have "
        "not changed this field, choosing browser authentication sets 360 s.");
    /* A new profile starts with 360 s for browser authentication. A stored 0
     * (new or existing) follows the authentication mode until the user sets a
     * value; an existing profile still opens with its 0. */
    if (is_new && stored == 0)
        gtk_spin_button_set_value (self->timeout_spin,
                                   auth_index == 0 ? SAML_TIMEOUT_DEFAULT : 0);
    else
        gtk_spin_button_set_value (self->timeout_spin, (double) stored);
    self->timeout_follows_auth_mode = (stored == 0);
    /* Connect after the initial value is set */
    g_signal_connect (G_OBJECT (self->timeout_spin), "changed", G_CALLBACK (timeout_changed_cb), self);
    gtk_box_pack_start (GTK_BOX (timeout_box), GTK_WIDGET (self->timeout_spin), TRUE, TRUE, 0);
    timeout_hint = gtk_label_new ("seconds (0 = NetworkManager default)");
    gtk_box_pack_start (GTK_BOX (timeout_box), timeout_hint, FALSE, FALSE, 0);
    gtk_grid_attach (GTK_GRID (widget), timeout_box, 1, row++, 1, 1);

    /* Legacy TLS renegotiation workaround */
    label = gtk_label_new ("Legacy TLS renegotiation:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->fix_openssl_combo = GTK_COMBO_BOX_TEXT (gtk_combo_box_text_new ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->fix_openssl_combo), TRUE);
    gtk_combo_box_text_append_text (self->fix_openssl_combo, "Automatic (enable when the portal needs it)");
    gtk_combo_box_text_append_text (self->fix_openssl_combo, "Always on");
    gtk_combo_box_text_append_text (self->fix_openssl_combo, "Never");
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->fix_openssl_combo),
        "Portals with an old TLS stack need renegotiation that OpenSSL 3 refuses "
        "by default.\n"
        "Automatic: retry once with the workaround when the portal asks for it, "
        "and switch this setting to \"Always on\" afterwards, so the failed first "
        "attempt does not repeat.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "fix-openssl");
        if (g_strcmp0 (value, "true") == 0)
            gtk_combo_box_set_active (GTK_COMBO_BOX (self->fix_openssl_combo), 1);
        else if (g_strcmp0 (value, "false") == 0)
            gtk_combo_box_set_active (GTK_COMBO_BOX (self->fix_openssl_combo), 2);
        else
            gtk_combo_box_set_active (GTK_COMBO_BOX (self->fix_openssl_combo), 0);
    } else {
        gtk_combo_box_set_active (GTK_COMBO_BOX (self->fix_openssl_combo), 0);
    }
    g_signal_connect (G_OBJECT (self->fix_openssl_combo), "changed", G_CALLBACK (stuff_changed_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->fix_openssl_combo), 1, row++, 1, 1);

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

    /* HIP */
    label = gtk_label_new ("HIP:");
    gtk_widget_set_halign (label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), label, 0, row, 1, 1);

    self->hip_check = GTK_CHECK_BUTTON (gtk_check_button_new_with_label ("Enable Host Integrity Protection (HIP)"));
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->hip_check),
        "Enable HIP (Host Integrity Protection) to send host information to the VPN gateway.\n"
        "This is required by some organizations for security compliance.");
    /* Default: enabled */
    gboolean hip_enabled = TRUE;
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "hip");
        if (value && *value)
            hip_enabled = (g_strcmp0(value, "true") == 0);
    }
    gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (self->hip_check), hip_enabled);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->hip_check), 1, row++, 1, 1);

    /* HIP script */
    self->hip_script_label = gtk_label_new ("HIP script:");
    gtk_widget_set_halign (self->hip_script_label, GTK_ALIGN_START);
    gtk_grid_attach (GTK_GRID (widget), self->hip_script_label, 0, row, 1, 1);

    self->hip_script_entry = GTK_ENTRY (gtk_entry_new ());
    gtk_widget_set_hexpand (GTK_WIDGET (self->hip_script_entry), TRUE);
    gtk_entry_set_placeholder_text (self->hip_script_entry, "Empty: built-in HIP report");
    gtk_widget_set_tooltip_text (GTK_WIDGET (self->hip_script_entry),
        "Optional absolute path to a custom HIP executable, passed to gpclient as --hip=<path>.\n"
        "The service runs it as root: it must be a root-owned file that is not writable by group or others, "
        "in root-owned directories that are not writable by group or others. Otherwise the connection fails.\n"
        "Ignored when HIP is off.");
    if (s_vpn) {
        value = nm_setting_vpn_get_data_item (s_vpn, "hip-script");
        if (value && *value)
            gtk_entry_set_text (self->hip_script_entry, value);
    }
    gtk_widget_set_sensitive (self->hip_script_label, hip_enabled);
    gtk_widget_set_sensitive (GTK_WIDGET (self->hip_script_entry), hip_enabled);
    g_signal_connect (G_OBJECT (self->hip_script_entry), "changed", G_CALLBACK (stuff_changed_cb), self);
    g_signal_connect (G_OBJECT (self->hip_check), "toggled", G_CALLBACK (hip_toggled_cb), self);
    gtk_grid_attach (GTK_GRID (widget), GTK_WIDGET (self->hip_script_entry), 1, row++, 1, 1);

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

    if (self->as_gateway_check) {
        if (gtk_toggle_button_get_active (GTK_TOGGLE_BUTTON (self->as_gateway_check)))
            nm_setting_vpn_add_data_item (s_vpn, "as-gateway", "true");
        else
            nm_setting_vpn_remove_data_item (s_vpn, "as-gateway");
    }

    if (self->preferred_gateway_combo) {
        str = gtk_combo_box_text_get_active_text (self->preferred_gateway_combo);
        /* The automatic entry means "let the portal decide" - store nothing */
        if (str && strlen (str) && g_strcmp0 (str, GATEWAY_AUTO_LABEL) != 0)
            nm_setting_vpn_add_data_item (s_vpn, "preferred-gateway", str);
        else
            nm_setting_vpn_remove_data_item (s_vpn, "preferred-gateway");
        g_free ((char *) str);
    }

    if (self->auth_mode_combo) {
        if (gtk_combo_box_get_active (GTK_COMBO_BOX (self->auth_mode_combo)) == 1)
            nm_setting_vpn_add_data_item (s_vpn, "auth-mode", "credentials");
        else
            nm_setting_vpn_remove_data_item (s_vpn, "auth-mode");
    }

    if (self->username_entry) {
        str = gtk_entry_get_text (self->username_entry);
        if (str && strlen (str))
            nm_setting_vpn_add_data_item (s_vpn, "username", str);
        else
            nm_setting_vpn_remove_data_item (s_vpn, "username");
    }

    if (self->browser_combo) {
        str = gtk_combo_box_text_get_active_text (self->browser_combo);
        if (str && strlen (str) && g_strcmp0(str, "Custom...") != 0)
            nm_setting_vpn_add_data_item (s_vpn, "browser", str);
        g_free ((char *)str);
    }

    if (self->timeout_spin) {
        /* NetworkManager's own vpn.timeout; read what the user sees */
        g_object_set (s_vpn, NM_SETTING_VPN_TIMEOUT,
                      timeout_from_spin (self->timeout_spin),
                      NULL);
    }

    if (self->dns_entry) {
        str = gtk_entry_get_text (self->dns_entry);
        if (str && strlen (str))
            nm_setting_vpn_add_data_item (s_vpn, "dns", str);
    }

    if (self->fix_openssl_combo) {
        /* Automatic stores nothing - the service reads a missing key as "auto"
         * and switches the profile to "true" once a portal needs it. */
        switch (gtk_combo_box_get_active (GTK_COMBO_BOX (self->fix_openssl_combo))) {
        case 1:
            nm_setting_vpn_add_data_item (s_vpn, "fix-openssl", "true");
            break;
        case 2:
            nm_setting_vpn_add_data_item (s_vpn, "fix-openssl", "false");
            break;
        default:
            nm_setting_vpn_remove_data_item (s_vpn, "fix-openssl");
            break;
        }
    }

    if (self->hip_check) {
        if (gtk_toggle_button_get_active (GTK_TOGGLE_BUTTON (self->hip_check)))
            nm_setting_vpn_add_data_item (s_vpn, "hip", "true");
        else
            nm_setting_vpn_add_data_item (s_vpn, "hip", "false");
    }

    if (self->hip_script_entry) {
        /* Saved even when HIP is off: the service ignores it then */
        gchar *script = g_strstrip (g_strdup (gtk_entry_get_text (self->hip_script_entry)));

        if (*script)
            nm_setting_vpn_add_data_item (s_vpn, "hip-script", script);
        else
            nm_setting_vpn_remove_data_item (s_vpn, "hip-script");
        g_free (script);
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
    self->as_gateway_check = NULL;
    self->preferred_gateway_combo = NULL;
    self->auth_mode_combo = NULL;
    self->username_entry = NULL;
    self->browser_combo = NULL;
    self->timeout_spin = NULL;
    self->timeout_follows_auth_mode = FALSE;
    self->dns_entry = NULL;
    self->hip_check = NULL;
    self->hip_script_label = NULL;
    self->hip_script_entry = NULL;
    self->fix_openssl_combo = NULL;
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

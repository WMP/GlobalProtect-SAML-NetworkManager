/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

#include "gpclientui.h"
#include "gpclientwidget.h"

#include <KPluginFactory>

GpclientUiPlugin::GpclientUiPlugin(QObject *parent, const QVariantList &)
    : VpnUiPlugin(parent)
{
}

GpclientUiPlugin::~GpclientUiPlugin()
{
}

SettingWidget *GpclientUiPlugin::widget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent)
{
    return new GpclientWidget(setting, parent);
}

SettingWidget *GpclientUiPlugin::askUser(const NetworkManager::VpnSetting::Ptr &setting, const QStringList &hints, QWidget *parent)
{
    Q_UNUSED(hints);
    return new GpclientWidget(setting, parent);
}

QString GpclientUiPlugin::suggestedFileName(const NetworkManager::ConnectionSettings::Ptr &connection) const
{
    Q_UNUSED(connection);
    return QString();
}

#if !defined(NETWORKMANAGERQT_VERSION) || NETWORKMANAGERQT_VERSION < QT_VERSION_CHECK(5, 102, 0)
// Required for KF5 < 5.102 (Ubuntu 22.04)
QStringList GpclientUiPlugin::supportedFileExtensions() const
{
    return QStringList();
}

VpnUiPlugin::ImportResult GpclientUiPlugin::importConnectionSettings(const QString &fileName)
{
    Q_UNUSED(fileName);
    return VpnUiPlugin::ImportResult();
}

VpnUiPlugin::ExportResult GpclientUiPlugin::exportConnectionSettings(const NetworkManager::ConnectionSettings::Ptr &connection, const QString &fileName)
{
    Q_UNUSED(connection);
    Q_UNUSED(fileName);
    return VpnUiPlugin::ExportResult();
}
#endif

K_PLUGIN_CLASS_WITH_JSON(GpclientUiPlugin, "plasmanetworkmanagement_gpclientui.json")

#include "gpclientui.moc"

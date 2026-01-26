/*
    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
    SPDX-FileCopyrightText: 2025 GlobalProtect VPN Plugin
*/

#ifndef PLASMA_NM_GPCLIENT_UI_H
#define PLASMA_NM_GPCLIENT_UI_H

#include <NetworkManagerQt/VpnSetting>
#include <vpnuiplugin.h>

class Q_DECL_EXPORT GpclientUiPlugin : public VpnUiPlugin
{
    Q_OBJECT
public:
    explicit GpclientUiPlugin(QObject *parent = nullptr, const QVariantList & = QVariantList());
    ~GpclientUiPlugin() override;
    
    SettingWidget *widget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent = nullptr) override;
    SettingWidget *askUser(const NetworkManager::VpnSetting::Ptr &setting, const QStringList &hints, QWidget *parent = nullptr) override;
    QString suggestedFileName(const NetworkManager::ConnectionSettings::Ptr &connection) const override;
    
#if !defined(NETWORKMANAGERQT_VERSION) || NETWORKMANAGERQT_VERSION < QT_VERSION_CHECK(5, 102, 0)
    // Required for KF5 < 5.102 (Ubuntu 22.04)
    QStringList supportedFileExtensions() const override;
    VpnUiPlugin::ImportResult importConnectionSettings(const QString &fileName) override;
    VpnUiPlugin::ExportResult exportConnectionSettings(const NetworkManager::ConnectionSettings::Ptr &connection, const QString &fileName) override;
#endif
};

#endif // PLASMA_NM_GPCLIENT_UI_H
